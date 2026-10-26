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
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import android.app.Application;
import android.content.Context;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import java.io.File;
import java.io.InputStream;
import java.nio.file.Files;

/** The food catalog: its folder is separate from the user's data; the shipped package is copied once per APK. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioCatalogTest {
    @Test
    public void catalogFolderIsNotInsideTheData() {
        final Context ctx = RuntimeEnvironment.getApplication();
        final File state = HelioPythonBackend.stateDir(ctx);
        final File catalog = HelioPythonBackend.catalogDir(ctx);
        assertEquals(state.getParentFile(), catalog.getParentFile());
        assertNotEquals(state, catalog);
        assertFalse(catalog.getPath().startsWith(state.getPath() + File.separator));
        assertFalse(HelioPythonBackend.bundledCatalog(ctx).getPath().startsWith(state.getPath() + File.separator));
    }

    @Test
    public void bundledCatalogIsCopiedOnce() throws Exception {
        final Context ctx = RuntimeEnvironment.getApplication();
        final File dest = HelioPythonBackend.bundledCatalog(ctx);
        //noinspection ResultOfMethodCallIgnored
        dest.delete();
        HelioPythonBackend.copyBundledCatalog(ctx);
        assertTrue(dest.isFile());
        final byte[] asset;
        try (InputStream in = ctx.getAssets().open(HelioPythonBackend.BUNDLED_CATALOG_ASSET)) {
            asset = in.readAllBytes();
        }
        assertArrayEquals(asset, Files.readAllBytes(dest.toPath()));
        assertTrue(asset.length > 100_000);
        // already there and newer than the APK: not copied again
        assertTrue(dest.setLastModified(System.currentTimeMillis() + 60_000));
        final long mtime = dest.lastModified();
        Files.write(dest.toPath(), new byte[]{1});
        assertTrue(dest.setLastModified(mtime));
        HelioPythonBackend.copyBundledCatalog(ctx);
        assertEquals(1, dest.length());
    }
}
