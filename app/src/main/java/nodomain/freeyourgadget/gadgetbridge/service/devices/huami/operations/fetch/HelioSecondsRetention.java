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

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import org.json.JSONException;
import org.json.JSONObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

import nodomain.freeyourgadget.gadgetbridge.entities.DaoSession;
import nodomain.freeyourgadget.gadgetbridge.entities.GenericHeartRateSampleDao;

/**
 * How long the heart rate of every second is kept (Helio build). Ritmo chooses the period (30, 90, 365 days or always)
 * and, before saying a day may go, saves that day's results in its own data; it writes up to where this side may delete
 * in {@link #FILE} (helio_android.seconds_retention). After each per-second fetch, the older rows of that device are
 * deleted: never past what Ritmo wrote, never within the last {@link #MIN_KEEP_MS}. Without the file (no Ritmo, or
 * Ritmo not opened yet) nothing is deleted.
 */
public final class HelioSecondsRetention {
    private static final Logger LOG = LoggerFactory.getLogger(HelioSecondsRetention.class);

    public static final String FILE = "helio-seconds-retention.json";
    /** The shortest period Ritmo offers: rows newer than this are never deleted, whatever the file says. */
    static final long MIN_KEEP_MS = 30L * 24 * 60 * 60 * 1000;

    private HelioSecondsRetention() {
    }

    /** Up to where (ms, excluded) the rows of the device may be deleted, or 0 for none. */
    static long pruneBefore(@NonNull final File file, final long deviceId, final long now) {
        final JSONObject o;
        try {
            o = new JSONObject(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
        } catch (final IOException | JSONException | RuntimeException e) {
            return 0;
        }
        if (o.optLong("device_id", -1) != deviceId) {
            return 0;
        }
        final long before = o.optLong("before_ms", 0);
        if (before <= 0) {
            return 0;
        }
        return Math.min(before, now - MIN_KEEP_MS);
    }

    /** Deletes the device's rows older than Ritmo allows; returns how many went. */
    public static long prune(@NonNull final Context context, @NonNull final DaoSession session, @Nullable final Long deviceId,
                             final long now) {
        if (deviceId == null) {
            return 0;
        }
        final long before = pruneBefore(new File(context.getFilesDir(), FILE), deviceId, now);
        if (before <= 0) {
            return 0;
        }
        final GenericHeartRateSampleDao dao = session.getGenericHeartRateSampleDao();
        final long count = dao.queryBuilder()
                .where(GenericHeartRateSampleDao.Properties.DeviceId.eq(deviceId),
                        GenericHeartRateSampleDao.Properties.Timestamp.lt(before))
                .count();
        if (count > 0) {
            dao.queryBuilder()
                    .where(GenericHeartRateSampleDao.Properties.DeviceId.eq(deviceId),
                            GenericHeartRateSampleDao.Properties.Timestamp.lt(before))
                    .buildDelete().executeDeleteWithoutDetachingEntities();
            LOG.info("Deleted {} heart rate seconds before {}", count, before);
        }
        return count;
    }
}
