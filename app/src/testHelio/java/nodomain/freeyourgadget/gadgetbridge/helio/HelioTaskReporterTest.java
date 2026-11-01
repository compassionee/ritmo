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

import android.app.Application;

import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import java.util.ArrayList;
import java.util.List;

/** Progress events of a long operation for the page: spaced out, only known names and numbers. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioTaskReporterTest {
    private final List<JSONObject> sent = new ArrayList<>();
    private long now = 1000;

    private HelioTaskReporter reporter(final String task) {
        return new HelioTaskReporter(task, json -> {
            try {
                sent.add(new JSONObject(json));
            } catch (final Exception e) {
                throw new AssertionError(json, e);
            }
        }, () -> now);
    }

    @Test
    public void startPhasesPercentAndEnd() throws Exception {
        final HelioTaskReporter r = reporter("catalog_import");
        r.start();
        r.report("copy", 50, 200);
        now += HelioTaskReporter.MIN_INTERVAL_MS;
        r.report("copy", 100, 200);
        r.report("check", 1, 4);
        now += HelioTaskReporter.MIN_INTERVAL_MS;
        r.report("check", 2, 0);  // unknown total: no percentage
        r.end(true);
        assertEquals(6, sent.size());
        assertEquals("start", sent.get(0).getString("phase"));
        assertFalse(sent.get(0).has("percent"));
        assertEquals(25, sent.get(1).getInt("percent"));
        assertEquals(50, sent.get(2).getInt("percent"));
        assertEquals("check", sent.get(3).getString("phase"));  // a new phase always goes out
        assertFalse(sent.get(4).has("percent"));
        assertTrue(sent.get(5).getBoolean("end"));
        assertTrue(sent.get(5).getBoolean("ok"));
        for (final JSONObject o : sent) {
            assertEquals("catalog_import", o.getString("task"));
        }
    }

    @Test
    public void spacedOutWithinAPhase() {
        final HelioTaskReporter r = reporter("csv");
        for (int i = 0; i <= 1000; i++) {
            r.report("copy", i, 1000);  // a thousand updates in no time: one event
        }
        assertEquals(1, sent.size());
        r.report("copy", 1000, 1000);  // too soon after the last event
        assertEquals(1, sent.size());
        now += HelioTaskReporter.MIN_INTERVAL_MS;
        r.report("copy", 1000, 1000);  // the interval has passed: the new percentage goes out
        assertEquals(2, sent.size());
        assertEquals(100, sent.get(1).optInt("percent"));
        now += HelioTaskReporter.MIN_INTERVAL_MS;
        r.report("copy", 1000, 1000);  // same percentage: nothing new to say
        assertEquals(2, sent.size());
    }

    @Test
    public void unknownNamesNeverReachThePage() {
        final HelioTaskReporter r = reporter("export");
        r.report("<script>", 1, 2);
        assertEquals(0, sent.size());
        try {
            reporter("anything");
            throw new AssertionError("unknown task accepted");
        } catch (final IllegalArgumentException expected) {
            // only the four tasks of the app
        }
    }
}
