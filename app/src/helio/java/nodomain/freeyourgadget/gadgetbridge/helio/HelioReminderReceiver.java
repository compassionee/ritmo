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

/**
 * A reminder's alarm (HelioReminders), in the :helio process: Python is not started. Not exported: only Android's
 * alarm, through Ritmo's own PendingIntent, can send it.
 */
public class HelioReminderReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(final Context context, final Intent intent) {
        if (intent == null || !HelioReminders.ACTION_FIRE.equals(intent.getAction())) {
            return;
        }
        final String kind = intent.getStringExtra(HelioReminders.EXTRA_KIND);
        if (kind != null) {
            HelioReminders.fire(context, kind, intent.getLongExtra(HelioReminders.EXTRA_AT, 0), System.currentTimeMillis());
        }
    }
}
