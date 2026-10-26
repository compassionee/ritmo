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
package nodomain.freeyourgadget.gadgetbridge;

import static org.junit.Assert.assertThrows;

import android.app.Application;
import android.content.ComponentCallbacks2;
import android.content.res.Configuration;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

import java.lang.reflect.Field;

/**
 * In the Helio process GBApplication skips its initialization: the application callbacks that
 * Android sends in the background (memory, configuration) must not touch what was not created.
 * Before the fix, onTrimMemory(TRIM_MEMORY_BACKGROUND) crashed :helio after Home.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class GBApplicationHelioProcessTest {
    private static GBApplication helioProcessApp(final boolean helio) throws Exception {
        final GBApplication app = new GBApplication();
        final Field f = GBApplication.class.getDeclaredField("helioProcess");
        f.setAccessible(true);
        f.setBoolean(app, helio);
        return app;
    }

    @Test
    public void backgroundCallbacksDoNothingInTheHelioProcess() throws Exception {
        final GBApplication app = helioProcessApp(true);
        for (final int level : new int[]{ComponentCallbacks2.TRIM_MEMORY_UI_HIDDEN,
                ComponentCallbacks2.TRIM_MEMORY_BACKGROUND, ComponentCallbacks2.TRIM_MEMORY_COMPLETE}) {
            app.onTrimMemory(level);
        }
        app.onConfigurationChanged(new Configuration());
        app.onLowMemory();
    }

    @Test
    public void withoutTheGuardTheUninitializedAppCrashes() throws Exception {
        // counter-check: the same object without the guard is what used to close Helio
        final GBApplication app = helioProcessApp(false);
        assertThrows(NullPointerException.class, () -> app.onTrimMemory(ComponentCallbacks2.TRIM_MEMORY_BACKGROUND));
    }
}
