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

import androidx.annotation.NonNull;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * Actions that the Helio page can ask of Gadgetbridge, from the band menu.
 */
public interface HelioGbActions {
    String SYNC = "sync";
    String SETTINGS = "settings";
    String CHARTS = "charts";
    String DATA = "data";
    String GADGETBRIDGE = "gadgetbridge";
    /** Choose a food catalog package to import (file picker; Helio checks and installs it). */
    String CATALOG_IMPORT = "catalog_import";
    /** Scan a product barcode (camera permission asked at the first scan); the digits go back to the page. */
    String SCAN = "scan";
    /** Type or paste the band's auth key (checked, saved in the band's preferences by MAC address). */
    String AUTH_KEY = "authkey";
    /** Choose the automatic backup folder (Android's folder picker, persistent permission), then back up at once. */
    String BACKUP_FOLDER = "backup_folder";
    /** Back up now into the chosen folder. */
    String BACKUP_NOW = "backup_now";
    /** Stop the automatic backups: the permission on the folder is given back, the files there stay. */
    String BACKUP_OFF = "backup_off";
    /** How often: every day, week or month. */
    String BACKUP_DAY = "backup_day";
    String BACKUP_WEEK = "backup_week";
    String BACKUP_MONTH = "backup_month";
    /** The reminders changed in settings.json: set the alarms again, asking for the notification permission if needed. */
    String REMINDERS = "reminders";
    /** Android's notification settings of Ritmo (permission refused for good, or notifications turned off). */
    String NOTIFICATION_SETTINGS = "notif_settings";
    /**
     * Capture mode, only in personal test builds (-PhelioCapture=true; elsewhere these do nothing): raw answers of the
     * band to the fetch types not handled yet, saved privately during the sync; save them as a .tgz, delete them.
     */
    String CAPTURE_ON = "capture_on";
    String CAPTURE_OFF = "capture_off";
    String CAPTURE_EXPORT = "capture_export";
    String CAPTURE_CLEAR = "capture_clear";

    /** Same list as helio_android.ANDROID_ACTIONS. */
    Set<String> ALL = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(SYNC, SETTINGS, CHARTS, DATA, GADGETBRIDGE,
            CATALOG_IMPORT, SCAN, AUTH_KEY, BACKUP_FOLDER, BACKUP_NOW, BACKUP_OFF, BACKUP_DAY, BACKUP_WEEK, BACKUP_MONTH,
            REMINDERS, NOTIFICATION_SETTINGS, CAPTURE_ON, CAPTURE_OFF, CAPTURE_EXPORT, CAPTURE_CLEAR)));

    void run(@NonNull String action);

    /**
     * Whether the main Gadgetbridge process is running (otherwise the band cannot be connected).
     */
    interface ProcessProbe {
        boolean isGadgetbridgeRunning();
    }
}
