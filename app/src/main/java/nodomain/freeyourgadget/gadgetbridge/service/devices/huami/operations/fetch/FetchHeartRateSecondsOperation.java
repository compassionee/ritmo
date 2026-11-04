/*  Copyright (C) 2026 compassionee

    This file is part of Gadgetbridge.

    Gadgetbridge is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as published
    by the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Gadgetbridge is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>. */
package nodomain.freeyourgadget.gadgetbridge.service.devices.huami.operations.fetch;

import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.annotation.StringRes;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.GregorianCalendar;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import nodomain.freeyourgadget.gadgetbridge.GBApplication;
import nodomain.freeyourgadget.gadgetbridge.R;
import nodomain.freeyourgadget.gadgetbridge.database.DBHandler;
import nodomain.freeyourgadget.gadgetbridge.database.DBHelper;
import nodomain.freeyourgadget.gadgetbridge.devices.huami.HuamiCoordinator;
import nodomain.freeyourgadget.gadgetbridge.entities.DaoSession;
import nodomain.freeyourgadget.gadgetbridge.entities.GenericHeartRateSample;
import nodomain.freeyourgadget.gadgetbridge.service.btle.BLETypeConversions;
import nodomain.freeyourgadget.gadgetbridge.service.devices.huami.HuamiFetcher;
import nodomain.freeyourgadget.gadgetbridge.util.GB;

/**
 * Fetches the heart rate of every second (type 0x57, "SEC_HR_V2" in the Zepp app): a sequence of packets
 * {@code u32 start (s) · u16 slots · slots x u8 bpm}, sample i at start + i, as the Zepp app's parser reads them.
 * Confirmed on two real captures of a Helio Strap: slots always 300; trailing 0xFF are padding; the band first sends
 * part of a packet, then the whole packet again with the same start, so for a start the longest packet wins.
 * Saved in the generic heart rate table, one row per second.
 * <p>
 * The band answers with the packets that start at or after the requested time: the next fetch starts at the last
 * packet's start, so a packet still being filled is read again whole (rows are replaced, never doubled).
 */
public class FetchHeartRateSecondsOperation extends AbstractRepeatingFetchOperation {
    private static final Logger LOG = LoggerFactory.getLogger(FetchHeartRateSecondsOperation.class);

    static final int HEADER_LENGTH = 6;
    static final int PADDING = 0xff;
    /** Values outside this range are not a heart rate (0 at the end of some packets: no reading). */
    static final int MIN_BPM = 25;
    static final int MAX_BPM = 230;
    /** The first fetch goes back a week (about 4 MB): per-second data is large, and the band keeps it for days. */
    static final long FIRST_FETCH_MS = 7L * 24 * 60 * 60 * 1000;

    public FetchHeartRateSecondsOperation(final HuamiFetcher fetcher) {
        super(fetcher, HuamiFetchDataType.SEC_HR_V2);
    }

    @StringRes
    @Override
    public int taskDescription() {
        return R.string.busy_task_fetch_hr_data;
    }

    /** What a fetch holds: the samples (one per second, in order) and the start of its last packet (ms). */
    static final class Parsed {
        final List<GenericHeartRateSample> samples = new ArrayList<>();
        long lastPacketStartMs = -1;
    }

    /** The packets of a fetch, or null when a packet is cut short. */
    @Nullable
    static Parsed parse(@NonNull final byte[] bytes) {
        final ByteBuffer buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        // start (s) -> valid samples of the longest packet with that start
        final TreeMap<Long, byte[]> packets = new TreeMap<>();
        while (buf.remaining() >= HEADER_LENGTH) {
            final long start = buf.getInt() & 0xffffffffL;
            final int slots = buf.getShort() & 0xffff;
            if (slots > buf.remaining()) {
                LOG.error("Heart rate packet at {} cut short: {} slots, {} bytes left", start, slots, buf.remaining());
                return null;
            }
            final byte[] body = new byte[slots];
            buf.get(body);
            int valid = slots;
            while (valid > 0 && (body[valid - 1] & 0xff) == PADDING) {
                valid--;
            }
            final byte[] previous = packets.get(start);
            if (previous == null || valid > previous.length) {
                final byte[] kept = new byte[valid];
                System.arraycopy(body, 0, kept, 0, valid);
                packets.put(start, kept);
            }
        }
        if (buf.hasRemaining()) {
            LOG.error("Heart rate data: {} bytes left after the last packet", buf.remaining());
            return null;
        }
        final Parsed out = new Parsed();
        long lastSecond = Long.MIN_VALUE;
        for (final Map.Entry<Long, byte[]> p : packets.entrySet()) {
            out.lastPacketStartMs = p.getKey() * 1000L;
            final byte[] body = p.getValue();
            for (int i = 0; i < body.length; i++) {
                final long second = p.getKey() + i;
                final int bpm = body[i] & 0xff;
                if (bpm < MIN_BPM || bpm > MAX_BPM || second <= lastSecond) {
                    continue;  // not a reading, or a second already given by the previous packet
                }
                lastSecond = second;
                final GenericHeartRateSample sample = new GenericHeartRateSample();
                sample.setTimestamp(second * 1000L);
                sample.setHeartRate(bpm);
                out.samples.add(sample);
            }
        }
        return out;
    }

    @Override
    protected boolean handleActivityData(final GregorianCalendar timestamp, final byte[] bytes) {
        final Parsed parsed = parse(bytes);
        if (parsed == null) {
            return false;
        }
        if (parsed.lastPacketStartMs > 0) {
            timestamp.setTimeInMillis(parsed.lastPacketStartMs);
        }
        return persistSamples(parsed.samples);
    }

    /** The next fetch starts at the last packet's start: it may still be filling up. */
    @Override
    protected void advanceLastSyncTimestamp(final GregorianCalendar timestamp) {
    }

    @Override
    protected GregorianCalendar getLastSuccessfulSyncTime() {
        final long saved = GBApplication.getDeviceSpecificSharedPrefs(getDevice().getAddress()).getLong(getLastSyncTimeKey(), 0);
        if (saved != 0) {
            return super.getLastSuccessfulSyncTime();
        }
        final GregorianCalendar since = BLETypeConversions.createCalendar();
        since.setTimeInMillis(System.currentTimeMillis() - FIRST_FETCH_MS);
        return since;
    }

    protected boolean persistSamples(final List<GenericHeartRateSample> samples) {
        try (DBHandler handler = GBApplication.acquireDB()) {
            final HuamiCoordinator coordinator = (HuamiCoordinator) getDevice().getDeviceCoordinator();
            coordinator.getHeartRateSecondsSampleProvider(getDevice(), handler.getDaoSession()).persistSamples(samples, getContext());
            pruneOldSeconds(handler.getDaoSession());
        } catch (final Exception e) {
            GB.toast(getContext(), "Error saving heart rate samples", Toast.LENGTH_LONG, GB.ERROR, e);
            return false;
        }
        return true;
    }

    /** The seconds older than the period kept (Helio build, HelioSecondsRetention): a failure never fails the fetch. */
    private void pruneOldSeconds(final DaoSession session) {
        try {
            HelioSecondsRetention.prune(getContext(), session, DBHelper.getDevice(getDevice(), session).getId(),
                    System.currentTimeMillis());
        } catch (final Exception e) {
            LOG.warn("Could not delete old heart rate seconds", e);
        }
    }

    @Override
    protected String getLastSyncTimeKey() {
        return "lastHeartRateSecondsTimeMillis";
    }
}
