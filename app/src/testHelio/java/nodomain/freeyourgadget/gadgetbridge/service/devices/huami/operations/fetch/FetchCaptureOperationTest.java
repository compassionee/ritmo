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

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import android.app.Application;
import android.content.Context;

import androidx.annotation.NonNull;

import org.json.JSONObject;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import java.io.File;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.List;
import java.util.concurrent.TimeUnit;

import nodomain.freeyourgadget.gadgetbridge.BuildConfig;
import nodomain.freeyourgadget.gadgetbridge.devices.DeviceCoordinator;
import nodomain.freeyourgadget.gadgetbridge.devices.huami.zeppos.straps.AmazfitHelioStrapCoordinator;
import nodomain.freeyourgadget.gadgetbridge.impl.GBDevice;
import nodomain.freeyourgadget.gadgetbridge.service.SleepAsAndroidSender;
import nodomain.freeyourgadget.gadgetbridge.service.devices.huami.HuamiFetcher;
import nodomain.freeyourgadget.gadgetbridge.util.CheckSums;

/**
 * Capture mode of the Helio build: the band's answers saved raw, refusals and empty answers logged, the ack always
 * "keep on device" (0x09), a missing answer stopping the other capture requests of the sync, nothing without the build
 * flag. The band is simulated: the operation sees only the bytes it would get over Bluetooth.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class FetchCaptureOperationTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private static final long NOW = 1_762_160_400_000L;  // 2025-11-03
    private static final long DAY = 24L * 60 * 60 * 1000;

    private HelioCaptureStore store;
    private final List<byte[]> written = new ArrayList<>();
    private FakeClock clock;
    private HuamiFetcher fetcher;

    static final class FakeClock implements FetchCaptureOperation.Clock {
        long now = NOW;
        final List<Runnable> timers = new ArrayList<>();

        @Override
        public long now() {
            return now;
        }

        @Override
        public void schedule(@NonNull final Runnable r, final long delayMs) {
            timers.add(r);
        }

        @Override
        public void cancel(@NonNull final Runnable r) {
            timers.remove(r);
        }

        void fire() {
            for (final Runnable r : new ArrayList<>(timers)) {
                timers.remove(r);
                r.run();
            }
        }
    }

    @Before
    public void setUp() throws Exception {
        store = new HelioCaptureStore(tmp.newFolder("helio-capture"));
        clock = new FakeClock();
        final Context context = RuntimeEnvironment.getApplication();
        fetcher = new HuamiFetcher(new HuamiFetcher.HuamiFetchSupport() {
            @Override
            public DeviceCoordinator getCoordinator() {
                return new AmazfitHelioStrapCoordinator();
            }

            @Override
            public GBDevice getDevice() {
                return null;
            }

            @Override
            public Context getContext() {
                return context;
            }

            @Override
            public int getActivitySampleSize() {
                return 4;
            }

            @Override
            public byte[] getTimeBytes(final Calendar calendar, final TimeUnit precision) {
                return new byte[8];
            }

            @Override
            public void setActivityNotifications(final boolean control, final boolean data) {
            }

            @Override
            public void writeActivityControl(final String name, final byte[] value) {
                written.add(value);
            }

            @Override
            public SleepAsAndroidSender getSleepAsAndroidSender() {
                return null;
            }
        }) {
            @Override
            public TimeUnit getFetchOperationsTimeUnit() {
                return TimeUnit.MINUTES;
            }
        };
    }

    private FetchCaptureOperation op(final HuamiFetchDataType type, final FetchCaptureOperation.Session session) {
        return new FetchCaptureOperation(fetcher, type, store, session, clock);
    }

    /** The band's answer to the start request: status, how many bytes will come, the start date. */
    private static byte[] startAnswer(final int status, final int length) {
        return new byte[]{0x10, 0x01, (byte) status, (byte) length, (byte) (length >> 8), (byte) (length >> 16),
                (byte) (length >> 24), (byte) 0xe9, 0x07, 0x0b, 0x01, 0x00, 0x00, 0x00, 0x00};
    }

    private static byte[] fetchDone(final byte[] payload) {
        final int crc = CheckSums.getCRC32(payload);
        return new byte[]{0x10, 0x02, 0x01, (byte) crc, (byte) (crc >> 8), (byte) (crc >> 16), (byte) (crc >> 24)};
    }

    private JSONObject lastLog() {
        final List<JSONObject> lines = store.logLines();
        return lines.get(lines.size() - 1);
    }

    private void assertNeverDropsFromTheBand() {
        for (final byte[] w : written) {
            if (w[0] == 0x03) {
                assertEquals("ack must keep the data on the band", 2, w.length);
                assertEquals(0x09, w[1]);
            }
        }
    }

    @Test
    public void dataIsSavedRawAndLeftOnTheBand() throws Exception {
        final FetchCaptureOperation op = op(HuamiFetchDataType.SPO2_OSA_EVENT, new FetchCaptureOperation.Session());
        op.doPerform();
        assertEquals(1, written.size());
        assertEquals(0x01, written.get(0)[0]);                 // start request
        assertEquals((byte) 0x27, written.get(0)[1]);          // for the asked type
        final byte[] payload = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77};
        op.handleActivityMetadata(startAnswer(1, payload.length));
        assertArrayEquals(new byte[]{0x02}, written.get(1));  // fetch
        op.handleActivityData(new byte[]{0x00, 0x11, 0x22, 0x33});
        op.handleActivityData(new byte[]{0x01, 0x44, 0x55, 0x66, 0x77});
        op.handleActivityMetadata(fetchDone(payload));
        assertArrayEquals(new byte[]{0x03, 0x09}, written.get(2));
        op.handleActivityMetadata(new byte[]{0x10, 0x03, 0x01});
        assertEquals(3, written.size());

        final JSONObject log = lastLog();
        assertEquals("data", log.getString("outcome"));
        assertEquals("27", log.getString("type"));
        assertEquals(7, log.getInt("bytes"));
        assertTrue(log.getBoolean("crc_ok"));
        assertEquals(NOW - HelioCaptureStore.FIRST_SINCE_MS, log.getLong("since"));
        assertTrue(log.getString("start").startsWith("100101"));
        final File saved = new File(store.dir(), log.getString("file"));
        assertArrayEquals(payload, Files.readAllBytes(saved.toPath()));  // exactly as received, counters removed
        assertTrue(log.getString("file").matches("\\d{8}/\\d{6}-27\\.bin"));
        // the next sync starts a day before this answer
        assertEquals(NOW - DAY, store.since(HuamiFetchDataType.SPO2_OSA_EVENT, NOW + 1000));
        assertNeverDropsFromTheBand();
    }

    @Test
    public void refusalIsLoggedWithTheBandsStatus() throws Exception {
        final FetchCaptureOperation op = op(HuamiFetchDataType.BODY_BATTERY, new FetchCaptureOperation.Session());
        op.doPerform();
        op.handleActivityMetadata(new byte[]{0x10, 0x01, 0x04});
        final JSONObject log = lastLog();
        assertEquals("rejected", log.getString("outcome"));
        assertEquals(4, log.getInt("status"));
        assertEquals("100104", log.getString("response"));
        assertEquals(1, written.size());                       // no fetch, no ack
        assertTrue(store.dataFiles().isEmpty());
        // a refusal does not move the start of the next request
        assertEquals(NOW - HelioCaptureStore.FIRST_SINCE_MS, store.since(HuamiFetchDataType.BODY_BATTERY, NOW));
        // a refusal at the fetch step too
        final FetchCaptureOperation op2 = op(HuamiFetchDataType.REST_HR, new FetchCaptureOperation.Session());
        op2.doPerform();
        op2.handleActivityMetadata(startAnswer(1, 3));
        op2.handleActivityMetadata(new byte[]{0x10, 0x02, 0x05});
        assertEquals("rejected", lastLog().getString("outcome"));
        assertEquals(5, lastLog().getInt("status"));
    }

    @Test
    public void emptyAnswerIsLoggedAndAcked() throws Exception {
        final FetchCaptureOperation op = op(HuamiFetchDataType.REST_HR, new FetchCaptureOperation.Session());
        op.doPerform();
        op.handleActivityMetadata(startAnswer(1, 0));
        assertEquals("empty", lastLog().getString("outcome"));
        assertArrayEquals(new byte[]{0x03, 0x09}, written.get(written.size() - 1));
        assertTrue(store.dataFiles().isEmpty());
        assertNeverDropsFromTheBand();
    }

    @Test
    public void invalidAnswersAndPacketGapsAreKept() throws Exception {
        final FetchCaptureOperation op = op(HuamiFetchDataType.SPO2_OSA_EVENT, new FetchCaptureOperation.Session());
        op.doPerform();
        op.handleActivityMetadata(new byte[]{0x11, 0x01});
        assertEquals("invalid", lastLog().getString("outcome"));

        final FetchCaptureOperation op2 = op(HuamiFetchDataType.SEC_HR, new FetchCaptureOperation.Session());
        op2.doPerform();
        op2.handleActivityMetadata(startAnswer(1, 4));
        op2.handleActivityData(new byte[]{0x00, 0x01, 0x02});
        op2.handleActivityData(new byte[]{0x05, 0x03, 0x04});  // a packet missing: kept as received, flagged
        final byte[] wrongCrc = fetchDone(new byte[]{9});
        op2.handleActivityMetadata(wrongCrc);
        final JSONObject log = lastLog();
        assertEquals("data", log.getString("outcome"));
        assertTrue(log.getBoolean("counter_error"));
        assertFalse(log.getBoolean("crc_ok"));
        assertArrayEquals(new byte[]{1, 2, 3, 4}, Files.readAllBytes(new File(store.dir(), log.getString("file")).toPath()));
        assertNeverDropsFromTheBand();
    }

    @Test
    public void noAnswerStopsTheOtherCaptureRequests() throws Exception {
        final FetchCaptureOperation.Session session = new FetchCaptureOperation.Session();
        final FetchCaptureOperation op = op(HuamiFetchDataType.SEC_HR, session);
        op.doPerform();
        clock.fire();                                          // 60 s without a byte
        assertEquals("no_answer", lastLog().getString("outcome"));
        assertTrue(session.aborted);
        // a late answer changes nothing
        op.handleActivityMetadata(startAnswer(1, 10));
        assertEquals(1, written.size());
        // the next type of the same sync is not even asked
        op(HuamiFetchDataType.SEC_HR, session).doPerform();
        assertEquals("skipped", lastLog().getString("outcome"));
        assertEquals(1, written.size());
        // a new sync asks again
        op(HuamiFetchDataType.SEC_HR, new FetchCaptureOperation.Session()).doPerform();
        assertEquals(2, written.size());
    }

    @Test
    public void storeSettingsSummaryAndClear() throws Exception {
        assertFalse(store.isOn());                             // off by default
        store.setOn(true);
        assertTrue(store.isOn());
        final FetchCaptureOperation op = op(HuamiFetchDataType.SPO2_OSA_EVENT, new FetchCaptureOperation.Session());
        op.doPerform();
        op.handleActivityMetadata(startAnswer(1, 2));
        op.handleActivityData(new byte[]{0x00, 0x01, 0x02});
        op.handleActivityMetadata(fetchDone(new byte[]{1, 2}));
        final FetchCaptureOperation op2 = op(HuamiFetchDataType.BODY_BATTERY, new FetchCaptureOperation.Session());
        op2.doPerform();
        op2.handleActivityMetadata(new byte[]{0x10, 0x01, 0x04});

        final JSONObject s = store.summary();
        assertTrue(s.getBoolean("on"));
        assertEquals(1, s.getInt("files"));
        assertEquals(2, s.getLong("bytes"));
        assertEquals(HelioCaptureStore.TYPES.length, s.getJSONArray("types").length());
        JSONObject osa = null;
        JSONObject battery = null;
        for (int i = 0; i < s.getJSONArray("types").length(); i++) {
            final JSONObject t = s.getJSONArray("types").getJSONObject(i);
            if ("27".equals(t.getString("code"))) {
                osa = t;
            } else if ("43".equals(t.getString("code"))) {
                battery = t;
            }
        }
        assertEquals("data", osa.getJSONObject("last").getString("outcome"));
        assertEquals(1, osa.getJSONObject("counts").getInt("data"));
        assertEquals("rejected", battery.getJSONObject("last").getString("outcome"));
        assertEquals(4, battery.getJSONObject("last").getInt("status"));

        store.clear();
        assertTrue(store.isOn());                              // the choice stays
        assertEquals(0, store.summary().getInt("files"));
        assertTrue(store.logLines().isEmpty());
        assertEquals(NOW - HelioCaptureStore.FIRST_SINCE_MS, store.since(HuamiFetchDataType.SPO2_OSA_EVENT, NOW));
    }

    @Test
    public void capturedTypesAreTheRequestedOnes() {
        final List<Integer> codes = new ArrayList<>();
        for (final HuamiFetchDataType t : HelioCaptureStore.TYPES) {
            codes.add(t.getCode() & 0xff);
        }
        assertEquals(java.util.Arrays.asList(0x56, 0x46, 0x43, 0x27, 0x4c), codes);
        // none of them is a type Gadgetbridge already fetches and saves (its ack would drop it from the band)
        for (final HuamiFetchDataType t : HelioCaptureStore.TYPES) {
            assertNotEquals(HuamiFetchDataType.HRV, t);
            assertNotEquals(HuamiFetchDataType.RESTING_HEART_RATE, t);
            assertNotEquals(HuamiFetchDataType.SHRV_SDNN, t);
            assertNotEquals(HuamiFetchDataType.SEC_HR_V2, t);
        }
    }

    @Test
    public void nothingWithoutTheBuildFlag() throws Exception {
        // the unit tests are built without -PhelioCapture=true, like every release
        assertFalse(BuildConfig.HELIO_CAPTURE);
        HelioCaptureStore.of(RuntimeEnvironment.getApplication()).setOn(true);
        assertFalse(FetchCaptureOperation.wanted(RuntimeEnvironment.getApplication(), new AmazfitHelioStrapCoordinator()));
        assertFalse(FetchCaptureOperation.wanted(RuntimeEnvironment.getApplication(), null));
    }
}
