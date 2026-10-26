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

    /** Same list as helio_android.ANDROID_ACTIONS. */
    Set<String> ALL = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(SYNC, SETTINGS, CHARTS, DATA, GADGETBRIDGE,
            CATALOG_IMPORT, SCAN, AUTH_KEY)));

    void run(@NonNull String action);

    /**
     * Whether the main Gadgetbridge process is running (otherwise the band cannot be connected).
     */
    interface ProcessProbe {
        boolean isGadgetbridgeRunning();
    }
}
