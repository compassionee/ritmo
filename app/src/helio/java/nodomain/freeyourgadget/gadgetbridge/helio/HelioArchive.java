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

/**
 * File types of the Helio data archive (.tgz, helio_transfer.py) in the Android file picker.
 */
final class HelioArchive {
    /**
     * Android's type for .tgz. With another type (application/gzip) the documents provider adds
     * the extension of that type and the file becomes ".tgz.gz".
     */
    static final String EXPORT_MIME_TYPE = "application/x-gtar-compressed";

    /**
     * What a .tgz can be called by the provider that holds it: the type from the extension, the
     * gzip types and, for files whose type is unknown (downloads, some cloud apps), the generic one.
     * Everything else (images, documents...) stays greyed out; the archive is checked anyway.
     */
    static final String[] IMPORT_MIME_TYPES = {
            EXPORT_MIME_TYPE,
            "application/gzip",
            "application/x-gzip",
            "application/x-compressed-tar",
            "application/x-tgz",
            "application/x-tar",
            "application/octet-stream",
    };

    private HelioArchive() {
    }

    /** Types a file manager may give to a food catalog package (.tar). */
    static final String[] CATALOG_MIME_TYPES = {
            "application/x-tar",
            "application/octet-stream",
    };

    /** Largest catalog package read from a file (the checks in Python allow less). */
    static final long MAX_CATALOG_BYTES = 1024L * 1024 * 1024;
}
