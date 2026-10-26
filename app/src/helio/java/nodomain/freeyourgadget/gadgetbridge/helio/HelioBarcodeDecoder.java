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
import com.google.zxing.BinaryBitmap;
import com.google.zxing.DecodeHintType;
import com.google.zxing.MultiFormatReader;
import com.google.zxing.NotFoundException;
import com.google.zxing.PlanarYUVLuminanceSource;
import com.google.zxing.ReaderException;
import com.google.zxing.Result;
import com.google.zxing.common.HybridBinarizer;

import java.util.Arrays;
import java.util.EnumMap;
import java.util.Map;

/**
 * Reads EAN/UPC product barcodes from the luminance (Y plane) of a camera frame with ZXing (Apache 2.0, no
 * Google Play Services). The frame is tried as it is and turned by 90 degrees, so the code can be held either
 * way. Only the digits leave this class; nothing is sent anywhere.
 */
final class HelioBarcodeDecoder {
    private final MultiFormatReader reader = new MultiFormatReader();

    HelioBarcodeDecoder() {
        final Map<DecodeHintType, Object> hints = new EnumMap<>(DecodeHintType.class);
        hints.put(DecodeHintType.POSSIBLE_FORMATS,
                Arrays.asList(BarcodeFormat.EAN_13, BarcodeFormat.EAN_8, BarcodeFormat.UPC_A, BarcodeFormat.UPC_E));
        hints.put(DecodeHintType.TRY_HARDER, Boolean.TRUE);
        reader.setHints(hints);
    }

    /**
     * @param y         luminance bytes, rowStride bytes per row
     * @return the digits of the code, or null if none was found
     */
    @Nullable
    String decode(@NonNull final byte[] y, final int width, final int height, final int rowStride) {
        final byte[] packed = pack(y, width, height, rowStride);
        final String found = decodePacked(packed, width, height);
        return found != null ? found : decodePacked(rotate(packed, width, height), height, width);
    }

    @Nullable
    private String decodePacked(@NonNull final byte[] data, final int width, final int height) {
        try {
            final PlanarYUVLuminanceSource source = new PlanarYUVLuminanceSource(data, width, height, 0, 0, width, height, false);
            final Result result = reader.decodeWithState(new BinaryBitmap(new HybridBinarizer(source)));
            final String text = result.getText();
            return text != null && text.matches("\\d{6,14}") ? text : null;
        } catch (final NotFoundException e) {
            return null;
        } catch (final ReaderException e) {
            return null;
        } finally {
            reader.reset();
        }
    }

    /** Removes the padding at the end of each row (rowStride > width). */
    @NonNull
    static byte[] pack(@NonNull final byte[] y, final int width, final int height, final int rowStride) {
        if (rowStride == width && y.length == width * height) {
            return y;
        }
        final byte[] out = new byte[width * height];
        for (int r = 0; r < height; r++) {
            System.arraycopy(y, r * rowStride, out, r * width, width);
        }
        return out;
    }

    /** Turns the image by 90 degrees clockwise: width x height becomes height x width. */
    @NonNull
    static byte[] rotate(@NonNull final byte[] data, final int width, final int height) {
        final byte[] out = new byte[data.length];
        for (int r = 0; r < height; r++) {
            for (int c = 0; c < width; c++) {
                out[c * height + (height - 1 - r)] = data[r * width + c];
            }
        }
        return out;
    }
}
