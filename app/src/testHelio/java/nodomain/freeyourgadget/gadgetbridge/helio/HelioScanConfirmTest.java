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

import com.google.zxing.BarcodeFormat;

import org.junit.Test;

/**
 * Confirmation rule: the same code 3 times in a row (4 for EAN-8 and UPC-E); empty frames in between do not
 * count, another code or a pause of more than 4 seconds without reads starts again.
 */
public class HelioScanConfirmTest {
    private static final HelioBarcodeDecoder.Read EAN13 = new HelioBarcodeDecoder.Read("4006381333931", BarcodeFormat.EAN_13);
    private static final HelioBarcodeDecoder.Read OTHER = new HelioBarcodeDecoder.Read("4006381333924", BarcodeFormat.EAN_13);
    private static final HelioBarcodeDecoder.Read EAN8 = new HelioBarcodeDecoder.Read("96385074", BarcodeFormat.EAN_8);
    private static final HelioBarcodeDecoder.Read UPCE = new HelioBarcodeDecoder.Read("012345000065", BarcodeFormat.UPC_E);

    @Test
    public void readsNeeded() {
        assertEquals(3, HelioScanConfirm.readsNeeded(BarcodeFormat.EAN_13));
        assertEquals(3, HelioScanConfirm.readsNeeded(BarcodeFormat.UPC_A));
        assertEquals(4, HelioScanConfirm.readsNeeded(BarcodeFormat.EAN_8));
        assertEquals(4, HelioScanConfirm.readsNeeded(BarcodeFormat.UPC_E));
    }

    @Test
    public void threeEqualReadsForEan13() {
        final HelioScanConfirm c = new HelioScanConfirm();
        assertNull(c.offer(EAN13, 0));
        assertNull(c.offer(EAN13, 33));
        assertEquals("4006381333931", c.offer(EAN13, 66));
    }

    @Test
    public void emptyFramesInBetweenDoNotCount() {
        final HelioScanConfirm c = new HelioScanConfirm();
        assertNull(c.offer(EAN13, 0));
        for (int t = 33; t < 300; t += 33) {
            assertNull(c.offer(null, t));
        }
        assertNull(c.offer(EAN13, 330));
        assertNull(c.offer(null, 363));
        assertEquals("4006381333931", c.offer(EAN13, 396));
    }

    @Test
    public void aSingleMisreadIsNeverAccepted() {
        final HelioScanConfirm c = new HelioScanConfirm();
        assertNull(c.offer(EAN13, 0));
        assertNull(c.offer(EAN13, 33));
        assertNull(c.offer(OTHER, 66));   // another code: start again
        assertNull(c.offer(EAN13, 99));
        assertNull(c.offer(EAN13, 132));
        assertEquals("4006381333931", c.offer(EAN13, 165));
        // a misread among good reads never wins: it would need 3 in a row
        assertNull(c.offer(OTHER, 200));
        assertNull(c.offer(EAN13, 233));
        assertNull(c.offer(OTHER, 266));
        assertNull(c.offer(EAN13, 299));
    }

    @Test
    public void sparseReadsStillAddUp() {
        // on a phone a read comes every few frames: up to 4 seconds apart they still count
        assertEquals(4000, HelioScanConfirm.MAX_GAP_MS);
        final HelioScanConfirm c = new HelioScanConfirm();
        assertNull(c.offer(EAN13, 0));
        for (int t = 33; t < 3900; t += 33) {
            assertNull(c.offer(null, t));
        }
        assertNull(c.offer(EAN13, 3900));
        assertEquals("4006381333931", c.offer(EAN13, 7800));
    }

    @Test
    public void aLongPauseStartsAgain() {
        final HelioScanConfirm c = new HelioScanConfirm();
        assertNull(c.offer(EAN13, 0));
        assertNull(c.offer(EAN13, 100));
        assertNull(c.offer(null, 2000));
        assertNull(c.offer(EAN13, 100 + HelioScanConfirm.MAX_GAP_MS + 1));  // counts as the first again
        assertNull(c.offer(EAN13, 4200));
        assertEquals("4006381333931", c.offer(EAN13, 4250));
    }

    @Test
    public void fourReadsForEan8AndUpcE() {
        final HelioScanConfirm c = new HelioScanConfirm();
        for (final HelioBarcodeDecoder.Read r : new HelioBarcodeDecoder.Read[]{EAN8, UPCE}) {
            assertNull(c.offer(r, 0));
            assertNull(c.offer(r, 33));
            assertNull(c.offer(r, 66));
            assertEquals(r.code, c.offer(r, 99));
        }
        // half of an EAN-13 read as EAN-8 three times, then the whole EAN-13: nothing wrong comes out
        assertNull(c.offer(EAN8, 200));
        assertNull(c.offer(EAN8, 233));
        assertNull(c.offer(EAN8, 266));
        assertNull(c.offer(EAN13, 299));
        assertNull(c.offer(EAN13, 332));
        assertEquals("4006381333931", c.offer(EAN13, 365));
    }

    @Test
    public void sameDigitsAsAnotherSymbologyDoNotAddUp() {
        final HelioScanConfirm c = new HelioScanConfirm();
        final HelioBarcodeDecoder.Read upca = new HelioBarcodeDecoder.Read("012345000065", BarcodeFormat.UPC_A);
        assertNull(c.offer(UPCE, 0));
        assertNull(c.offer(upca, 33));
        assertNull(c.offer(upca, 66));
        assertEquals("012345000065", c.offer(upca, 99));
    }

    @Test
    public void afterAConfirmationItStartsAgain() {
        final HelioScanConfirm c = new HelioScanConfirm();
        c.offer(EAN13, 0);
        c.offer(EAN13, 33);
        assertEquals("4006381333931", c.offer(EAN13, 66));
        assertNull(c.offer(EAN13, 99));
        assertNull(c.offer(EAN13, 132));
        assertEquals("4006381333931", c.offer(EAN13, 165));
    }
}
