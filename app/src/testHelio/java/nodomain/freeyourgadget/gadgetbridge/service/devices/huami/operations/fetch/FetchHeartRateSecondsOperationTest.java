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

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.database.Cursor;

import org.junit.Test;
import org.robolectric.annotation.Config;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.GregorianCalendar;
import java.util.List;

import nodomain.freeyourgadget.gadgetbridge.database.DBHelper;
import nodomain.freeyourgadget.gadgetbridge.devices.GenericHeartRateSampleProvider;
import nodomain.freeyourgadget.gadgetbridge.devices.GenericHrvSdnnSampleProvider;
import nodomain.freeyourgadget.gadgetbridge.entities.GenericHeartRateSample;
import nodomain.freeyourgadget.gadgetbridge.entities.GenericHrvSdnnSample;
import nodomain.freeyourgadget.gadgetbridge.impl.GBDevice;
import nodomain.freeyourgadget.gadgetbridge.model.DeviceType;
import nodomain.freeyourgadget.gadgetbridge.test.TestBase;

/**
 * SDNN (0x4e) and heart rate every second (0x57) from the Helio Strap: parsed from synthetic bytes built on the formats
 * confirmed by two captures (no real data here), then saved in Gadgetbridge's tables, which Ritmo reads.
 */
@Config(sdk = 34)
public class FetchHeartRateSecondsOperationTest extends TestBase {
    private static final long T0 = 1_762_160_400L;  // seconds

    /** One 0x57 packet: start, 300 slots, the values, then 0xFF padding. */
    static byte[] packet(final long start, final int... bpm) {
        final ByteBuffer b = ByteBuffer.allocate(306).order(ByteOrder.LITTLE_ENDIAN);
        b.putInt((int) start).putShort((short) 300);
        for (int i = 0; i < 300; i++) {
            b.put(i < bpm.length ? (byte) bpm[i] : (byte) 0xff);
        }
        return b.array();
    }

    static byte[] join(final byte[]... parts) {
        final ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (final byte[] p : parts) {
            out.write(p, 0, p.length);
        }
        return out.toByteArray();
    }

    static int[] ramp(final int n, final int from) {
        final int[] v = new int[n];
        for (int i = 0; i < n; i++) {
            v[i] = from + i % 40;
        }
        return v;
    }

    static byte[] sdnn(final long ts, final int tz, final long hundredths) {
        return ByteBuffer.allocate(9).order(ByteOrder.LITTLE_ENDIAN).putInt((int) ts).put((byte) tz).putInt((int) hundredths).array();
    }

    // ------------------------------------------------------------ 0x57

    @Test
    public void secondsFromPacketsWithPadding() {
        final FetchHeartRateSecondsOperation.Parsed p = FetchHeartRateSecondsOperation.parse(
                join(packet(T0, ramp(300, 60)), packet(T0 + 301, 70, 71, 72)));
        assertEquals(303, p.samples.size());
        assertEquals(T0 * 1000, (long) p.samples.get(0).getTimestamp());
        assertEquals(60, p.samples.get(0).getHeartRate());
        assertEquals((T0 + 299) * 1000, (long) p.samples.get(299).getTimestamp());  // sample i at start + i
        assertEquals((T0 + 303) * 1000, (long) p.samples.get(302).getTimestamp());  // second packet: start + 2
        assertEquals(72, p.samples.get(302).getHeartRate());
        assertEquals((T0 + 301) * 1000, p.lastPacketStartMs);
    }

    @Test
    public void partialPacketThenWholeOneKeepsTheLongest() {
        // the band sends part of a packet, then the same packet whole (seen in both captures)
        final byte[] partial = packet(T0, 88);
        final byte[] whole = packet(T0, ramp(120, 80));
        for (final byte[] data : Arrays.asList(join(partial, whole), join(whole, partial))) {
            final FetchHeartRateSecondsOperation.Parsed p = FetchHeartRateSecondsOperation.parse(data);
            assertEquals(120, p.samples.size());
            assertEquals(80, p.samples.get(0).getHeartRate());
        }
    }

    @Test
    public void invalidReadingsAndOverlapsAreLeftOut() {
        // 0 (no reading) at the end of a packet, 0xFF in the middle, a packet overlapping the previous one
        final FetchHeartRateSecondsOperation.Parsed p = FetchHeartRateSecondsOperation.parse(
                join(packet(T0, 70, 255, 72, 0), packet(T0 + 2, 99, 74)));
        final List<Long> ts = new ArrayList<>();
        final List<Integer> bpm = new ArrayList<>();
        for (final GenericHeartRateSample s : p.samples) {
            ts.add(s.getTimestamp() / 1000 - T0);
            bpm.add(s.getHeartRate());
        }
        assertEquals(Arrays.asList(0L, 2L, 3L), ts);
        assertEquals(Arrays.asList(70, 72, 74), bpm);
    }

    @Test
    public void cutShortOrTrailingBytesAreRefused() {
        final byte[] one = packet(T0, 70, 71);
        assertNull(FetchHeartRateSecondsOperation.parse(Arrays.copyOf(one, 200)));
        assertNull(FetchHeartRateSecondsOperation.parse(join(one, new byte[]{1, 2})));
        assertEquals(0, FetchHeartRateSecondsOperation.parse(new byte[0]).samples.size());
        // an empty slot list is fine (count 0)
        final byte[] empty = ByteBuffer.allocate(6).order(ByteOrder.LITTLE_ENDIAN).putInt((int) T0).putShort((short) 0).array();
        assertEquals(2, FetchHeartRateSecondsOperation.parse(join(empty, one)).samples.size());
    }

    // ------------------------------------------------------------ 0x4e

    @Test
    public void sdnnRecords() {
        final List<GenericHrvSdnnSample> s = FetchHrvSdnnOperation.parse(join(
                sdnn(T0, 4, 4223), sdnn(T0 + 3600, 4, 10212), sdnn(T0 + 7200, 4, 0), sdnn(T0 + 9000, -8, 6150)));
        assertEquals(3, s.size());                             // 0 is not a measurement
        assertEquals(42, s.get(0).getValue());                 // ms x 100 -> ms
        assertEquals(102, s.get(1).getValue());
        assertEquals((T0 + 9000) * 1000, (long) s.get(2).getTimestamp());  // the time zone byte does not move it
        assertEquals(62, s.get(2).getValue());
        assertNull(FetchHrvSdnnOperation.parse(new byte[10]));
        assertEquals(0, FetchHrvSdnnOperation.parse(sdnn(T0, 4, 60000)).size());  // 600 ms: not plausible
    }

    // ------------------------------------------------------------ where the next fetch starts

    @Test
    public void nextFetchStartsFromTheLastData() {
        final GregorianCalendar t = new GregorianCalendar();
        t.setTimeInMillis(T0 * 1000);
        new FetchHrvSdnnOperation(null).advanceLastSyncTimestamp(t);
        assertEquals(T0 * 1000 + 1000, t.getTimeInMillis());   // one second after the last record
        t.setTimeInMillis(T0 * 1000);
        new FetchHeartRateSecondsOperation(null).advanceLastSyncTimestamp(t);
        assertEquals(T0 * 1000, t.getTimeInMillis());          // the last packet again: it may still be filling up
        final GregorianCalendar other = new GregorianCalendar();
        other.setTimeInMillis(T0 * 1000);
        new FetchHrvOperation(null).advanceLastSyncTimestamp(other);
        other.add(Calendar.MINUTE, -1);
        assertEquals(T0 * 1000, other.getTimeInMillis());      // the other fetches are unchanged
    }

    // ------------------------------------------------------------ Gadgetbridge's tables (read by Ritmo)

    @Test
    public void savedInGadgetbridgeTablesAndReplacedWhenReadAgain() throws Exception {
        final GBDevice device = new GBDevice("AA:BB:CC:DD:EE:01", "Amazfit Helio Strap", null, null, DeviceType.AMAZFITHELIOSTRAP);
        final long deviceId = DBHelper.getDevice(device, daoSession).getId();
        final GenericHeartRateSampleProvider hr = new GenericHeartRateSampleProvider(device, daoSession);
        hr.persistSamples(FetchHeartRateSecondsOperation.parse(packet(T0, 90)).samples, null);
        // the next sync reads the last packet again, now whole
        hr.persistSamples(FetchHeartRateSecondsOperation.parse(packet(T0, ramp(60, 90))).samples, null);
        final GenericHrvSdnnSampleProvider sdnnProvider = new GenericHrvSdnnSampleProvider(device, daoSession);
        sdnnProvider.persistSamples(FetchHrvSdnnOperation.parse(join(sdnn(T0, 4, 5000), sdnn(T0 + 60, 4, 6000))), null);

        try (Cursor c = daoSession.getDatabase().rawQuery(
                "SELECT COUNT(*), MIN(TIMESTAMP), MAX(TIMESTAMP), MIN(HEART_RATE) FROM GENERIC_HEART_RATE_SAMPLE WHERE DEVICE_ID=?",
                new String[]{String.valueOf(deviceId)})) {
            assertTrue(c.moveToFirst());
            assertEquals(60, c.getInt(0));                     // replaced, never doubled
            assertEquals(T0 * 1000, c.getLong(1));             // milliseconds
            assertEquals((T0 + 59) * 1000, c.getLong(2));
            assertEquals(90, c.getInt(3));
        }
        try (Cursor c = daoSession.getDatabase().rawQuery(
                "SELECT TIMESTAMP, VALUE, USER_ID FROM GENERIC_HRV_SDNN_SAMPLE WHERE DEVICE_ID=? ORDER BY TIMESTAMP",
                new String[]{String.valueOf(deviceId)})) {
            assertTrue(c.moveToFirst());
            assertEquals(T0 * 1000, c.getLong(0));
            assertEquals(50, c.getInt(1));
            assertTrue(c.moveToNext());
            assertEquals(60, c.getInt(1));
        }
    }
}
