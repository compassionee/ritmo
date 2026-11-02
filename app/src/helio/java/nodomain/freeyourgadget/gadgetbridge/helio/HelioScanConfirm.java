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
import androidx.annotation.Nullable;

import com.google.zxing.BarcodeFormat;

/**
 * Confirmation rule of the scanner: a code is accepted after the same digits, read as the same symbology, came
 * READS times in a row (4 for EAN-8 and UPC-E, which a piece of an EAN-13 can look like). Frames with nothing in
 * between do not count against it (on a phone most frames give nothing); another code starts again, and so does
 * a pause of more than MAX_GAP_MS without reads.
 */
final class HelioScanConfirm {
    static final int READS = 3, READS_SHORT = 4;
    static final long MAX_GAP_MS = 4000;

    @Nullable
    private HelioBarcodeDecoder.Read last;
    private long lastAt;
    private int count;

    static int readsNeeded(@NonNull final BarcodeFormat format) {
        return HelioBarcodeDecoder.shortCode(format) ? READS_SHORT : READS;
    }

    /**
     * @param read what one frame gave, null for nothing
     * @param now  time of the frame, milliseconds
     * @return the digits once confirmed (then the count starts again), otherwise null
     */
    @Nullable
    String offer(@Nullable final HelioBarcodeDecoder.Read read, final long now) {
        if (last != null && now - lastAt > MAX_GAP_MS) {
            last = null;
            count = 0;
        }
        if (read == null) {
            return null;
        }
        if (read.equals(last)) {
            count++;
        } else {
            last = read;
            count = 1;
        }
        lastAt = now;
        if (count >= readsNeeded(read.format)) {
            last = null;
            count = 0;
            return read.code;
        }
        return null;
    }
}
