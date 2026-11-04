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
import java.util.Calendar;
import java.util.GregorianCalendar;
import java.util.List;

import nodomain.freeyourgadget.gadgetbridge.GBApplication;
import nodomain.freeyourgadget.gadgetbridge.R;
import nodomain.freeyourgadget.gadgetbridge.database.DBHandler;
import nodomain.freeyourgadget.gadgetbridge.devices.huami.HuamiCoordinator;
import nodomain.freeyourgadget.gadgetbridge.entities.GenericHrvSdnnSample;
import nodomain.freeyourgadget.gadgetbridge.service.devices.huami.HuamiFetcher;
import nodomain.freeyourgadget.gadgetbridge.util.GB;

/**
 * Fetches the SDNN records (type 0x4e, "SHRV_SDNN" in the Zepp app), 9 bytes each, little endian:
 * {@code u32 timestamp (s) · s8 time zone (quarters of an hour) · u32 SDNN (ms x 100)}. Format from the Zepp app's
 * parser, confirmed on two real captures of a Helio Strap. Saved in milliseconds, like HRV values.
 */
public class FetchHrvSdnnOperation extends AbstractRepeatingFetchOperation {
    private static final Logger LOG = LoggerFactory.getLogger(FetchHrvSdnnOperation.class);

    static final int RECORD_LENGTH = 9;
    /** Values outside this range (ms) are not a plausible SDNN and are left out. */
    static final int MIN_MS = 1;
    static final int MAX_MS = 500;

    public FetchHrvSdnnOperation(final HuamiFetcher fetcher) {
        super(fetcher, HuamiFetchDataType.SHRV_SDNN);
    }

    @StringRes
    @Override
    public int taskDescription() {
        return R.string.busy_task_fetch_hrv_data;
    }

    /** The records of a fetch, or null when the length is not a whole number of records. */
    @Nullable
    static List<GenericHrvSdnnSample> parse(@NonNull final byte[] bytes) {
        if (bytes.length % RECORD_LENGTH != 0) {
            return null;
        }
        final ByteBuffer buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        final List<GenericHrvSdnnSample> samples = new ArrayList<>();
        while (buf.remaining() >= RECORD_LENGTH) {
            final long timestampSeconds = buf.getInt() & 0xffffffffL;
            buf.get(); // time zone: the timestamp is already UTC
            final long sdnnHundredths = buf.getInt() & 0xffffffffL;
            final int ms = (int) Math.round(sdnnHundredths / 100.0);
            if (ms < MIN_MS || ms > MAX_MS) {
                LOG.warn("SDNN at {} out of range: {}", timestampSeconds, sdnnHundredths);
                continue;
            }
            final GenericHrvSdnnSample sample = new GenericHrvSdnnSample();
            sample.setTimestamp(timestampSeconds * 1000L);
            sample.setValue(ms);
            samples.add(sample);
        }
        return samples;
    }

    @Override
    protected boolean handleActivityData(final GregorianCalendar timestamp, final byte[] bytes) {
        final List<GenericHrvSdnnSample> samples = parse(bytes);
        if (samples == null) {
            LOG.error("Unexpected length for SDNN data {}, not divisible by {}", bytes.length, RECORD_LENGTH);
            return false;
        }
        for (final GenericHrvSdnnSample s : samples) {
            if (s.getTimestamp() > timestamp.getTimeInMillis()) {
                timestamp.setTimeInMillis(s.getTimestamp());
            }
        }
        return persistSamples(samples);
    }

    /** The next fetch asks from one second after the last record (the band answers with records from then on). */
    @Override
    protected void advanceLastSyncTimestamp(final GregorianCalendar timestamp) {
        timestamp.add(Calendar.SECOND, 1);
    }

    protected boolean persistSamples(final List<GenericHrvSdnnSample> samples) {
        try (DBHandler handler = GBApplication.acquireDB()) {
            final HuamiCoordinator coordinator = (HuamiCoordinator) getDevice().getDeviceCoordinator();
            coordinator.getHrvSdnnSampleProvider(getDevice(), handler.getDaoSession()).persistSamples(samples, getContext());
        } catch (final Exception e) {
            GB.toast(getContext(), "Error saving SDNN samples", Toast.LENGTH_LONG, GB.ERROR, e);
            return false;
        }
        return true;
    }

    @Override
    protected String getLastSyncTimeKey() {
        return "lastHrvSdnnTimeMillis";
    }
}
