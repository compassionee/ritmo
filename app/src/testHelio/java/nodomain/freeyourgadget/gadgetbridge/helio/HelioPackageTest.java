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

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import android.app.Application;
import android.content.Context;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import nodomain.freeyourgadget.gadgetbridge.BuildConfig;
import nodomain.freeyourgadget.gadgetbridge.R;

/**
 * The applicationId of the helio flavor comes from a Gradle property (helioApplicationId or
 * helio.applicationId in local.properties); the authorities must follow it, so that a build with
 * another id never clashes with an installed one.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioPackageTest {
    private final Context context = RuntimeEnvironment.getApplication();

    @Test
    public void pebbleProviderAuthorityFollowsTheApplicationId() {
        assertEquals(BuildConfig.APPLICATION_ID + ".pebble_provider", context.getString(R.string.pebble_content_provider));
        // never the authority of the official Pebble app or of Gadgetbridge
        assertNotEquals("com.getpebble.android.provider", context.getString(R.string.pebble_content_provider));
    }

    @Test
    public void applicationIdResourceIsTheBuildOne() {
        assertEquals(BuildConfig.APPLICATION_ID, context.getString(R.string.applicationId));
        assertTrue(BuildConfig.APPLICATION_ID.matches("^[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+$"));
    }

}
