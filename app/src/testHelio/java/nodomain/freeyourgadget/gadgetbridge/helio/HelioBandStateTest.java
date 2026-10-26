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
import android.content.Context;
import android.content.SharedPreferences;

import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioBandStateTest {
    private static final String A = "AA:BB:CC:DD:EE:02";
    private Context context;
    private SharedPreferences prefs;

    @Before
    public void setUp() {
        context = RuntimeEnvironment.getApplication();
        prefs = context.getSharedPreferences(HelioBandState.PREFS_NAME, Context.MODE_PRIVATE);
        prefs.edit().clear().commit();
    }

    @Test
    public void recordsStateAndBusyTask() {
        HelioBandState.record(context, A, "CONNECTING", null, false, 1000);
        assertEquals("CONNECTING", prefs.getString(A + "/state", null));
        assertEquals("", prefs.getString(A + "/busy", null));
        assertEquals(1000, prefs.getLong(A + "/updated", 0));
        assertFalse(prefs.contains(A + "/last_sync"));
    }

    @Test
    public void endOfATransferWhileConnectedIsASync() {
        HelioBandState.record(context, A, "INITIALIZED", null, true, 1000);
        assertFalse(prefs.contains(A + "/last_sync"));
        HelioBandState.record(context, A, "INITIALIZED", "Fetching activity data", true, 2000);
        assertFalse(prefs.contains(A + "/last_sync"));
        HelioBandState.record(context, A, "INITIALIZED", null, true, 3000);
        assertEquals(3000, prefs.getLong(A + "/last_sync", 0));
    }

    @Test
    public void transferInterruptedByADisconnectionIsNotASync() {
        HelioBandState.record(context, A, "INITIALIZED", "Fetching activity data", true, 1000);
        HelioBandState.record(context, A, "NOT_CONNECTED", null, false, 2000);
        assertFalse(prefs.contains(A + "/last_sync"));
    }

    @Test
    public void processStartResetsConnectionsButKeepsLastSync() {
        HelioBandState.record(context, A, "INITIALIZED", "x", true, 1000);
        HelioBandState.record(context, A, "INITIALIZED", null, true, 2000);
        HelioBandState.resetConnections(context, 5000);
        assertEquals("NOT_CONNECTED", prefs.getString(A + "/state", null));
        assertEquals("", prefs.getString(A + "/busy", null));
        assertEquals(5000, prefs.getLong(A + "/updated", 0));
        assertEquals(2000, prefs.getLong(A + "/last_sync", 0));
    }

    @Test
    public void fileIsWherePythonReadsIt() {
        assertTrue(HelioBandState.file(context).getPath().endsWith("/shared_prefs/helio_band_state.xml"));
    }
}
