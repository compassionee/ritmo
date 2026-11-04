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

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.annotation.StringRes;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Arrays;
import java.util.GregorianCalendar;
import java.util.HashMap;
import java.util.Map;

import nodomain.freeyourgadget.gadgetbridge.BuildConfig;
import nodomain.freeyourgadget.gadgetbridge.R;
import nodomain.freeyourgadget.gadgetbridge.devices.DeviceCoordinator;
import nodomain.freeyourgadget.gadgetbridge.devices.huami.HuamiService;
import nodomain.freeyourgadget.gadgetbridge.devices.huami.zeppos.straps.AmazfitHelioStrapCoordinator;
import nodomain.freeyourgadget.gadgetbridge.service.btle.BLETypeConversions;
import nodomain.freeyourgadget.gadgetbridge.service.devices.huami.HuamiFetcher;
import nodomain.freeyourgadget.gadgetbridge.util.CheckSums;

/**
 * Capture mode of the Helio build (only with -PhelioCapture=true, off until turned on in Ritmo's Profile): after the
 * normal sync, asks the Helio Strap for a fetch type Gadgetbridge does not handle and saves the answer raw in
 * {@link HelioCaptureStore}, without interpreting it. The data stays on the band: the ack is always "keep" (0x09), and
 * nothing is written to Gadgetbridge's database or preferences. A refusal, an empty answer, an invalid one or no answer
 * at all is logged too. With no answer in {@link #ANSWER_TIMEOUT_MS}, the other capture requests of this sync are
 * skipped: the band is never left waiting, and a late answer cannot be taken for another type's.
 */
public class FetchCaptureOperation extends AbstractFetchOperation {
    private static final Logger LOG = LoggerFactory.getLogger(FetchCaptureOperation.class);

    static final long ANSWER_TIMEOUT_MS = 60_000;
    private static final byte KEEP_ON_DEVICE = 0x09;

    /** The capture requests of one sync: once one gets no answer, the others are skipped. */
    public static final class Session {
        boolean aborted;
    }

    /** Time source and timer, replaced in the tests. */
    interface Clock {
        long now();

        void schedule(@NonNull Runnable r, long delayMs);

        void cancel(@NonNull Runnable r);
    }

    private static final Clock SYSTEM_CLOCK = new Clock() {
        private final Handler handler = new Handler(Looper.getMainLooper());

        @Override
        public long now() {
            return System.currentTimeMillis();
        }

        @Override
        public void schedule(@NonNull final Runnable r, final long delayMs) {
            handler.postDelayed(r, delayMs);
        }

        @Override
        public void cancel(@NonNull final Runnable r) {
            handler.removeCallbacks(r);
        }
    };

    private final HelioCaptureStore store;
    private final Session session;
    private final Clock clock;
    private final Runnable timeout = this::onNoAnswer;
    private boolean finished;
    private byte[] startResponse;
    private long since;
    private boolean counterError;

    public FetchCaptureOperation(final HuamiFetcher fetcher, final HuamiFetchDataType type,
                                 final HelioCaptureStore store, final Session session) {
        this(fetcher, type, store, session, SYSTEM_CLOCK);
    }

    FetchCaptureOperation(final HuamiFetcher fetcher, final HuamiFetchDataType type, final HelioCaptureStore store,
                          final Session session, final Clock clock) {
        super(fetcher, type);
        this.store = store;
        this.session = session;
        this.clock = clock;
    }

    /** Whether this sync adds the capture requests: Helio build with the flag, Helio Strap, capture turned on. */
    public static boolean wanted(@NonNull final Context context, @Nullable final DeviceCoordinator coordinator) {
        return BuildConfig.HELIO_CAPTURE && coordinator instanceof AmazfitHelioStrapCoordinator
                && HelioCaptureStore.of(context).isOn();
    }

    @StringRes
    @Override
    public int taskDescription() {
        return R.string.busy_task_fetch_activity_data;
    }

    @Override
    protected String getLastSyncTimeKey() {
        // never read nor written: the capture keeps its own times (HelioCaptureStore.since)
        return "helioCapture_" + dataType.name();
    }

    @Override
    protected boolean processBufferedData() {
        return true;
    }

    @Override
    protected void startFetching() {
        if (session.aborted) {
            log(HelioCaptureStore.SKIPPED, details());
            finish();
            return;
        }
        if (store.dataBytes() >= HelioCaptureStore.MAX_BYTES) {
            log(HelioCaptureStore.FULL, details());
            finish();
            return;
        }
        final long now = clock.now();
        since = store.since(dataType, now);
        final GregorianCalendar sinceWhen = BLETypeConversions.createCalendar();
        sinceWhen.setTimeInMillis(since);
        clock.schedule(timeout, ANSWER_TIMEOUT_MS);
        startFetching(dataType.getCode(), sinceWhen);
    }

    private void onNoAnswer() {
        if (finished) {
            return;
        }
        session.aborted = true;
        final Map<String, Object> d = details();
        d.put("received", buffer.size());
        log(HelioCaptureStore.NO_ANSWER, d);
        finish();
    }

    private void finish() {
        if (finished) {
            return;
        }
        finished = true;
        clock.cancel(timeout);
        onOperationFinished();
    }

    private void waitMore() {
        clock.cancel(timeout);
        clock.schedule(timeout, ANSWER_TIMEOUT_MS);
    }

    @NonNull
    private Map<String, Object> details() {
        final Map<String, Object> d = new HashMap<>();
        if (since > 0) {
            d.put("since", since);
        }
        if (startResponse != null) {
            d.put("start", HelioCaptureStore.hex(startResponse));
        }
        return d;
    }

    private void log(@NonNull final String outcome, @NonNull final Map<String, Object> details) {
        LOG.info("Capture {}: {} {}", dataType, outcome, details);
        store.log(dataType, clock.now(), outcome, details);
    }

    /** A refusal or a malformed answer: logged with the band's bytes, no ack. */
    private void failed(@NonNull final String outcome, @NonNull final byte[] value) {
        final Map<String, Object> d = details();
        d.put("response", HelioCaptureStore.hex(value));
        if (value.length > 2) {
            d.put("status", value[2] & 0xff);
        }
        log(outcome, d);
        finish();
    }

    @Override
    public void handleActivityData(final byte[] value) {
        if (finished || value.length == 0) {
            return;
        }
        waitMore();
        if ((byte) (lastPacketCounter + 1) == value[0]) {
            lastPacketCounter++;
            bufferActivityData(value);
        } else {
            // no toast, nothing dropped: the payload is saved as it came, with the error in the log
            counterError = true;
            lastPacketCounter = value[0];
            bufferActivityData(value);
        }
    }

    @Override
    public void handleActivityMetadata(final byte[] value) {
        if (finished) {
            return;
        }
        if (value.length < 3 || value[0] != HuamiService.RESPONSE) {
            failed(HelioCaptureStore.INVALID, value);
            return;
        }
        switch (value[1]) {
            case HuamiService.COMMAND_ACTIVITY_DATA_START_DATE:
                onStartDate(value);
                return;
            case HuamiService.COMMAND_FETCH_DATA:
                onFetchData(value);
                return;
            case HuamiService.COMMAND_ACK_ACTIVITY_DATA:
                finish();
                return;
            default:
                failed(HelioCaptureStore.INVALID, value);
        }
    }

    private void onStartDate(final byte[] value) {
        startResponse = value.clone();
        if (value[2] != HuamiService.SUCCESS) {
            failed(HelioCaptureStore.REJECTED, value);
            return;
        }
        if (value.length < 7) {
            failed(HelioCaptureStore.INVALID, value);
            return;
        }
        expectedDataLength = BLETypeConversions.toUint32(Arrays.copyOfRange(value, 3, 7));
        if (expectedDataLength == 0) {
            final Map<String, Object> d = details();
            d.put("bytes", 0);
            log(HelioCaptureStore.EMPTY, d);
            store.answered(dataType, clock.now());
            ack();
            return;
        }
        waitMore();
        fetcher.setNotifications(true, true);
        fetcher.writeControl(getName() + " capture", new byte[]{HuamiService.COMMAND_FETCH_DATA});
    }

    private void onFetchData(final byte[] value) {
        if (value[2] != HuamiService.SUCCESS) {
            failed(HelioCaptureStore.REJECTED, value);
            return;
        }
        final byte[] payload = buffer.toByteArray();
        final Map<String, Object> d = details();
        d.put("bytes", payload.length);
        d.put("expected", expectedDataLength);
        if (value.length >= 7) {
            d.put("crc_ok", BLETypeConversions.toUint32(value, 3) == CheckSums.getCRC32(payload));
        }
        if (counterError) {
            d.put("counter_error", true);
        }
        try {
            d.put("file", store.saveData(dataType, clock.now(), payload));
            log(HelioCaptureStore.DATA, d);
            store.answered(dataType, clock.now());
        } catch (final IOException e) {
            d.put("error", String.valueOf(e.getMessage()));
            log(HelioCaptureStore.INVALID, d);
        }
        ack();
    }

    /** Always "keep on device": the band does not mark anything as synced. */
    private void ack() {
        waitMore();
        fetcher.writeControl(getName() + " capture end",
                new byte[]{HuamiService.COMMAND_ACK_ACTIVITY_DATA, KEEP_ON_DEVICE});
    }

    @Override
    protected void sendAck(final boolean keepDataOnDevice) {
        ack();
    }
}
