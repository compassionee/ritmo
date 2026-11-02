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

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * How the barcode scans of this process went (Profile > About > Startup times), to see whether the checks of the
 * scanner are too strict: frames and milliseconds to accept a code, reads dropped by each check. Only in memory,
 * the last MAX scans; no digits of the codes, nothing is written or sent anywhere.
 */
final class HelioScanStats {
    static final int MAX = 20;
    private static final List<JSONObject> scans = new ArrayList<>();

    private HelioScanStats() {
    }

    static synchronized void add(@NonNull final JSONObject scan) {
        if (scans.size() >= MAX) {
            scans.remove(0);
        }
        scans.add(scan);
    }

    @NonNull
    static synchronized JSONArray json() {
        return new JSONArray(scans);
    }

    /** Tests only. */
    static synchronized void clear() {
        scans.clear();
    }

    /** One scan, counted by the analysis thread and saved when the scanner closes. */
    static final class Scan {
        private long cameraAsked, firstFrame, last;
        private int frames, passed;
        private long decodeMs;
        @Nullable
        private String size;
        @Nullable
        private BarcodeFormat format;
        private final int[] rejected = new int[5];

        synchronized void cameraAsked(final long at) {
            cameraAsked = at;
        }

        /** One analysed frame: upright size, time to decode it, the read that passed the checks (or null). */
        synchronized void frame(final long start, final long end, @NonNull final String uprightSize,
                                @Nullable final HelioBarcodeDecoder.Read read, @NonNull final int[] decoderRejected) {
            if (frames == 0) {
                firstFrame = start;
                size = uprightSize;
            }
            frames++;
            decodeMs += end - start;
            last = end;
            if (read != null) {
                passed++;
            }
            System.arraycopy(decoderRejected, 0, rejected, 0, rejected.length);
        }

        synchronized void accepted(@NonNull final BarcodeFormat f) {
            format = f;
        }

        /** @param result "ok", "cancel", "permission" or "camera" */
        @NonNull
        synchronized JSONObject json(@NonNull final String result, final long end) {
            try {
                final JSONObject o = new JSONObject().put("result", result).put("frames", frames).put("passed", passed)
                        .put("digits", rejected[HelioBarcodeDecoder.REJECT_DIGITS])
                        .put("scale", rejected[HelioBarcodeDecoder.REJECT_SCALE])
                        .put("quiet", rejected[HelioBarcodeDecoder.REJECT_QUIET])
                        .put("rows", rejected[HelioBarcodeDecoder.REJECT_ROWS])
                        .put("contrast", rejected[HelioBarcodeDecoder.REJECT_CONTRAST]);
                if (frames > 0) {
                    o.put("ms", ("ok".equals(result) ? last : end) - firstFrame)
                            .put("frame_ms", (double) decodeMs / frames).put("size", size);
                    if (cameraAsked > 0) {
                        o.put("camera_ms", firstFrame - cameraAsked);
                    }
                }
                if (format != null) {
                    o.put("format", format.name());
                }
                return o;
            } catch (final JSONException e) {
                throw new IllegalStateException(e);
            }
        }
    }
}
