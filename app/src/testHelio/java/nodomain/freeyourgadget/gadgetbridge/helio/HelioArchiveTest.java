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

import org.junit.Test;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;

public class HelioArchiveTest {
    @Test
    public void importAcceptsWhatExportCreates() {
        final List<String> types = Arrays.asList(HelioArchive.IMPORT_MIME_TYPES);
        assertTrue(types.contains(HelioArchive.EXPORT_MIME_TYPE));
        // names file managers and cloud apps use for a .tgz or a .gz
        assertTrue(types.containsAll(Arrays.asList("application/x-gtar-compressed", "application/gzip",
                "application/x-gzip", "application/octet-stream")));
    }

    @Test
    public void importFilterIsNotAWildcard() {
        for (final String type : HelioArchive.IMPORT_MIME_TYPES) {
            assertFalse(type, type.contains("*"));
            assertTrue(type, type.startsWith("application/"));
        }
        assertEquals(HelioArchive.IMPORT_MIME_TYPES.length, new HashSet<>(Arrays.asList(HelioArchive.IMPORT_MIME_TYPES)).size());
    }

    @Test
    public void exportTypeKeepsTheTgzName() {
        // Android's type for the .tgz extension: with it the chosen name stays .tgz
        assertEquals("application/x-gtar-compressed", HelioArchive.EXPORT_MIME_TYPE);
    }
}
