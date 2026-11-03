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

import android.Manifest;
import android.app.AlarmManager;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import nodomain.freeyourgadget.gadgetbridge.R;

/**
 * Local reminders, off until the user turns one on in the Profile (settings.json, saved by Python):
 * <ul>
 * <li>"day": in the evening at the chosen time, "Mark the day as complete", only if today's food diary is not marked;</li>
 * <li>"weigh": in the morning on the chosen weekdays, "Weigh yourself", only if no weight is entered for today.</li>
 * </ul>
 * Android's alarms start {@link HelioReminderReceiver} in the :helio process at the time, without Python: the conditions
 * are read from the data files (diary-YYYY-MM.json, weights.json), in the phone's time zone like Helio. Nothing leaves
 * the phone. The notification permission is asked only when the user turns a reminder on (HelioActivity).
 */
final class HelioReminders {
    private static final Logger LOG = LoggerFactory.getLogger(HelioReminders.class);

    static final String DAY = "day";
    static final String WEIGH = "weigh";
    static final String[] KINDS = {DAY, WEIGH};
    static final String CHANNEL = "helio_reminders";
    static final String ACTION_FIRE = "nodomain.freeyourgadget.gadgetbridge.helio.REMINDER";
    static final String EXTRA_KIND = "kind";
    static final String EXTRA_AT = "at";
    /** The section the notification opens (HelioActivity). */
    static final String EXTRA_TAB = "helio_tab";
    /** An alarm delivered this late (phone asleep, off) is dropped: "today" may already be another day. */
    static final long STALE_MS = 2 * 60 * 60 * 1000L;

    /** Permission states for the page. */
    static final String PERM_GRANTED = "granted";
    /** Android 13+: not granted yet, can be asked. */
    static final String PERM_ASK = "ask";
    /** Notifications of Ritmo (or of the reminders' channel) turned off in Android's settings. */
    static final String PERM_OFF = "off";

    private static final Pattern TIME = Pattern.compile("^([01]\\d|2[0-3]):([0-5]\\d)$");

    /** One reminder: on, hour and minute, ISO weekdays (index 1 = Monday ... 7 = Sunday). */
    static final class Rule {
        final boolean on;
        final int hour;
        final int minute;
        final boolean[] days = new boolean[8];

        Rule(final boolean on, final int hour, final int minute, @Nullable final int[] isoDays) {
            this.on = on;
            this.hour = hour;
            this.minute = minute;
            if (isoDays == null) {
                for (int d = 1; d <= 7; d++) {
                    days[d] = true;
                }
            } else {
                for (final int d : isoDays) {
                    if (d >= 1 && d <= 7) {
                        days[d] = true;
                    }
                }
            }
        }

        boolean anyDay() {
            for (int d = 1; d <= 7; d++) {
                if (days[d]) {
                    return true;
                }
            }
            return false;
        }
    }

    private HelioReminders() {
    }

    @NonNull
    static File settingsFile(@NonNull final File stateDir) {
        return new File(stateDir, "settings.json");
    }

    @Nullable
    private static JSONObject readJson(@NonNull final File file) {
        try {
            return new JSONObject(new String(java.nio.file.Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
        } catch (final IOException | JSONException | RuntimeException e) {
            return null;
        }
    }

    /** A reminder from settings.json; off when missing or invalid (the same defaults as helio_app.REMINDER_DEFAULTS). */
    @NonNull
    static Rule rule(@NonNull final File stateDir, @NonNull final String kind) {
        final boolean weigh = WEIGH.equals(kind);
        final int defHour = weigh ? 7 : 21;
        final int defMinute = 30;
        final JSONObject settings = readJson(settingsFile(stateDir));
        final JSONObject reminders = settings != null ? settings.optJSONObject("reminders") : null;
        final JSONObject r = reminders != null ? reminders.optJSONObject(kind) : null;
        if (r == null) {
            return new Rule(false, defHour, defMinute, null);
        }
        int hour = defHour;
        int minute = defMinute;
        final Matcher m = TIME.matcher(r.optString("time", ""));
        if (m.matches()) {
            hour = Integer.parseInt(m.group(1));
            minute = Integer.parseInt(m.group(2));
        }
        int[] days = null;
        if (weigh) {
            final JSONArray a = r.optJSONArray("days");
            if (a != null) {
                days = new int[a.length()];
                for (int i = 0; i < a.length(); i++) {
                    final Object d = a.opt(i);
                    days[i] = d instanceof Integer ? (Integer) d : 0;
                }
            }
        }
        final Rule rule = new Rule(r.opt("on") == Boolean.TRUE, hour, minute, days);
        return rule.on && !rule.anyDay() ? new Rule(false, hour, minute, days) : rule;
    }

    @Nullable
    static String lang(@NonNull final File stateDir) {
        final JSONObject settings = readJson(settingsFile(stateDir));
        final String lang = settings != null ? settings.optString("lang", "") : "";
        return "it".equals(lang) || "en".equals(lang) ? lang : null;
    }

    /** ISO weekday (1 = Monday ... 7 = Sunday) of a Calendar. */
    private static int isoDay(@NonNull final Calendar c) {
        final int d = c.get(Calendar.DAY_OF_WEEK);  // Sunday = 1
        return d == Calendar.SUNDAY ? 7 : d - 1;
    }

    /** The next time of the reminder strictly after now, on one of its weekdays; -1 when off. */
    static long next(@NonNull final Rule rule, final long now, @NonNull final TimeZone zone) {
        if (!rule.on || !rule.anyDay()) {
            return -1;
        }
        final Calendar day = Calendar.getInstance(zone);
        day.setTimeInMillis(now);
        for (int i = 0; i <= 8; i++) {
            final Calendar c = Calendar.getInstance(zone);
            c.clear();
            c.set(day.get(Calendar.YEAR), day.get(Calendar.MONTH), day.get(Calendar.DAY_OF_MONTH), rule.hour, rule.minute, 0);
            // a time skipped by the clock going forward (02:30) becomes the first one after it
            if (c.getTimeInMillis() > now && rule.days[isoDay(c)]) {
                return c.getTimeInMillis();
            }
            day.add(Calendar.DAY_OF_MONTH, 1);
        }
        return -1;
    }

    @NonNull
    static String isoDate(final long at, @NonNull final TimeZone zone) {
        final SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd", Locale.ROOT);
        f.setTimeZone(zone);
        return f.format(new Date(at));
    }

    /** Today's diary is marked complete (diary-YYYY-MM.json, {"YYYY-MM-DD": {"complete": true, ...}}). */
    static boolean dayMarked(@NonNull final File stateDir, @NonNull final String today) {
        final JSONObject month = readJson(new File(stateDir, "diary-" + today.substring(0, 7) + ".json"));
        final JSONObject rec = month != null ? month.optJSONObject(today) : null;
        return rec != null && rec.opt("complete") == Boolean.TRUE;
    }

    /** A weight is entered for today (weights.json, {"YYYY-MM-DD": kg}). */
    static boolean weighed(@NonNull final File stateDir, @NonNull final String today) {
        final JSONObject weights = readJson(new File(stateDir, "weights.json"));
        final Object kg = weights != null ? weights.opt(today) : null;
        return kg instanceof Number && !(kg instanceof Boolean);
    }

    /** Whether the reminder is still needed today. */
    static boolean needed(@NonNull final File stateDir, @NonNull final String kind, @NonNull final String today) {
        return WEIGH.equals(kind) ? !weighed(stateDir, today) : !dayMarked(stateDir, today);
    }

    /** Can Ritmo show notifications now: granted, ask (Android 13+, not asked or refused) or off (Android settings). */
    @NonNull
    static String permission(@NonNull final Context context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return PERM_ASK;
        }
        final NotificationManagerCompat nm = NotificationManagerCompat.from(context);
        if (!nm.areNotificationsEnabled()) {
            return PERM_OFF;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            final NotificationChannel ch = context.getSystemService(NotificationManager.class).getNotificationChannel(CHANNEL);
            if (ch != null && ch.getImportance() == NotificationManager.IMPORTANCE_NONE) {
                return PERM_OFF;
            }
        }
        return PERM_GRANTED;
    }

    static boolean anyOn(@NonNull final File stateDir) {
        for (final String kind : KINDS) {
            if (rule(stateDir, kind).on) {
                return true;
            }
        }
        return false;
    }

    @NonNull
    private static PendingIntent alarmIntent(@NonNull final Context context, @NonNull final String kind, final long at) {
        final Intent intent = new Intent(context, HelioReminderReceiver.class)
                .setAction(ACTION_FIRE)
                .putExtra(EXTRA_KIND, kind)
                .putExtra(EXTRA_AT, at);
        return PendingIntent.getBroadcast(context, WEIGH.equals(kind) ? 2 : 1, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    /** Sets (or cancels) the next alarm of every reminder from settings.json. */
    static void schedule(@NonNull final Context context, final long now) {
        final File stateDir = HelioPythonBackend.stateDir(context);
        final AlarmManager am = context.getSystemService(AlarmManager.class);
        if (am == null) {
            return;
        }
        for (final String kind : KINDS) {
            final long at = next(rule(stateDir, kind), now, TimeZone.getDefault());
            if (at < 0) {
                am.cancel(alarmIntent(context, kind, 0));
                continue;
            }
            final PendingIntent pi = alarmIntent(context, kind, at);
            // exact when Android allows it (Android 12+ may not), otherwise within a few minutes
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
            } else {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
            }
        }
    }

    /**
     * An alarm went off: the next one is set, then the notification is shown if it is not late, the reminder is
     * still on, the condition holds and notifications are allowed. Returns whether it was shown.
     */
    static boolean fire(@NonNull final Context context, @NonNull final String kind, final long at, final long now) {
        schedule(context, now);
        final File stateDir = HelioPythonBackend.stateDir(context);
        if (!DAY.equals(kind) && !WEIGH.equals(kind)) {
            return false;
        }
        if (at <= 0 || now - at > STALE_MS || now < at - STALE_MS) {
            LOG.info("Reminder {} dropped: delivered at the wrong time", kind);
            return false;
        }
        if (!rule(stateDir, kind).on) {
            return false;
        }
        if (!needed(stateDir, kind, isoDate(now, TimeZone.getDefault()))) {
            return false;
        }
        if (!PERM_GRANTED.equals(permission(context))) {
            LOG.info("Reminder {} not shown: notifications not allowed", kind);
            return false;
        }
        notify(context, kind, lang(stateDir));
        return true;
    }

    /** The app's resources in the language chosen in the Profile (the system one otherwise). */
    @NonNull
    static Context localized(@NonNull final Context context, @Nullable final String lang) {
        if (lang == null) {
            return context;
        }
        final Configuration config = new Configuration(context.getResources().getConfiguration());
        config.setLocale(new Locale(lang));
        return context.createConfigurationContext(config);
    }

    static void createChannel(@NonNull final Context context, @Nullable final String lang) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            final Context res = localized(context, lang);
            final NotificationChannel ch = new NotificationChannel(CHANNEL,
                    res.getString(R.string.helio_reminders_channel), NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription(res.getString(R.string.helio_reminders_channel_text));
            context.getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    @SuppressWarnings("MissingPermission")  // checked by permission() just before
    private static void notify(@NonNull final Context context, @NonNull final String kind, @Nullable final String lang) {
        createChannel(context, lang);
        final Context res = localized(context, lang);
        final boolean weigh = WEIGH.equals(kind);
        // as the launcher icon does: an open Ritmo comes back as it was, otherwise it opens on the section
        final Intent open = new Intent(Intent.ACTION_MAIN)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .setClass(context, HelioActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
                .putExtra(EXTRA_TAB, weigh ? "profile" : "food");
        final PendingIntent content = PendingIntent.getActivity(context, weigh ? 2 : 1, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        final NotificationCompat.Builder b = new NotificationCompat.Builder(context, CHANNEL)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(res.getString(weigh ? R.string.helio_reminder_weigh_title : R.string.helio_reminder_day_title))
                .setContentText(res.getString(weigh ? R.string.helio_reminder_weigh_text : R.string.helio_reminder_day_text))
                .setCategory(NotificationCompat.CATEGORY_REMINDER)
                .setContentIntent(content)
                .setAutoCancel(true);
        NotificationManagerCompat.from(context).notify(CHANNEL, weigh ? 2 : 1, b.build());
    }

    /** For the page (GET /api/android/reminders and window.helioReminders): next times in seconds. */
    @NonNull
    static JSONObject status(@NonNull final Context context, final long now, final boolean asked) {
        final File stateDir = HelioPythonBackend.stateDir(context);
        final JSONObject o = new JSONObject();
        try {
            o.put("permission", permission(context));
            o.put("asked", asked);
            final JSONObject next = new JSONObject();
            for (final String kind : KINDS) {
                final long at = next(rule(stateDir, kind), now, TimeZone.getDefault());
                next.put(kind, at > 0 ? at / 1000 : JSONObject.NULL);
            }
            o.put("next", next);
        } catch (final JSONException e) {
            throw new IllegalStateException(e);
        }
        return o;
    }
}
