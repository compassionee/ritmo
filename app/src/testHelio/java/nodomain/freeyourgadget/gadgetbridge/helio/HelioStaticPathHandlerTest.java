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
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/**
 * Checks the real Helio web files (src/helio/helio-app/static) against what the handler expects.
 */
public class HelioStaticPathHandlerTest {
    private static final File STATIC = new File("src/helio/helio-app/static");

    @Test
    public void realIndexLoadsTheShimBeforeAppJs() throws Exception {
        final byte[] html = Files.readAllBytes(new File(STATIC, "index.html").toPath());
        final String injected = new String(HelioStaticPathHandler.injectShim(html), StandardCharsets.UTF_8);
        final int shim = injected.indexOf(HelioStaticPathHandler.SHIM_SCRIPT_TAG);
        final int app = injected.indexOf(HelioStaticPathHandler.APP_SCRIPT_TAG);
        assertTrue(shim >= 0);
        assertEquals(shim + HelioStaticPathHandler.SHIM_SCRIPT_TAG.length(), app);
    }

    @Test(expected = IllegalStateException.class)
    public void indexWithoutAppJsIsAnError() {
        HelioStaticPathHandler.injectShim("<html></html>".getBytes(StandardCharsets.UTF_8));
    }

    @Test
    public void everyListedFileExistsAndSwIsNotServed() {
        final HelioStaticPathHandler handler = new HelioStaticPathHandler(path -> {
            final File f = path.startsWith("helio/static/")
                    ? new File(STATIC, path.substring("helio/static/".length()))
                    : new File("src/helio/assets", path);
            return new FileInputStream(f);
        });
        for (final String name : HelioStaticPathHandler.FILES.keySet()) {
            assertTrue(name, handler.handle(name) != null);
        }
        assertFalse(HelioStaticPathHandler.FILES.containsKey("sw.js"));
        assertNull(handler.handle("sw.js"));
        assertTrue(new File(STATIC, "sw.js").exists());  // present in the sources, but not served
    }

    @Test
    public void everyStaticFileIsServedExceptSw() throws Exception {
        // a new file in static/ (for example a language in i18n/) must be added to FILES too
        try (java.util.stream.Stream<java.nio.file.Path> all = Files.walk(STATIC.toPath())) {
            all.filter(Files::isRegularFile)
                    .map(p -> STATIC.toPath().relativize(p).toString().replace(File.separatorChar, '/'))
                    .filter(name -> !name.equals("sw.js"))
                    .forEach(name -> assertTrue(name, HelioStaticPathHandler.FILES.containsKey(name)));
        }
    }

    @Test
    public void cspIsTheSameAsTheServer() throws Exception {
        final String server = new String(Files.readAllBytes(new File(STATIC, "../helio_app.py").toPath()),
                StandardCharsets.UTF_8).replaceAll("\"\\s+\"", "");
        assertTrue(server.contains(HelioStaticPathHandler.CSP));
    }
}
