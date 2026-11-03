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

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * Android forgets alarms at a restart and an app update, and their times move with the clock and the time zone: the
 * reminders are set again (HelioReminders.schedule). It only reads settings.json and sets alarms, nothing else, so a
 * broadcast sent by another app changes nothing.
 */
public class HelioReminderBootReceiver extends BroadcastReceiver {
    static final Set<String> ACTIONS = Collections.unmodifiableSet(new HashSet<>(Arrays.asList(
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED, Intent.ACTION_TIME_CHANGED,
            Intent.ACTION_TIMEZONE_CHANGED)));

    @Override
    public void onReceive(final Context context, final Intent intent) {
        if (intent != null && ACTIONS.contains(intent.getAction())) {
            HelioReminders.schedule(context, System.currentTimeMillis());
        }
    }
}
