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
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.app.Activity;
import android.app.Application;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.Intent;
import android.content.SharedPreferences;
import android.view.View;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.shadows.ShadowToast;

import java.lang.reflect.Field;
import java.lang.reflect.Method;

import nodomain.freeyourgadget.gadgetbridge.GBApplication;
import nodomain.freeyourgadget.gadgetbridge.R;
import nodomain.freeyourgadget.gadgetbridge.activities.devicesettings.DeviceSettingsPreferenceConst;

/**
 * The band's auth key typed by hand: 32 hex digits, with or without "0x", saved as "0x" + lowercase in the
 * band's own preferences (the file Gadgetbridge reads by MAC address); a wrong format gives a clear error and
 * writes nothing.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioAuthKeyTest {
    private static final String MAC = "C8:0F:10:AA:BB:CC";
    // a made-up key: 32 hex digits, half lowercase and half uppercase
    private static final String TYPED = "0123456789abcdef" + "0123456789ABCDEF";
    private static final String SAVED = "0x0123456789abcdef0123456789abcdef";

    private Object previousContext;

    /** GBApplication.getDeviceSpecificSharedPrefs works on the test application's files. */
    @Before
    public void gadgetbridgeContext() throws Exception {
        final Field f = GBApplication.class.getDeclaredField("context");
        f.setAccessible(true);
        previousContext = f.get(null);
        final GBApplication app = new GBApplication();  // the constructor sets the static context
        final Method attach = ContextWrapper.class.getDeclaredMethod("attachBaseContext", Context.class);
        attach.setAccessible(true);
        attach.invoke(app, RuntimeEnvironment.getApplication());
    }

    @After
    public void restoreContext() throws Exception {
        final Field f = GBApplication.class.getDeclaredField("context");
        f.setAccessible(true);
        f.set(null, previousContext);
    }

    private static SharedPreferences bandPrefs(final String mac) {
        return GBApplication.getDeviceSpecificSharedPrefs(mac);
    }

    @Test
    public void acceptedFormats() {
        for (final String typed : new String[]{TYPED, "0x" + TYPED, "0X" + TYPED, "  0x" + TYPED + "\n",
                "0123 4567 89ab cdef 0123 4567 89ab cdef"}) {
            assertEquals(typed, HelioAuthKey.Check.OK, HelioAuthKey.check(typed));
            assertEquals(typed, SAVED, HelioAuthKey.normalize(typed));
        }
    }

    @Test
    public void wrongFormats() {
        assertEquals(HelioAuthKey.Check.EMPTY, HelioAuthKey.check(null));
        assertEquals(HelioAuthKey.Check.EMPTY, HelioAuthKey.check("   "));
        assertEquals(HelioAuthKey.Check.EMPTY, HelioAuthKey.check("0x"));
        assertEquals(HelioAuthKey.Check.BAD_LENGTH, HelioAuthKey.check(TYPED.substring(1)));        // 31
        assertEquals(HelioAuthKey.Check.BAD_LENGTH, HelioAuthKey.check(TYPED + "0"));               // 33
        assertEquals(HelioAuthKey.Check.BAD_LENGTH, HelioAuthKey.check("0x" + TYPED.substring(2))); // 0x + 30
        assertEquals(HelioAuthKey.Check.BAD_CHARACTERS, HelioAuthKey.check("0x0x" + TYPED.substring(2)));
        assertEquals(HelioAuthKey.Check.BAD_CHARACTERS, HelioAuthKey.check(TYPED.substring(1) + "g"));
        assertEquals(HelioAuthKey.Check.BAD_CHARACTERS, HelioAuthKey.check("0x" + TYPED.replace('a', 'z')));
        assertEquals(HelioAuthKey.Check.BAD_CHARACTERS, HelioAuthKey.check("01:23:45:67:89:ab:cd:ef:01:23:45:67:89:ab:cd:ef"));
        for (final String bad : new String[]{null, "", TYPED.substring(1), TYPED + "0", "x" + TYPED, "0x" + TYPED + "0"}) {
            assertNull(bad, HelioAuthKey.normalize(bad));
        }
    }

    @Test
    public void macAddresses() {
        assertEquals(MAC, HelioAuthKey.normalizeMac(" c8:0f:10:aa:bb:cc "));
        assertEquals(MAC, HelioAuthKey.normalizeMac("C8-0F-10-AA-BB-CC"));
        for (final String bad : new String[]{null, "", "C8:0F:10:AA:BB", "C8:0F:10:AA:BB:CC:DD", "C80F10AABBCC", "G8:0F:10:AA:BB:CC"}) {
            assertNull(bad, HelioAuthKey.normalizeMac(bad));
        }
    }

    @Test
    public void savedInTheBandPreferencesByMac() {
        final SharedPreferences prefs = bandPrefs(MAC);
        assertFalse(HelioAuthKey.hasKey(prefs));
        assertTrue(HelioAuthKey.save(prefs, "0X" + TYPED));
        // the same file Gadgetbridge reads for this band (the MAC case does not matter), key in the hex form
        assertEquals(SAVED, bandPrefs(MAC.toLowerCase()).getString(DeviceSettingsPreferenceConst.PREF_AUTH_KEY, null));
        assertEquals(SAVED, RuntimeEnvironment.getApplication().getSharedPreferences("devicesettings_" + MAC, Context.MODE_PRIVATE)
                .getString("authkey", null));
        assertTrue(HelioAuthKey.hasKey(prefs));
        // another band is untouched
        assertFalse(HelioAuthKey.hasKey(bandPrefs("C8:0F:10:AA:BB:CD")));
        // a wrong key writes nothing: the saved one stays
        assertFalse(HelioAuthKey.save(prefs, TYPED.substring(1)));
        assertFalse(HelioAuthKey.save(prefs, ""));
        assertEquals(SAVED, prefs.getString(DeviceSettingsPreferenceConst.PREF_AUTH_KEY, null));
    }

    private static ActivityController<HelioAuthKeyActivity> open(final String mac) {
        final Intent i = new Intent(RuntimeEnvironment.getApplication(), HelioAuthKeyActivity.class);
        if (mac != null) {
            i.putExtra(HelioAuthKeyActivity.EXTRA_MAC, mac).putExtra(HelioAuthKeyActivity.EXTRA_NAME, "Helio Strap");
        }
        return Robolectric.buildActivity(HelioAuthKeyActivity.class, i).setup();
    }

    private static String text(final int id, final Object... args) {
        return RuntimeEnvironment.getApplication().getString(id, args);
    }

    @Test
    public void screenShowsTheErrorAndSavesNothing() {
        final HelioAuthKeyActivity a = open(MAC).get();
        assertEquals(View.GONE, a.macField.getVisibility());
        assertEquals(View.GONE, a.savedText.getVisibility());

        a.saveButton.performClick();
        assertEquals(text(R.string.helio_authkey_empty), a.errorText.getText().toString());

        a.keyField.setText("0x" + TYPED.substring(3));
        a.saveButton.performClick();
        assertEquals(text(R.string.helio_authkey_bad_length, 32, 29), a.errorText.getText().toString());

        a.keyField.setText(TYPED.substring(1) + "z");
        a.saveButton.performClick();
        assertEquals(text(R.string.helio_authkey_bad_characters), a.errorText.getText().toString());
        assertEquals(View.VISIBLE, a.errorText.getVisibility());

        assertFalse(a.isFinishing());
        assertFalse(HelioAuthKey.hasKey(bandPrefs(MAC)));
    }

    @Test
    public void screenSavesTheKeyOfTheBand() {
        final HelioAuthKeyActivity a = open(MAC).get();
        a.keyField.setText(" " + TYPED + " ");
        a.saveButton.performClick();
        assertTrue(a.isFinishing());
        assertEquals(Activity.RESULT_OK, shadowOf(a).getResultCode());
        assertEquals(text(R.string.helio_authkey_saved), ShadowToast.getTextOfLatestToast());
        assertEquals(SAVED, bandPrefs(MAC).getString(DeviceSettingsPreferenceConst.PREF_AUTH_KEY, null));
        // the field is emptied: the key is not kept on screen
        assertEquals("", a.keyField.getText().toString());
        // opened again: it says a key is there, without showing it
        final HelioAuthKeyActivity again = open(MAC).get();
        assertEquals(View.VISIBLE, again.savedText.getVisibility());
        assertEquals("", again.keyField.getText().toString());
    }

    @Test
    public void withoutABandTheMacIsTyped() {
        final HelioAuthKeyActivity a = open(null).get();
        assertEquals(View.VISIBLE, a.macField.getVisibility());
        a.keyField.setText(TYPED);
        a.macField.setText("C8:0F:10");
        a.saveButton.performClick();
        assertEquals(text(R.string.helio_authkey_bad_mac), a.errorText.getText().toString());
        assertFalse(a.isFinishing());
        a.macField.setText("c8-0f-10-aa-bb-cc");
        a.saveButton.performClick();
        assertTrue(a.isFinishing());
        assertEquals(SAVED, bandPrefs(MAC).getString(DeviceSettingsPreferenceConst.PREF_AUTH_KEY, null));
    }

    @Test
    public void startedFromTheBandMenuWithTheBandMac() {
        final HelioGbActionActivity actions = Robolectric.buildActivity(HelioGbActionActivity.class).get();
        final Intent i = actions.authKeyIntent(null);
        assertEquals(HelioAuthKeyActivity.class.getName(), i.getComponent().getClassName());
        assertFalse(i.hasExtra(HelioAuthKeyActivity.EXTRA_MAC));
        assertTrue(HelioGbActions.ALL.contains(HelioGbActions.AUTH_KEY));
    }
}
