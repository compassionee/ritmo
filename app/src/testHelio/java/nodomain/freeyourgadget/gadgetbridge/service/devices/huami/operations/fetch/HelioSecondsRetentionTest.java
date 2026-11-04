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

import android.content.Context;

import org.junit.Test;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

import nodomain.freeyourgadget.gadgetbridge.database.DBHelper;
import nodomain.freeyourgadget.gadgetbridge.devices.GenericHeartRateSampleProvider;
import nodomain.freeyourgadget.gadgetbridge.entities.GenericHeartRateSample;
import nodomain.freeyourgadget.gadgetbridge.entities.GenericHeartRateSampleDao;
import nodomain.freeyourgadget.gadgetbridge.impl.GBDevice;
import nodomain.freeyourgadget.gadgetbridge.model.DeviceType;
import nodomain.freeyourgadget.gadgetbridge.test.TestBase;

/**
 * Pruning of the heart rate seconds: only the device's rows, only up to where Ritmo wrote (after saving their results),
 * never the last 30 days, nothing without Ritmo's file. Synthetic rows.
 */
@Config(sdk = 34)
public class HelioSecondsRetentionTest extends TestBase {
    private static final long DAY_MS = 24L * 60 * 60 * 1000;
    private static final long NOW = 1_762_160_400_000L;

    private Context context() {
        return RuntimeEnvironment.getApplication();
    }

    private void writeFile(final String json) throws Exception {
        Files.write(new File(context().getFilesDir(), HelioSecondsRetention.FILE).toPath(), json.getBytes(StandardCharsets.UTF_8));
    }

    private long fill(final GBDevice device) {
        final long id = DBHelper.getDevice(device, daoSession).getId();
        final List<GenericHeartRateSample> rows = new ArrayList<>();
        // one row a day, from 120 days ago to today
        for (int d = 120; d >= 0; d--) {
            final GenericHeartRateSample s = new GenericHeartRateSample();
            s.setTimestamp(NOW - d * DAY_MS);
            s.setHeartRate(60);
            rows.add(s);
        }
        new GenericHeartRateSampleProvider(device, daoSession).persistSamples(rows, null);
        return id;
    }

    private long rows(final long deviceId) {
        return daoSession.getGenericHeartRateSampleDao().queryBuilder()
                .where(GenericHeartRateSampleDao.Properties.DeviceId.eq(deviceId)).count();
    }

    @Test
    public void prunesOnlyWhatRitmoAllows() throws Exception {
        final GBDevice band = new GBDevice("AA:BB:CC:DD:EE:01", "Amazfit Helio Strap", null, null, DeviceType.AMAZFITHELIOSTRAP);
        final GBDevice other = new GBDevice("AA:BB:CC:DD:EE:02", "Other", null, null, DeviceType.AMAZFITHELIOSTRAP);
        final long id = fill(band);
        final long otherId = fill(other);
        assertEquals(121, rows(id));

        // no file: nothing
        new File(context().getFilesDir(), HelioSecondsRetention.FILE).delete();
        assertEquals(0, HelioSecondsRetention.prune(context(), daoSession, id, NOW));
        // another device's limit, damaged file, 0 (kept always): nothing
        writeFile("{\"before_ms\": " + (NOW - 90 * DAY_MS) + ", \"device_id\": " + (id + 100) + "}");
        assertEquals(0, HelioSecondsRetention.prune(context(), daoSession, id, NOW));
        writeFile("not json");
        assertEquals(0, HelioSecondsRetention.prune(context(), daoSession, id, NOW));
        writeFile("{\"before_ms\": 0, \"device_id\": " + id + "}");
        assertEquals(0, HelioSecondsRetention.prune(context(), daoSession, id, NOW));
        assertEquals(121, rows(id));

        // 90 days: the 30 rows older than 90 days go (days 120..91), the other device's stay
        writeFile("{\"before_ms\": " + (NOW - 90 * DAY_MS) + ", \"device_id\": " + id + ", \"keep_days\": 90}");
        assertEquals(30, HelioSecondsRetention.prune(context(), daoSession, id, NOW));
        assertEquals(91, rows(id));
        assertEquals(121, rows(otherId));
        assertEquals(0, HelioSecondsRetention.prune(context(), daoSession, id, NOW));  // again: nothing more

        // a limit too close to now is held at 30 days
        writeFile("{\"before_ms\": " + NOW + ", \"device_id\": " + id + "}");
        HelioSecondsRetention.prune(context(), daoSession, id, NOW);
        assertEquals(31, rows(id));                            // days 30..0
        assertEquals(0, HelioSecondsRetention.prune(context(), daoSession, null, NOW));
    }
}
