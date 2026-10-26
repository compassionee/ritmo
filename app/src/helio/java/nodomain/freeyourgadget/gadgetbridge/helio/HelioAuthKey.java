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

import android.content.SharedPreferences;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import java.util.Locale;
import java.util.regex.Pattern;

import nodomain.freeyourgadget.gadgetbridge.activities.devicesettings.DeviceSettingsPreferenceConst;

/**
 * The band's auth key typed or pasted by the user: format check and saving in the device's own preferences
 * (the file Gadgetbridge reads by MAC address, see GBApplication.getDeviceSpecificSharedPrefs).
 * Accepted: 32 hexadecimal characters, with or without "0x" in front; spaces from a paste are ignored.
 * Saved as "0x" + 32 lowercase hex digits, the form every Huami/Zepp OS coordinator reads as hex.
 */
public final class HelioAuthKey {
    public static final int HEX_DIGITS = 32;

    /** Outcome of the format check. */
    public enum Check { OK, EMPTY, BAD_CHARACTERS, BAD_LENGTH }

    private static final Pattern HEX = Pattern.compile("[0-9a-fA-F]*");
    private static final Pattern MAC = Pattern.compile("([0-9A-F]{2}:){5}[0-9A-F]{2}");

    private HelioAuthKey() {
    }

    /** The key without spaces and without the "0x" prefix (not yet checked). */
    @NonNull
    static String digits(@Nullable final String typed) {
        String s = typed == null ? "" : typed.replaceAll("\\s+", "");
        if (s.startsWith("0x") || s.startsWith("0X")) {
            s = s.substring(2);
        }
        return s;
    }

    @NonNull
    public static Check check(@Nullable final String typed) {
        final String s = digits(typed);
        if (s.isEmpty()) {
            return Check.EMPTY;
        }
        if (!HEX.matcher(s).matches()) {
            return Check.BAD_CHARACTERS;
        }
        return s.length() == HEX_DIGITS ? Check.OK : Check.BAD_LENGTH;
    }

    /** The key in the saved form ("0x" + 32 lowercase hex digits), or null when the format is wrong. */
    @Nullable
    public static String normalize(@Nullable final String typed) {
        return check(typed) == Check.OK ? "0x" + digits(typed).toLowerCase(Locale.ROOT) : null;
    }

    /** MAC address in Gadgetbridge's form (AA:BB:CC:DD:EE:FF), from colons or dashes; null when invalid. */
    @Nullable
    public static String normalizeMac(@Nullable final String typed) {
        if (typed == null) {
            return null;
        }
        final String s = typed.trim().replace('-', ':').toUpperCase(Locale.ROOT);
        return MAC.matcher(s).matches() ? s : null;
    }

    /** Whether the band's preferences already have a key (the key itself is never shown). */
    public static boolean hasKey(@Nullable final SharedPreferences devicePrefs) {
        if (devicePrefs == null) {
            return false;
        }
        final String k = devicePrefs.getString(DeviceSettingsPreferenceConst.PREF_AUTH_KEY, null);
        return k != null && !k.trim().isEmpty();
    }

    /**
     * Saves the key in the band's preferences; false, and nothing written, when the format is wrong.
     */
    public static boolean save(@NonNull final SharedPreferences devicePrefs, @Nullable final String typed) {
        final String key = normalize(typed);
        if (key == null) {
            return false;
        }
        return devicePrefs.edit().putString(DeviceSettingsPreferenceConst.PREF_AUTH_KEY, key).commit();
    }
}
