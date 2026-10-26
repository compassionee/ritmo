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
package nodomain.freeyourgadget.gadgetbridge.helio;

import android.content.Context;
import android.content.SharedPreferences;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import java.io.File;
import java.util.Map;

/**
 * Band state as seen by Gadgetbridge, saved in a preferences file for the Helio process, which
 * reads it (read only) in helio_android.read_band_state. Gadgetbridge keeps the connection state
 * only in memory, and announces changes with local broadcasts of the main process.
 * <p>
 * Keys, for each device address: {@code <address>/state} (GBDevice.State name),
 * {@code <address>/busy} (busy task, empty when idle), {@code <address>/updated} and
 * {@code <address>/last_sync} (end of the last transfer while connected), in milliseconds.
 */
public final class HelioBandState {
    public static final String PREFS_NAME = "helio_band_state";

    private HelioBandState() {
    }

    /** The XML file of the preferences, for the Python side. */
    @NonNull
    public static File file(@NonNull final Context context) {
        return new File(context.getApplicationInfo().dataDir, "shared_prefs/" + PREFS_NAME + ".xml");
    }

    /**
     * Records a device update. A busy period that ends while the device is connected is a
     * finished synchronization.
     */
    public static void record(@NonNull final Context context, @NonNull final String address,
                              @NonNull final String state, @Nullable final String busyTask,
                              final boolean initialized, final long now) {
        final SharedPreferences prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        final String busy = busyTask != null ? busyTask : "";
        final boolean wasBusy = !prefs.getString(address + "/busy", "").isEmpty();
        final SharedPreferences.Editor editor = prefs.edit()
                .putString(address + "/state", state)
                .putString(address + "/busy", busy)
                .putLong(address + "/updated", now);
        if (wasBusy && busy.isEmpty() && initialized) {
            editor.putLong(address + "/last_sync", now);
        }
        editor.apply();
    }

    /**
     * At the start of the Gadgetbridge process no device is connected yet: the saved states of
     * the previous run would be stale. The last synchronization times stay.
     */
    public static void resetConnections(@NonNull final Context context, final long now) {
        final SharedPreferences prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        final SharedPreferences.Editor editor = prefs.edit();
        for (final Map.Entry<String, ?> entry : prefs.getAll().entrySet()) {
            final String key = entry.getKey();
            if (key.endsWith("/state")) {
                final String address = key.substring(0, key.length() - "/state".length());
                editor.putString(key, "NOT_CONNECTED")
                        .putString(address + "/busy", "")
                        .putLong(address + "/updated", now);
            }
        }
        editor.apply();
    }
}
