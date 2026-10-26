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

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.Manifest;
import android.app.Activity;
import android.app.Application;
import android.content.Intent;
import android.content.pm.PackageManager;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.oned.EAN13Writer;
import com.google.zxing.oned.UPCAWriter;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;

import java.util.Random;

/**
 * Barcode scanner: ZXing reads EAN/UPC codes from camera-like frames; the camera permission is asked only by
 * the scanner (the first time the user scans), never when Helio starts; the result goes to the page as JSON.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioScanTest {
    /** Luminance of a barcode image, with padding at the end of each row like a camera plane. */
    private static byte[] frame(final BitMatrix m, final int rowStride) {
        final byte[] y = new byte[rowStride * m.getHeight()];
        for (int r = 0; r < m.getHeight(); r++) {
            for (int c = 0; c < rowStride; c++) {
                y[r * rowStride + c] = (byte) (c < m.getWidth() && m.get(c, r) ? 0 : 255);
            }
        }
        return y;
    }

    @Test
    public void readsEan13AndUpcA() throws Exception {
        final HelioBarcodeDecoder d = new HelioBarcodeDecoder();
        final BitMatrix ean = new EAN13Writer().encode("4006381333931", BarcodeFormat.EAN_13, 400, 120);
        assertEquals("4006381333931", d.decode(frame(ean, 432), 400, 120, 432));
        final BitMatrix upc = new UPCAWriter().encode("036000291452", BarcodeFormat.UPC_A, 400, 120);
        final String got = d.decode(frame(upc, 400), 400, 120, 400);
        assertTrue(got, "036000291452".equals(got) || "0036000291452".equals(got));
    }

    @Test
    public void readsACodeHeldTheOtherWay() throws Exception {
        final BitMatrix ean = new EAN13Writer().encode("4006381333931", BarcodeFormat.EAN_13, 400, 120);
        final byte[] turned = HelioBarcodeDecoder.rotate(frame(ean, 400), 400, 120);  // 120 x 400: vertical bars
        assertEquals("4006381333931", new HelioBarcodeDecoder().decode(turned, 120, 400, 120));
    }

    @Test
    public void noCodeInNoise() {
        final byte[] y = new byte[320 * 240];
        new Random(1).nextBytes(y);
        assertNull(new HelioBarcodeDecoder().decode(y, 320, 240, 320));
    }

    @Test
    public void packAndRotate() {
        final byte[] y = {1, 2, 9, 3, 4, 9};  // 2 x 2 with one padding byte per row
        assertArrayEquals(new byte[]{1, 2, 3, 4}, HelioBarcodeDecoder.pack(y, 2, 2, 3));
        assertArrayEquals(new byte[]{3, 1, 4, 2}, HelioBarcodeDecoder.rotate(new byte[]{1, 2, 3, 4}, 2, 2));
    }

    /** The scanner without a real camera: counts the starts. */
    public static class TestScanActivity extends HelioScanActivity {
        int starts;

        @Override
        void startCamera() {
            starts++;
        }
    }

    private static void cameraFeature() {
        shadowOf(RuntimeEnvironment.getApplication().getPackageManager()).setSystemFeature(PackageManager.FEATURE_CAMERA_ANY, true);
    }

    @Test
    public void permissionAskedAtTheFirstScan() {
        cameraFeature();
        final ActivityController<TestScanActivity> c = Robolectric.buildActivity(TestScanActivity.class).setup();
        assertNotNull(shadowOf(c.get()).getLastRequestedPermission());
        assertArrayEquals(new String[]{Manifest.permission.CAMERA}, shadowOf(c.get()).getLastRequestedPermission().requestedPermissions);
        assertEquals(0, c.get().starts);
    }

    @Test
    public void noRequestWhenAlreadyAllowed() {
        cameraFeature();
        shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.CAMERA);
        final ActivityController<TestScanActivity> c = Robolectric.buildActivity(TestScanActivity.class).setup();
        assertNull(shadowOf(c.get()).getLastRequestedPermission());
        assertEquals(1, c.get().starts);
    }

    @Test
    public void noCameraEndsWithAnError() {
        final ActivityController<TestScanActivity> c = Robolectric.buildActivity(TestScanActivity.class).setup();
        assertTrue(c.get().isFinishing());
        assertEquals(Activity.RESULT_CANCELED, shadowOf(c.get()).getResultCode());
        assertEquals(HelioScanActivity.ERROR_CAMERA, shadowOf(c.get()).getResultIntent().getStringExtra(HelioScanActivity.EXTRA_ERROR));
    }

    @Test
    public void helioDoesNotAskForTheCameraWhenItStarts() {
        final ActivityController<HelioActivityLifecycleTest.TestHelioActivity> c =
                Robolectric.buildActivity(HelioActivityLifecycleTest.TestHelioActivity.class).setup();
        assertNull(shadowOf(c.get()).getLastRequestedPermission());
        assertFalse(c.get().isFinishing());
    }

    @Test
    public void resultForThePage() throws Exception {
        final Intent ok = new Intent().putExtra(HelioScanActivity.EXTRA_CODE, "8001234567890");
        assertEquals("8001234567890", HelioActivity.scanResult(Activity.RESULT_OK, ok).getString("code"));
        final Intent denied = new Intent().putExtra(HelioScanActivity.EXTRA_ERROR, HelioScanActivity.ERROR_PERMISSION);
        assertEquals("permission", HelioActivity.scanResult(Activity.RESULT_CANCELED, denied).getString("error"));
        assertEquals("cancel", HelioActivity.scanResult(Activity.RESULT_CANCELED, null).getString("error"));
        final Intent odd = new Intent().putExtra(HelioScanActivity.EXTRA_CODE, "12; alert(1)");
        assertFalse(HelioActivity.scanResult(Activity.RESULT_OK, odd).has("code"));
        assertTrue(HelioGbActions.ALL.contains(HelioGbActions.SCAN));
    }
}
