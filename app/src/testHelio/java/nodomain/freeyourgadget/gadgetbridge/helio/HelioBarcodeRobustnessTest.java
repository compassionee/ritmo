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
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.MultiFormatWriter;
import com.google.zxing.WriterException;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.oned.UPCEReader;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

/**
 * The scanner never accepts a wrong code: EAN-13, EAN-8, UPC-A and UPC-E generated with ZXing are drawn into
 * camera-like frames and spoiled (blur, noise, rotation, perspective, glare, the code cut by the edge of the
 * frame, and these together); five frames of each scene go through the decoder and the confirmation rule.
 * Single frames may rarely give other digits, the confirmed code never. Mild frames must be read, so the test
 * cannot pass by reading nothing.
 * <p>
 * Not covered, on purpose: a crisp white mask ending exactly on a digit boundary leaves a part of an EAN-13 that
 * is, bar for bar, a valid EAN-8 with its quiet zone; no check on the image can tell it from a real one.
 */
public class HelioBarcodeRobustnessTest {
    private static final int W = 640, H = 480;

    /** Contents, symbology, digits expected from the decoder. */
    private static final Object[][] CODES = {
            {"4006381333931", BarcodeFormat.EAN_13, "4006381333931"},
            {"3017620422003", BarcodeFormat.EAN_13, "3017620422003"},
            {"8001234567897", BarcodeFormat.EAN_13, "8001234567897"},
            {"5901234123457", BarcodeFormat.EAN_13, "5901234123457"},
            {"7613035974685", BarcodeFormat.EAN_13, "7613035974685"},
            {"96385074", BarcodeFormat.EAN_8, "96385074"},
            {"73513537", BarcodeFormat.EAN_8, "73513537"},
            {"20570910", BarcodeFormat.EAN_8, "20570910"},
            {"036000291452", BarcodeFormat.UPC_A, "036000291452"},
            {"012345678905", BarcodeFormat.UPC_A, "012345678905"},
            {"01234565", BarcodeFormat.UPC_E, UPCEReader.convertUPCEtoUPCA("01234565")},
            {"04252614", BarcodeFormat.UPC_E, UPCEReader.convertUPCEtoUPCA("04252614")},
    };

    /** How a frame is spoiled. */
    private static final class Spoil {
        final String name;
        final double px;       // pixels per module
        final double angle;    // degrees
        final double from, to; // part of the code inside the frame, 0..1 of its width with the quiet zones (the rest
                               // beyond the edge of the frame: framed by half)
        final int blur;        // box blur radius, pixels
        final double noise;    // standard deviation of the noise, levels of 255
        final double contrast; // 1 = black on white
        final double slant;    // perspective: modules 1 + slant times wider at one end, 1 - slant at the other
        double glareAt = -1;   // centre of a glare (bright spot with soft edges) across the code, 0..1; -1 none
        double glareModules;   // its radius, modules

        Spoil glare(final double at, final double modules) {
            glareAt = at;
            glareModules = modules;
            return this;
        }

        Spoil(final String name, final double px, final double angle, final double from, final double to, final int blur,
              final double noise, final double contrast) {
            this(name, px, angle, from, to, blur, noise, contrast, 0);
        }

        Spoil(final String name, final double px, final double angle, final double from, final double to, final int blur,
              final double noise, final double contrast, final double slant) {
            this.slant = slant;
            this.name = name;
            this.px = px;
            this.angle = angle;
            this.from = from;
            this.to = to;
            this.blur = blur;
            this.noise = noise;
            this.contrast = contrast;
        }

        @Override
        public String toString() {
            return name;
        }
    }

    private static final List<Spoil> MILD = new ArrayList<>();
    private static final List<Spoil> ALL = new ArrayList<>();

    static {
        MILD.add(new Spoil("clean", 3, 0, 0, 1, 0, 0, 1));
        MILD.add(new Spoil("noise 12", 3, 0, 0, 1, 0, 12, 1));
        MILD.add(new Spoil("blur 1", 3, 0, 0, 1, 1, 0, 1));
        MILD.add(new Spoil("turned 6°", 3, 6, 0, 1, 0, 4, 1));
        MILD.add(new Spoil("small and grey", 2.2, 0, 0, 1, 0, 6, 0.6));
        MILD.add(new Spoil("photographed at a slant", 3, 0, 0, 1, 1, 4, 1, 0.2));
        ALL.addAll(MILD);
        for (final int blur : new int[]{2, 3, 4, 6}) {
            ALL.add(new Spoil("blur " + blur, 3, 0, 0, 1, blur, 3, 1));
        }
        for (final double noise : new double[]{30, 50, 80}) {
            ALL.add(new Spoil("noise " + noise, 3, 0, 0, 1, 0, noise, 0.8));
        }
        for (final double angle : new double[]{-20, -12, 15, 30, 45, 80, 90, 135, 180}) {
            ALL.add(new Spoil("turned " + angle + "°", 3, angle, 0, 1, 0, 6, 1));
        }
        for (final double[] cut : new double[][]{{0, .5}, {.5, 1}, {0, .55}, {.45, 1}, {0, .6}, {.4, 1}, {0, .7}, {.3, 1},
                {.25, .75}, {0, .8}, {.2, 1}, {0, .9}, {.1, 1}, {0, .65}, {.35, 1}, {0, .75}, {.25, 1}}) {
            ALL.add(new Spoil("cut " + cut[0] + "-" + cut[1], 3, 0, cut[0], cut[1], 0, 4, 1));
            ALL.add(new Spoil("cut " + cut[0] + "-" + cut[1] + ", blur 2, noise 25", 3, 0, cut[0], cut[1], 2, 25, 0.8));
            ALL.add(new Spoil("cut " + cut[0] + "-" + cut[1] + ", turned 8°", 3.5, 8, cut[0], cut[1], 1, 10, 1));
        }
        ALL.add(new Spoil("blur 3, noise 40, turned 10°", 3, 10, 0, 1, 3, 40, 0.7));
        ALL.add(new Spoil("tiny", 1.2, 0, 0, 1, 0, 8, 1));
        ALL.add(new Spoil("tiny and blurred", 1.5, 0, 0, 1, 2, 8, 1));
        for (final double at : new double[]{0.2, 0.35, 0.5, 0.65, 0.8}) {
            for (final double r : new double[]{4, 8, 14}) {
                ALL.add(new Spoil("glare at " + at + ", " + r + " modules", 3, 0, 0, 1, 1, 5, 1).glare(at, r));
                ALL.add(new Spoil("glare at " + at + ", " + r + " modules, turned 8°", 3, 8, 0, 1, 1, 8, 1).glare(at, r));
            }
        }
        for (final double slant : new double[]{-0.3, -0.15, 0.15, 0.3}) {
            ALL.add(new Spoil("slant " + slant, 3, 0, 0, 1, 1, 6, 1, slant));
            ALL.add(new Spoil("slant " + slant + ", cut 0-0.7", 3, 0, 0, 0.7, 1, 6, 1, slant));
            ALL.add(new Spoil("slant " + slant + ", cut 0.3-1", 3, 0, 0.3, 1, 1, 6, 1, slant));
        }
    }

    private static BitMatrix modules(final String code, final BarcodeFormat format) throws WriterException {
        return new MultiFormatWriter().encode(code, format, 0, 1);  // 1 pixel per module, with the quiet zones
    }

    /** Luminance of an upright W x H frame with the code in the middle, spoiled. */
    static byte[] frame(final BitMatrix m, final Spoil s, final long seed) {
        return frame(m, s, seed, 0);
    }

    /** The same, with the code moved down by dy pixels (up if negative). */
    static byte[] frame(final BitMatrix m, final Spoil s, final long seed, final int dy) {
        final Random rnd = new Random(seed);
        final double lo = 128 - 110 * s.contrast, hi = 128 + 100 * s.contrast;
        // framed by half: the cut is the edge of the frame (both cuts: zoomed in until that part fills it)
        final double px = s.from > 0 && s.to < 1 ? W / ((s.to - s.from) * m.getWidth()) : s.px;
        final double codeW = m.getWidth() * px, codeH = Math.min(H * 0.6, codeW * 0.6);
        final double x0 = s.from > 0 ? (0.5 - s.from) * codeW : s.to < 1 ? W - (s.to - 0.5) * codeW : W / 2.0;
        final double cx = x0 + rnd.nextGaussian() * 3, cy = H / 2.0 + dy + rnd.nextGaussian() * 3;
        final double a = Math.toRadians(s.angle + rnd.nextGaussian() * 0.3), cos = Math.cos(a), sin = Math.sin(a);
        final double[] img = new double[W * H];
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                double sum = 0;
                for (int k = 0; k < 3; k++) {  // three samples per pixel across the bars: soft edges
                    final double qx = x + (k + 0.5) / 3 - cx, qy = y + 0.5 - cy;
                    final double u0 = (qx * cos + qy * sin) / codeW + 0.5, v = (-qx * sin + qy * cos) / codeH + 0.5;
                    final double u = u0 + s.slant * u0 * (1 - u0);  // local scale 1 + slant at the start, 1 - slant at the end
                    boolean dark = false;
                    if (u >= 0 && u < 1 && v >= 0 && v < 1) {
                        dark = m.get((int) (u * m.getWidth()), 0);
                    }
                    double level = dark ? lo : hi;
                    if (s.glareAt >= 0) {  // the glare lifts everything towards white, softly
                        final double d = (u - s.glareAt) * m.getWidth() / s.glareModules, e = (v - 0.5) * 2;
                        level += (255 - level) * Math.exp(-(d * d + e * e));
                    }
                    sum += level;
                }
                img[y * W + x] = sum / 3;
            }
        }
        final double[] blurred = s.blur > 0 ? blur(img, s.blur) : img;
        final byte[] out = new byte[W * H];
        for (int i = 0; i < out.length; i++) {
            final double v = blurred[i] + rnd.nextGaussian() * s.noise;
            out[i] = (byte) Math.max(0, Math.min(255, Math.round(v)));
        }
        return out;
    }

    private static double[] blur(final double[] img, final int r) {
        final double[] tmp = new double[img.length], out = new double[img.length];
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                double sum = 0;
                for (int k = -r; k <= r; k++) {
                    sum += img[y * W + Math.max(0, Math.min(W - 1, x + k))];
                }
                tmp[y * W + x] = sum / (2 * r + 1);
            }
        }
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                double sum = 0;
                for (int k = -r; k <= r; k++) {
                    sum += tmp[Math.max(0, Math.min(H - 1, y + k)) * W + x];
                }
                out[y * W + x] = sum / (2 * r + 1);
            }
        }
        return out;
    }

    /** The same product in every form (UPC-A = EAN-13 with a leading 0), as the catalog compares them. */
    private static String gtin(final String digits) {
        return String.format("%14s", digits).replace(' ', '0');
    }

    @Test
    public void expectedDigitsAreValid() {
        for (final Object[] c : CODES) {
            assertTrue(c[2].toString(), HelioBarcodeDecoder.gs1CheckOk((String) c[2]));
        }
    }

    @Test
    public void neverAWrongCode() throws Exception {
        final HelioBarcodeDecoder d = new HelioBarcodeDecoder();
        int frames = 0, read = 0, confirmed = 0, misreads = 0;
        final StringBuilder wrong = new StringBuilder(), misread = new StringBuilder();
        for (final Object[] c : CODES) {
            final BitMatrix m = modules((String) c[0], (BarcodeFormat) c[1]);
            final String expected = gtin((String) c[2]);
            for (final Spoil s : ALL) {
                final HelioScanConfirm rule = new HelioScanConfirm();
                for (int i = 0; i < 5; i++) {  // five frames of the same scene, with their own noise and shake
                    final HelioBarcodeDecoder.Read r = d.read(frame(m, s, 31L * i + s.name.hashCode()), W, H, W, 0);
                    frames++;
                    if (r != null) {
                        read++;
                        if (!gtin(r.code).equals(expected)) {
                            misread.append(c[0]).append(" ").append(s).append(" -> ").append(r).append('\n');
                            misreads++;
                        }
                    }
                    final String ok = rule.offer(r, 33L * i);
                    if (ok != null) {
                        confirmed++;
                        if (!gtin(ok).equals(expected)) {
                            wrong.append(c[0]).append(" ").append(s).append(" confirmed ").append(ok).append('\n');
                        }
                    }
                }
            }
        }
        if (wrong.length() > 0) {
            fail("wrong codes accepted:\n" + wrong);
        }
        // the frames are not all unreadable, so the test really reads codes (blur from 2 pixels and noise from 30
        // levels are beyond ZXing itself; the pieces of codes must give nothing)
        assertTrue(read + " of " + frames, read > frames / 8);
        assertTrue(confirmed + " confirmed", confirmed > CODES.length * 8);
        // a wrong read in a single frame is rare (the confirmation rule is there for it)
        assertTrue("single frames misread:\n" + misread, misreads * 100 <= read);
        System.out.println("HelioBarcodeRobustnessTest: " + frames + " frames, " + read + " read, " + confirmed + " confirmed, "
                + misreads + " single misreads, 0 wrong accepted");
    }

    @Test
    public void mildFramesAreRead() throws Exception {
        // each mild scene is read in most of its frames (the scanner needs 3 or 4 reads), always with the right digits
        final HelioBarcodeDecoder d = new HelioBarcodeDecoder();
        final StringBuilder bad = new StringBuilder();
        for (final Object[] c : CODES) {
            final BitMatrix m = modules((String) c[0], (BarcodeFormat) c[1]);
            for (final Spoil s : MILD) {
                int ok = 0;
                for (int seed = 0; seed < 5; seed++) {
                    final HelioBarcodeDecoder.Read r = d.read(frame(m, s, seed), W, H, W, 0);
                    if (r != null && gtin((String) c[2]).equals(gtin(r.code))
                            && (c[1] == BarcodeFormat.UPC_A ? r.format : c[1]) == r.format) {
                        ok++;
                    } else if (r != null) {
                        bad.append(c[0]).append(' ').append(s).append(" wrong ").append(r).append('\n');
                    }
                }
                if (ok < 4) {
                    bad.append(c[0]).append(' ').append(s).append(" read ").append(ok).append(" of 5\n");
                }
            }
        }
        assertEquals("", bad.toString());
    }

    @Test
    public void upcEComesBackAsUpcA() throws Exception {
        final HelioBarcodeDecoder.Read r = new HelioBarcodeDecoder().read(frame(modules("01234565", BarcodeFormat.UPC_E), MILD.get(0), 1),
                W, H, W, 0);
        assertEquals(BarcodeFormat.UPC_E, r.format);
        assertEquals("012345000065", r.code);
    }

    @Test
    public void sensorFramesAreTurnedUpright() throws Exception {
        // the sensor of a phone held upright gives frames turned by 90 or 270 degrees
        final byte[] upright = frame(modules("4006381333931", BarcodeFormat.EAN_13), MILD.get(0), 3);
        byte[] sensor = upright;
        int w = W, h = H;
        for (int i = 0; i < 3; i++) {  // three turns clockwise = one turn back
            sensor = HelioBarcodeDecoder.rotate(sensor, w, h);
            final int t = w;
            w = h;
            h = t;
        }
        assertEquals("4006381333931", new HelioBarcodeDecoder().read(sensor, w, h, w, 90).code);
        assertEquals("4006381333931", new HelioBarcodeDecoder().read(HelioBarcodeDecoder.rotate(upright, W, H), H, W, H, 270).code);
    }

    @Test
    public void theCentralBandComesFirst() throws Exception {
        // two products in sight, one on the guide line and one above it: the one on the line wins
        final Spoil small = new Spoil("small", 2, 0, 0, 1, 0, 3, 1);
        final byte[] center = frame(modules("4006381333931", BarcodeFormat.EAN_13), small, 5, 0);
        final byte[] above = frame(modules("3017620422003", BarcodeFormat.EAN_13), small, 6, -165);
        final byte[] both = center.clone();
        final int bandTop = Math.round(H * (1 - HelioBarcodeDecoder.BAND) / 2);
        System.arraycopy(above, 0, both, 0, (bandTop - 10) * W);
        assertEquals("4006381333931", new HelioBarcodeDecoder().read(both, W, H, W, 0).code);
        // a code away from the line is still found in the whole frame
        assertEquals("3017620422003", new HelioBarcodeDecoder().read(above, W, H, W, 0).code);
    }

    @Test
    public void aPieceOfALongerCodeIsNotRead() throws Exception {
        // ZXing alone reads the UPC-E 16130357 (number system 1) in the left 70% of this EAN-13
        final BitMatrix m = modules("7613035974685", BarcodeFormat.EAN_13);
        final HelioBarcodeDecoder d = new HelioBarcodeDecoder();
        for (int seed = 0; seed < 5; seed++) {
            final HelioBarcodeDecoder.Read r = d.read(frame(m, new Spoil("cut", 3, 0, 0, 0.7, 0, 4, 1), seed), W, H, W, 0);
            assertTrue(String.valueOf(r), r == null || r.code.equals("7613035974685"));
        }
        assertEquals("7613035974685", d.read(frame(m, MILD.get(0), 1), W, H, W, 0).code);
    }

    @Test
    public void wrongCheckDigitIsDropped() {
        assertTrue(HelioBarcodeDecoder.gs1CheckOk("4006381333931"));
        assertTrue(!HelioBarcodeDecoder.gs1CheckOk("4006381333932"));
        assertTrue(!HelioBarcodeDecoder.gs1CheckOk("4006381339331"));
        assertTrue(!HelioBarcodeDecoder.gs1CheckOk("4006"));
        assertNull(HelioBarcodeDecoder.checked(new com.google.zxing.Result("4006381333932", null, null, BarcodeFormat.EAN_13)));
        assertNull(HelioBarcodeDecoder.checked(new com.google.zxing.Result("400638133393", null, null, BarcodeFormat.EAN_13)));
        assertNull(HelioBarcodeDecoder.checked(new com.google.zxing.Result("4006381333931", null, null, BarcodeFormat.QR_CODE)));
        assertEquals("4006381333931",
                HelioBarcodeDecoder.checked(new com.google.zxing.Result("4006381333931", null, null, BarcodeFormat.EAN_13)).code);
    }
}
