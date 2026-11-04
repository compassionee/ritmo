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
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import android.app.Application;

import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

import java.io.File;

import nodomain.freeyourgadget.gadgetbridge.BuildConfig;
import nodomain.freeyourgadget.gadgetbridge.service.devices.huami.operations.fetch.HelioCaptureStore;

/**
 * Capture mode on Ritmo's side: in a build without -PhelioCapture=true (every release) the Profile does not show it
 * and its actions do nothing; the capture folder sits outside Ritmo's data, so the export and the backup never see it.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioCaptureTest {
    @Test
    public void releaseBuildsHaveNoCaptureMode() throws Exception {
        assertFalse(BuildConfig.HELIO_CAPTURE);
        final HelioActivityLifecycleTest.TestHelioActivity a =
                Robolectric.buildActivity(HelioActivityLifecycleTest.TestHelioActivity.class).setup().get();
        final JSONObject status = a.captureStatus();
        assertFalse(status.getBoolean("available"));
        assertEquals(1, status.length());  // nothing else about the folder
        a.captureAction(HelioGbActions.CAPTURE_ON);
        a.longWork().submit(() -> null).get();
        assertFalse(HelioCaptureStore.of(a).isOn());
        assertFalse(new File(a.getFilesDir(), HelioCaptureStore.DIR).exists());
    }

    @Test
    public void captureFolderIsNotInRitmosData() {
        final Application app = org.robolectric.RuntimeEnvironment.getApplication();
        final File capture = HelioCaptureStore.of(app).dir();
        final File state = HelioPythonBackend.stateDir(app);
        assertFalse(capture.getAbsolutePath().startsWith(state.getAbsolutePath() + File.separator));
        assertEquals(state.getParentFile(), capture.getParentFile());
        assertTrue(HelioGbActions.ALL.contains(HelioGbActions.CAPTURE_EXPORT));
        assertTrue(HelioTaskReporter.TASKS.contains("capture"));
    }
}
