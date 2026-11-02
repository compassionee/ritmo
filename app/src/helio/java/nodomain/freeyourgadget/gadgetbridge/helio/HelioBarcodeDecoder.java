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
import com.google.zxing.PlanarYUVLuminanceSource;
import com.google.zxing.ReaderException;
import com.google.zxing.Result;
import com.google.zxing.ResultPoint;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.oned.UPCEReader;

import java.util.Arrays;
import java.util.EnumMap;
import java.util.Map;
import java.util.Objects;

/**
 * Reads EAN/UPC product barcodes from the luminance (Y plane) of a camera frame with ZXing (Apache 2.0, no
 * Google Play Services). The frame is first turned upright (as the screen shows it); then the central band
 * (where the guide line is) is tried, then the whole frame, then the frame turned by 90 degrees, so the code
 * can also be held the other way. A read whose GS1 check digit does not match is dropped; a UPC-E code (number
 * system 0 only) is given in its 12-digit UPC-A form, the one product databases use. Only the digits leave this class; nothing
 * is sent anywhere.
 */
final class HelioBarcodeDecoder {
    /** Height of the central band tried first, as a fraction of the upright frame. */
    static final float BAND = 1 / 3f;

    /** A code read in one frame: the digits and the symbology they were read as. */
    static final class Read {
        @NonNull
        final String code;
        @NonNull
        final BarcodeFormat format;

        Read(@NonNull final String code, @NonNull final BarcodeFormat format) {
            this.code = code;
            this.format = format;
        }

        @Override
        public boolean equals(final Object o) {
            return o instanceof Read && ((Read) o).code.equals(code) && ((Read) o).format == format;
        }

        @Override
        public int hashCode() {
            return Objects.hash(code, format);
        }

        @NonNull
        @Override
        public String toString() {
            return format + ":" + code;
        }
    }

    /**
     * Reads found by ZXing and dropped, by check: wrong digits, proportions, quiet zones, other rows, contrast of
     * the digits (statistics).
     */
    static final int REJECT_DIGITS = 0, REJECT_SCALE = 1, REJECT_QUIET = 2, REJECT_ROWS = 3, REJECT_CONTRAST = 4;
    final int[] rejected = new int[5];

    private final MultiFormatReader reader = new MultiFormatReader();

    HelioBarcodeDecoder() {
        final Map<DecodeHintType, Object> hints = new EnumMap<>(DecodeHintType.class);
        hints.put(DecodeHintType.POSSIBLE_FORMATS,
                Arrays.asList(BarcodeFormat.EAN_13, BarcodeFormat.EAN_8, BarcodeFormat.UPC_A, BarcodeFormat.UPC_E));
        hints.put(DecodeHintType.TRY_HARDER, Boolean.TRUE);
        reader.setHints(hints);
    }

    /**
     * @param y               luminance bytes, rowStride bytes per row
     * @param rotationDegrees clockwise turn that makes the frame upright (ImageInfo.getRotationDegrees())
     * @return the code, or null if none was found
     */
    @Nullable
    Read read(@NonNull final byte[] y, final int width, final int height, final int rowStride, final int rotationDegrees) {
        byte[] data = pack(y, width, height, rowStride);
        int w = width, h = height;
        // 180 degrees needs no turn: the band is in the middle either way and ZXing also reads rows backwards
        if (rotationDegrees == 90 || rotationDegrees == 270) {
            data = rotate(data, width, height);
            w = height;
            h = width;
        }
        final int band = Math.max(1, Math.round(h * BAND));
        Read found = decodePacked(data, w, h, (h - band) / 2, band);
        if (found == null) {
            found = decodePacked(data, w, h, 0, h);
        }
        return found != null ? found : decodePacked(rotate(data, w, h), h, w, 0, w);
    }

    /** The digits only, for a frame already upright. */
    @Nullable
    String decode(@NonNull final byte[] y, final int width, final int height, final int rowStride) {
        final Read r = read(y, width, height, rowStride, 0);
        return r != null ? r.code : null;
    }

    /**
     * Rows top .. top + rows of a packed frame. ZXing finds a code on one row; it is kept only with light quiet
     * zones on both sides (quietZones), contrast all along the code (digitsContrastOk), for EAN-8 and UPC-E with
     * the proportions of a real code (guardScaleOk),
     * and if the rows above and below it (half and whole OTHER_ROWS of its length away) give the same code at
     * least once and another code never: the bars of a real code are tall, while a piece of a longer code, a row
     * crossing the bars at a slant or a row spoiled by noise give other digits there. Rows beyond the end of
     * the bars give nothing, which is no disagreement.
     */
    @Nullable
    private Read decodePacked(@NonNull final byte[] data, final int width, final int height, final int top, final int rows) {
        final Result result = decodeStrip(data, width, height, top, rows);
        if (result == null) {
            return null;
        }
        final Read found = checked(result);
        final ResultPoint[] ends = result.getResultPoints();
        if (found == null || ends == null || ends.length < 2) {
            return reject(REJECT_DIGITS);
        }
        final int row = top + Math.round(ends[0].getY());
        if (shortCode(found.format) && !guardScaleOk(data, width, height, row, ends[0].getX(), ends[1].getX(), found.format)) {
            return reject(REJECT_SCALE);
        }
        if (!quietZones(data, width, height, row, ends[0].getX(), ends[1].getX(), found.format)) {
            return reject(REJECT_QUIET);
        }
        if (!digitsContrastOk(data, width, height, row, ends[0].getX(), ends[1].getX(), found.format)) {
            return reject(REJECT_CONTRAST);
        }
        final int step = Math.max(4, Math.round(Math.abs(ends[1].getX() - ends[0].getX()) * OTHER_ROWS));
        int agree = 0;
        for (final int other : new int[]{row - step / 2, row + step / 2, row - step, row + step}) {
            if (other - STRIP / 2 < 0 || other + STRIP / 2 >= height) {  // the whole strip in the frame
                continue;
            }
            final Result again = decodeRow(data, width, height, other);
            final Read same = again != null ? checked(again) : null;
            if (same != null) {
                if (!found.equals(same)) {
                    return reject(REJECT_ROWS);
                }
                agree++;
            }
        }
        return agree > 0 ? found : reject(REJECT_ROWS);
    }

    @Nullable
    private Read reject(final int why) {
        rejected[why]++;
        return null;
    }

    /** EAN-8 and UPC-E: short codes that a piece of a longer one can look like. */
    static boolean shortCode(@NonNull final BarcodeFormat format) {
        return format == BarcodeFormat.EAN_8 || format == BarcodeFormat.UPC_E;
    }

    /** Distance between the guard centres in modules (ZXing's result points). */
    private static float guardSpan(@NonNull final BarcodeFormat format) {
        return format == BarcodeFormat.EAN_8 ? 64 : format == BarcodeFormat.UPC_E ? 46.5f : 92;
    }

    /** How far the module measured on the start guard may be from the one of the whole code (perspective, blur). */
    static final float SCALE_LOW = 0.75f, SCALE_HIGH = 1.4f;

    /**
     * EAN-8 and UPC-E only: the start guard (bar, space, bar: 3 modules) measured on the row must be as wide as 3
     * modules of the whole code. ZXing reads each digit from relative widths, so it can find a short code in a
     * piece of a longer one whose digits are wider than its guard (a UPC-E inside an EAN-13 next to a glare);
     * a real code keeps its proportions even photographed at a slant. Tolerant on purpose: the first version
     * compared every module and dropped almost every real read.
     */
    static boolean guardScaleOk(@NonNull final byte[] data, final int width, final int height, final int row, final float start,
                                final float end, @NonNull final BarcodeFormat format) {
        final float ratio = guardRatio(data, width, height, row, start, end, format);
        return ratio >= SCALE_LOW && ratio <= SCALE_HIGH;
    }

    /** Width of the start guard over 3 modules of the whole code; NaN if it cannot be measured. */
    static float guardRatio(@NonNull final byte[] data, final int width, final int height, final int row, final float start,
                            final float end, @NonNull final BarcodeFormat format) {
        final float module = Math.abs(end - start) / guardSpan(format);
        final int dir = (int) Math.signum(end - start);
        final int threshold = codeThreshold(data, width, height, row, start, end);
        if (module <= 0 || dir == 0 || threshold < 0) {
            return Float.NaN;
        }
        // the centre of the start guard is in its space; on each side a bar, then light
        final int centre = (int) Math.floor(start);
        final int outer = barEnd(data, width, height, row, centre, -dir, module, threshold);
        final int inner = barEnd(data, width, height, row, centre, dir, module, threshold);
        if (outer < 0 || inner < 0) {
            return Float.NaN;
        }
        return (Math.abs(inner - outer) - 1) / 3f / module;
    }

    /** From x along dir: past the light, past the next bar; the first light pixel after it, -1 if not found nearby. */
    private static int barEnd(@NonNull final byte[] data, final int width, final int height, final int row, final int from,
                              final int dir, final float module, final int threshold) {
        final int limit = Math.max(3, Math.round(2.5f * module));
        int x = from, n = 0;
        while (x >= 0 && x < width && luminance(data, width, height, row, x) >= threshold && n++ < limit) {
            x += dir;
        }
        n = 0;
        while (x >= 0 && x < width && luminance(data, width, height, row, x) < threshold && n++ < limit) {
            x += dir;
        }
        return x >= 0 && x < width && luminance(data, width, height, row, x) >= threshold ? x : -1;
    }

    /** Midway between the dark and the light of the code on its row, -1 without contrast. */
    private static int codeThreshold(@NonNull final byte[] data, final int width, final int height, final int row,
                                     final float start, final float end) {
        final int lo = Math.max(0, Math.round(Math.min(start, end))), hi = Math.min(width - 1, Math.round(Math.max(start, end)));
        if (hi <= lo) {
            return -1;
        }
        final int[] levels = new int[hi - lo + 1];
        for (int x = lo; x <= hi; x++) {
            levels[x - lo] = luminance(data, width, height, row, x);
        }
        Arrays.sort(levels);
        final int dark = levels[levels.length / 10], light = levels[levels.length * 9 / 10];
        return light - dark < 20 ? -1 : (dark + light) / 2;
    }

    /** Least contrast in any 5 modules of the code, as a share of the contrast of the whole code. */
    static final float LOCAL_CONTRAST = 0.45f;

    /**
     * True if every 5 modules of the code (from guard to guard; any 5 modules hold a bar and a space, no element
     * is wider than 4) have bars and spaces as distinct as the rest of the code. A glare that washes out part of
     * a digit can turn it into another digit and, through the parity of the left half, change the first digit
     * too, with a check digit that still matches; the washed part has little contrast left.
     */
    static boolean digitsContrastOk(@NonNull final byte[] data, final int width, final int height, final int row,
                                    final float start, final float end, @NonNull final BarcodeFormat format) {
        final int lo = Math.max(0, Math.round(Math.min(start, end))), hi = Math.min(width - 1, Math.round(Math.max(start, end)));
        if (hi <= lo) {
            return false;
        }
        final int[] line = new int[hi - lo + 1];
        for (int x = lo; x <= hi; x++) {
            line[x - lo] = luminance(data, width, height, row, x);
        }
        final int[] sorted = line.clone();
        Arrays.sort(sorted);
        final int code = sorted[sorted.length * 9 / 10] - sorted[sorted.length / 10];
        if (code < 20) {
            return false;
        }
        final float module = (hi - lo) / guardSpan(format);
        final int window = Math.max(3, Math.round(5 * module)), step = Math.max(1, Math.round(module));
        for (int x = 0; x + window <= line.length; x += step) {
            int min = 255, max = 0;
            for (int i = x; i < x + window; i++) {
                min = Math.min(min, line[i]);
                max = Math.max(max, line[i]);
            }
            if (max - min < LOCAL_CONTRAST * code) {
                return false;
            }
        }
        return true;
    }

    /** Light modules required on each side of the code, outside its guard bars (GS1 asks for 7 to 11): more for
     *  the short codes. */
    static final int QUIET_MODULES = 6, QUIET_MODULES_SHORT = 8;

    /**
     * True if, on the row of the code, the modules outside each guard are light and inside the frame. A piece of a
     * longer code (EAN-8 or UPC-E inside an EAN-13) has the other bars right next to it.
     *
     * @param start centre of the start guard (ZXing's first result point), end centre of the end guard
     */
    static boolean quietZones(@NonNull final byte[] data, final int width, final int height, final int row, final float start,
                              final float end, @NonNull final BarcodeFormat format) {
        final float module = Math.abs(end - start) / guardSpan(format);
        final float dir = Math.signum(end - start);
        final int threshold = codeThreshold(data, width, height, row, start, end);
        if (module <= 0 || dir == 0 || threshold < 0) {
            return false;
        }
        final int quiet = shortCode(format) ? QUIET_MODULES_SHORT : QUIET_MODULES;
        final float endGuard = format == BarcodeFormat.UPC_E ? 3 : 1.5f;  // half the width of the end guard
        return light(data, width, height, row, start - dir * 1.5f * module, -dir, module, quiet, threshold)
                && light(data, width, height, row, end + dir * endGuard * module, dir, module, quiet, threshold);
    }

    /**
     * From one module to quiet modules beyond the outer edge of a guard (the first module may be grey with
     * blur): inside the frame, with no dark run of a module.
     */
    private static boolean light(@NonNull final byte[] data, final int width, final int height, final int row, final float edge,
                                 final float dir, final float module, final int quiet, final int threshold) {
        final int from = Math.round(edge + dir * module), to = Math.round(edge + dir * quiet * module);
        if (Math.min(from, to) < 0 || Math.max(from, to) >= width) {
            return false;
        }
        final int longest = Math.max(1, Math.round(0.7f * module));
        int run = 0;
        for (int x = Math.min(from, to); x <= Math.max(from, to); x++) {
            run = luminance(data, width, height, row, x) < threshold ? run + 1 : 0;
            if (run >= longest) {
                return false;
            }
        }
        return true;
    }

    /** Mean of the pixel and the two rows above and below it, against the noise of the sensor. */
    private static int luminance(@NonNull final byte[] data, final int width, final int height, final int row, final int x) {
        int sum = 0, n = 0;
        for (int r = Math.max(0, row - 2); r <= Math.min(height - 1, row + 2); r++) {
            sum += data[r * width + x] & 0xff;
            n++;
        }
        return sum / n;
    }

    /** Distance of the rows that must agree, as a fraction of the distance between the guard bars of the code. */
    static final float OTHER_ROWS = 0.15f;
    /** Rows around each of those rows (averaged, then one by one). */
    private static final int STRIP = 7;

    /**
     * The code on a row: first the mean of the STRIP rows around it (against the noise of the sensor), then each of
     * those rows (a code photographed at a slant can be read on some rows only).
     */
    @Nullable
    private Result decodeRow(@NonNull final byte[] data, final int width, final int height, final int row) {
        final Result mean = decodeMeanRow(data, width, height, row);
        return mean != null ? mean : decodeStrip(data, width, height, row - STRIP / 2, STRIP);
    }

    @Nullable
    private Result decodeMeanRow(@NonNull final byte[] data, final int width, final int height, final int row) {
        final byte[] mean = new byte[width];
        for (int x = 0; x < width; x++) {
            int sum = 0;
            for (int r = row - STRIP / 2; r <= row + STRIP / 2; r++) {
                sum += data[r * width + x] & 0xff;
            }
            mean[x] = (byte) (sum / STRIP);
        }
        return decodeStrip(mean, width, 1, 0, 1);
    }

    @Nullable
    private Result decodeStrip(@NonNull final byte[] data, final int width, final int height, final int top, final int rows) {
        try {
            final PlanarYUVLuminanceSource source = new PlanarYUVLuminanceSource(data, width, height, 0, top, width, rows, false);
            return reader.decodeWithState(new BinaryBitmap(new HybridBinarizer(source)));
        } catch (final ReaderException e) {
            return null;
        } finally {
            reader.reset();
        }
    }

    /** The read with its check digit verified again, UPC-E as UPC-A; null for anything else. */
    @Nullable
    static Read checked(@NonNull final Result result) {
        String text = result.getText();
        final BarcodeFormat format = result.getBarcodeFormat();
        if (text == null || format == null) {
            return null;
        }
        final int length;
        switch (format) {
            case EAN_13:
                length = 13;
                break;
            case EAN_8:
                length = 8;
                break;
            case UPC_A:
                length = 12;
                break;
            case UPC_E:
                // number system 0 only: system 1 is hardly used, and the left half of an EAN-13 can be one bit
                // for bit (an EAN-13 starting with 7 has the parity of a UPC-E of system 1)
                if (!text.matches("0\\d{7}")) {
                    return null;
                }
                text = UPCEReader.convertUPCEtoUPCA(text);
                length = 12;
                break;
            default:
                return null;
        }
        return text.length() == length && text.matches("\\d+") && gs1CheckOk(text) ? new Read(text, format) : null;
    }

    /**
     * GS1 check digit of EAN-8, UPC-A, EAN-13 and GTIN-14 (as hc.gs1_check_ok in Helio): from the right,
     * weights 3, 1, 3, ... on the digits before the last one; the last one makes the sum a multiple of 10.
     */
    static boolean gs1CheckOk(@NonNull final String digits) {
        if (!digits.matches("\\d{8,14}")) {
            return false;
        }
        int sum = 0;
        for (int i = digits.length() - 2, weight = 3; i >= 0; i--, weight = 4 - weight) {
            sum += (digits.charAt(i) - '0') * weight;
        }
        return (10 - sum % 10) % 10 == digits.charAt(digits.length() - 1) - '0';
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
