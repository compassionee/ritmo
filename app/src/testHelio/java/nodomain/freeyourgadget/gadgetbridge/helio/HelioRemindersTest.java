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
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.Manifest;
import android.app.AlarmManager;
import android.app.Application;
import android.app.Notification;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Looper;

import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.shadows.ShadowAlarmManager;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Calendar;
import java.util.TimeZone;

/**
 * Local reminders: times (weekdays, the clock changes), conditions read from the data files in Helio's format, the
 * notification permission (never asked when nothing is on; refused: nothing shown, the page told), late alarms.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioRemindersTest {
    private static final TimeZone ROME = TimeZone.getTimeZone("Europe/Rome");
    private TimeZone savedZone;
    private Application app;
    private File state;

    @Before
    public void setUp() {
        savedZone = TimeZone.getDefault();
        TimeZone.setDefault(ROME);
        app = RuntimeEnvironment.getApplication();
        state = HelioPythonBackend.stateDir(app);
        //noinspection ResultOfMethodCallIgnored
        state.mkdirs();
    }

    @After
    public void tearDown() {
        TimeZone.setDefault(savedZone);
        for (final File f : state.listFiles()) {
            //noinspection ResultOfMethodCallIgnored
            f.delete();
        }
    }

    private static long at(final int y, final int mo, final int d, final int h, final int mi) {
        final Calendar c = Calendar.getInstance(ROME);
        c.clear();
        c.set(y, mo - 1, d, h, mi, 0);
        return c.getTimeInMillis();
    }

    private void write(final String name, final String json) throws IOException {
        Files.write(new File(state, name).toPath(), json.getBytes(StandardCharsets.UTF_8));
    }

    /** settings.json as helio_app.save_settings writes it. */
    private void settings(final boolean dayOn, final String dayTime, final boolean weighOn, final String weighTime,
                          final String days) throws IOException {
        write("settings.json", "{\"lang\": null, \"hints\": [], \"save_mine\": false, \"reminders\": {\"day\": {\"on\": "
                + dayOn + ", \"time\": \"" + dayTime + "\"}, \"weigh\": {\"on\": " + weighOn + ", \"time\": \"" + weighTime
                + "\", \"days\": " + days + "}}}");
    }

    private void allowNotifications(final boolean allowed) {
        if (allowed) {
            shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS);
        } else {
            shadowOf(app).denyPermissions(Manifest.permission.POST_NOTIFICATIONS);
        }
    }

    private int shown() {
        return shadowOf((NotificationManager) app.getSystemService(Context.NOTIFICATION_SERVICE)).getAllNotifications().size();
    }

    // ------------------------------------------------------------ times

    @Test
    public void eveningTimeTodayOrTomorrow() {
        final HelioReminders.Rule rule = new HelioReminders.Rule(true, 21, 30, null);
        assertEquals(at(2026, 11, 3, 21, 30), HelioReminders.next(rule, at(2026, 11, 3, 18, 0), ROME));
        // at the time exactly or after it: tomorrow
        assertEquals(at(2026, 11, 4, 21, 30), HelioReminders.next(rule, at(2026, 11, 3, 21, 30), ROME));
        assertEquals(at(2026, 11, 4, 21, 30), HelioReminders.next(rule, at(2026, 11, 3, 23, 59), ROME));
        // off: nothing
        assertEquals(-1, HelioReminders.next(new HelioReminders.Rule(false, 21, 30, null), at(2026, 11, 3, 18, 0), ROME));
    }

    @Test
    public void morningOnlyOnTheChosenDays() {
        // Monday, Wednesday, Friday at 07:00; 3 November 2026 is a Tuesday
        final HelioReminders.Rule rule = new HelioReminders.Rule(true, 7, 0, new int[]{1, 3, 5});
        assertEquals(at(2026, 11, 4, 7, 0), HelioReminders.next(rule, at(2026, 11, 3, 6, 0), ROME));
        assertEquals(at(2026, 11, 6, 7, 0), HelioReminders.next(rule, at(2026, 11, 4, 7, 0), ROME));
        // Friday after the time: next Monday
        assertEquals(at(2026, 11, 9, 7, 0), HelioReminders.next(rule, at(2026, 11, 6, 8, 0), ROME));
        // Sunday only (7)
        assertEquals(at(2026, 11, 8, 7, 0), HelioReminders.next(new HelioReminders.Rule(true, 7, 0, new int[]{7}),
                at(2026, 11, 3, 6, 0), ROME));
        // no day: never
        assertEquals(-1, HelioReminders.next(new HelioReminders.Rule(true, 7, 0, new int[0]), at(2026, 11, 3, 6, 0), ROME));
    }

    @Test
    public void clockChanges() {
        // the clock goes forward on 29 March 2026 at 02:00: 02:30 does not exist, the reminder comes at 03:30
        final HelioReminders.Rule night = new HelioReminders.Rule(true, 2, 30, null);
        final long forward = HelioReminders.next(night, at(2026, 3, 29, 0, 0), ROME);
        assertEquals("2026-03-29", HelioReminders.isoDate(forward, ROME));
        assertTrue(forward > at(2026, 3, 29, 0, 0));
        // the clock goes back on 25 October 2026: 21:30 is still 21:30 local time, 25 hours after the day before
        final HelioReminders.Rule evening = new HelioReminders.Rule(true, 21, 30, null);
        final long before = HelioReminders.next(evening, at(2026, 10, 24, 12, 0), ROME);
        final long after = HelioReminders.next(evening, before, ROME);
        assertEquals(25 * 3600 * 1000L, after - before);
        final Calendar c = Calendar.getInstance(ROME);
        c.setTimeInMillis(after);
        assertEquals(21, c.get(Calendar.HOUR_OF_DAY));
        assertEquals(30, c.get(Calendar.MINUTE));
    }

    @Test
    public void rulesFromSettingsJson() throws IOException {
        // no file, no reminders key: off
        assertFalse(HelioReminders.rule(state, HelioReminders.DAY).on);
        assertFalse(HelioReminders.anyOn(state));
        settings(true, "22:15", true, "06:45", "[1, 3, 5]");
        final HelioReminders.Rule day = HelioReminders.rule(state, HelioReminders.DAY);
        assertTrue(day.on);
        assertEquals(22, day.hour);
        assertEquals(15, day.minute);
        final HelioReminders.Rule weigh = HelioReminders.rule(state, HelioReminders.WEIGH);
        assertTrue(weigh.days[1] && weigh.days[3] && weigh.days[5]);
        assertFalse(weigh.days[2] || weigh.days[7]);
        // on without days, damaged values: off or defaults, never a crash
        settings(true, "25:00", true, "x", "[]");
        assertFalse(HelioReminders.rule(state, HelioReminders.WEIGH).on);
        assertEquals(21, HelioReminders.rule(state, HelioReminders.DAY).hour);
        write("settings.json", "{\"reminders\": {\"day\": {\"on\": \"true\"}}}");
        assertFalse(HelioReminders.rule(state, HelioReminders.DAY).on);
        write("settings.json", "not json");
        assertFalse(HelioReminders.anyOn(state));
    }

    // ------------------------------------------------------------ conditions

    @Test
    public void conditionsReadHelioFiles() throws IOException {
        // the same format as helio_app (test_reminders.py checks it on the Python side)
        assertTrue(HelioReminders.needed(state, HelioReminders.DAY, "2026-11-03"));
        assertTrue(HelioReminders.needed(state, HelioReminders.WEIGH, "2026-11-03"));
        write("diary-2026-11.json", "{\"2026-11-02\": {\"entries\": [], \"complete\": true, \"saved\": 1},"
                + " \"2026-11-03\": {\"entries\": [{\"id\": \"a\"}], \"complete\": false, \"saved\": 1}}");
        write("weights.json", "{\"2026-11-02\": 72.4}");
        assertTrue(HelioReminders.needed(state, HelioReminders.DAY, "2026-11-03"));    // entries but not marked
        assertFalse(HelioReminders.needed(state, HelioReminders.DAY, "2026-11-02"));
        assertTrue(HelioReminders.needed(state, HelioReminders.WEIGH, "2026-11-03"));  // only yesterday's weight
        assertFalse(HelioReminders.needed(state, HelioReminders.WEIGH, "2026-11-02"));
        write("diary-2026-11.json", "{\"2026-11-03\": {\"entries\": [], \"complete\": true}}");
        write("weights.json", "{\"2026-11-03\": 72}");
        assertFalse(HelioReminders.needed(state, HelioReminders.DAY, "2026-11-03"));
        assertFalse(HelioReminders.needed(state, HelioReminders.WEIGH, "2026-11-03"));
        // "true" as a string, a weight that is not a number: not marked, not weighed
        write("diary-2026-11.json", "{\"2026-11-03\": {\"complete\": \"true\"}}");
        write("weights.json", "{\"2026-11-03\": \"72\"}");
        assertTrue(HelioReminders.needed(state, HelioReminders.DAY, "2026-11-03"));
        assertTrue(HelioReminders.needed(state, HelioReminders.WEIGH, "2026-11-03"));
    }

    // ------------------------------------------------------------ alarms and notifications

    @Test
    public void scheduleSetsAndCancelsTheAlarms() throws IOException {
        final ShadowAlarmManager alarms = shadowOf((AlarmManager) app.getSystemService(Context.ALARM_SERVICE));
        HelioReminders.schedule(app, at(2026, 11, 3, 12, 0));
        assertEquals(0, alarms.getScheduledAlarms().size());  // off by default: no alarm
        settings(true, "21:30", false, "07:30", "[1, 2, 3, 4, 5, 6, 7]");
        HelioReminders.schedule(app, at(2026, 11, 3, 12, 0));
        assertEquals(1, alarms.getScheduledAlarms().size());
        assertEquals(at(2026, 11, 3, 21, 30), alarms.getScheduledAlarms().get(0).getTriggerAtMs());
        settings(true, "21:30", true, "07:30", "[3]");
        HelioReminders.schedule(app, at(2026, 11, 3, 12, 0));
        assertEquals(2, alarms.getScheduledAlarms().size());
        settings(false, "21:30", false, "07:30", "[3]");
        HelioReminders.schedule(app, at(2026, 11, 3, 12, 0));
        assertEquals(0, alarms.getScheduledAlarms().size());
    }

    @Test
    public void shownOnlyWhenNeededAndAllowed() throws IOException {
        settings(true, "21:30", true, "07:30", "[2]");
        allowNotifications(true);
        final long evening = at(2026, 11, 3, 21, 30);
        assertTrue(HelioReminders.fire(app, HelioReminders.DAY, evening, evening + 1000));
        assertEquals(1, shown());
        final Notification n = shadowOf((NotificationManager) app.getSystemService(Context.NOTIFICATION_SERVICE))
                .getAllNotifications().get(0);
        assertEquals(HelioReminders.CHANNEL, n.getChannelId());
        assertNotNull(n.contentIntent);
        // the day marked complete: nothing more
        write("diary-2026-11.json", "{\"2026-11-03\": {\"entries\": [], \"complete\": true}}");
        assertFalse(HelioReminders.fire(app, HelioReminders.DAY, evening, evening + 1000));
        // weighed this morning: nothing
        write("weights.json", "{\"2026-11-03\": 71.8}");
        final long morning = at(2026, 11, 3, 7, 30);
        assertFalse(HelioReminders.fire(app, HelioReminders.WEIGH, morning, morning + 1000));
        write("weights.json", "{\"2026-11-02\": 71.8}");
        assertTrue(HelioReminders.fire(app, HelioReminders.WEIGH, morning, morning + 1000));
        assertEquals(2, shown());
        // the next alarm is always set
        final ShadowAlarmManager alarms = shadowOf((AlarmManager) app.getSystemService(Context.ALARM_SERVICE));
        assertEquals(2, alarms.getScheduledAlarms().size());
    }

    @Test
    public void lateOrTurnedOffAlarmsShowNothing() throws IOException {
        settings(true, "23:50", false, "07:30", "[1]");
        allowNotifications(true);
        final long late = at(2026, 11, 3, 23, 50);
        // delivered after midnight more than two hours late: "today" is already another day
        assertFalse(HelioReminders.fire(app, HelioReminders.DAY, late, late + HelioReminders.STALE_MS + 1));
        assertFalse(HelioReminders.fire(app, HelioReminders.DAY, 0, late));
        // turned off in the meantime
        assertFalse(HelioReminders.fire(app, HelioReminders.WEIGH, at(2026, 11, 3, 7, 30), at(2026, 11, 3, 7, 30)));
        assertFalse(HelioReminders.fire(app, "other", late, late));
        assertEquals(0, shown());
    }

    @Test
    public void permissionDeniedShowsNothingButKeepsTheAlarm() throws IOException {
        settings(true, "21:30", false, "07:30", "[1]");
        allowNotifications(false);
        assertEquals(HelioReminders.PERM_ASK, HelioReminders.permission(app));
        final long evening = at(2026, 11, 3, 21, 30);
        assertFalse(HelioReminders.fire(app, HelioReminders.DAY, evening, evening + 1000));
        assertEquals(0, shown());
        final ShadowAlarmManager alarms = shadowOf((AlarmManager) app.getSystemService(Context.ALARM_SERVICE));
        assertEquals(1, alarms.getScheduledAlarms().size());  // allowed later from Android's settings: it works again
        // granted, but Ritmo's notifications turned off in Android's settings
        allowNotifications(true);
        shadowOf((NotificationManager) app.getSystemService(Context.NOTIFICATION_SERVICE)).setNotificationsEnabled(false);
        assertEquals(HelioReminders.PERM_OFF, HelioReminders.permission(app));
        assertFalse(HelioReminders.fire(app, HelioReminders.DAY, evening, evening + 1000));
        assertEquals(0, shown());
    }

    @Test
    public void receiverOnlyTakesItsOwnAction() throws IOException {
        settings(true, "21:30", false, "07:30", "[1]");
        allowNotifications(true);
        new HelioReminderReceiver().onReceive(app, new Intent("other").putExtra(HelioReminders.EXTRA_KIND, "day"));
        assertEquals(0, shown());
        final long now = System.currentTimeMillis();
        new HelioReminderReceiver().onReceive(app, new Intent(HelioReminders.ACTION_FIRE)
                .putExtra(HelioReminders.EXTRA_KIND, HelioReminders.DAY).putExtra(HelioReminders.EXTRA_AT, now));
        assertEquals(1, shown());
        // the restart receiver only sets the alarms again
        final ShadowAlarmManager alarms = shadowOf((AlarmManager) app.getSystemService(Context.ALARM_SERVICE));
        new HelioReminderBootReceiver().onReceive(app, new Intent(Intent.ACTION_BOOT_COMPLETED));
        assertEquals(1, alarms.getScheduledAlarms().size());
        assertEquals(1, shown());
    }

    @Test
    public void notificationOpensTheRightSection() {
        assertEquals("{\"tab\":\"food\"}", HelioActivity.tabFromNotification(new Intent().putExtra(HelioReminders.EXTRA_TAB, "food")));
        assertEquals("{\"tab\":\"profile\"}", HelioActivity.tabFromNotification(new Intent().putExtra(HelioReminders.EXTRA_TAB, "profile")));
        assertNull(HelioActivity.tabFromNotification(new Intent().putExtra(HelioReminders.EXTRA_TAB, "x\"}")));
        assertNull(HelioActivity.tabFromNotification(new Intent()));
    }

    @Test
    public void statusForThePage() throws Exception {
        settings(true, "21:30", true, "07:30", "[3]");
        allowNotifications(false);
        final JSONObject s = HelioReminders.status(app, at(2026, 11, 3, 12, 0), true);
        assertEquals("ask", s.getString("permission"));
        assertTrue(s.getBoolean("asked"));
        assertEquals(at(2026, 11, 3, 21, 30) / 1000, s.getJSONObject("next").getLong("day"));
        assertEquals(at(2026, 11, 4, 7, 30) / 1000, s.getJSONObject("next").getLong("weigh"));
    }

    // ------------------------------------------------------------ the permission, asked by Ritmo

    private HelioActivityLifecycleTest.TestHelioActivity activity() {
        return Robolectric.buildActivity(HelioActivityLifecycleTest.TestHelioActivity.class).setup().get();
    }

    private String lastScript(final HelioActivityLifecycleTest.TestHelioActivity a) {
        shadowOf(Looper.getMainLooper()).idle();
        return shadowOf(a.webView()).getLastEvaluatedJavascript();
    }

    @Test
    public void permissionIsAskedOnlyWhenAReminderIsTurnedOn() throws Exception {
        allowNotifications(false);
        final HelioActivityLifecycleTest.TestHelioActivity a = activity();
        // all off (the page saved a time, for example): no dialog
        settings(false, "21:30", false, "07:30", "[1]");
        a.remindersChanged();
        assertNull(shadowOf(a).getLastRequestedPermission());
        assertTrue(lastScript(a).contains("helioReminders"));
        // one on: Android's dialog
        settings(true, "21:30", false, "07:30", "[1]");
        a.remindersChanged();
        assertNotNull(shadowOf(a).getLastRequestedPermission());
        assertEquals(Manifest.permission.POST_NOTIFICATIONS, shadowOf(a).getLastRequestedPermission().requestedPermissions[0]);
    }

    @Test
    public void refusedPermissionIsToldToThePage() throws Exception {
        allowNotifications(false);
        settings(true, "21:30", false, "07:30", "[1]");
        final HelioActivityLifecycleTest.TestHelioActivity a = activity();
        a.remindersChanged();
        final int code = shadowOf(a).getLastRequestedPermission().requestCode;
        a.onRequestPermissionsResult(code, new String[]{Manifest.permission.POST_NOTIFICATIONS},
                new int[]{PackageManager.PERMISSION_DENIED});
        final String js = lastScript(a);
        final JSONObject status = new JSONObject(js.substring(js.indexOf("helioReminders(") + "helioReminders(".length(),
                js.lastIndexOf(')')));
        assertEquals("ask", status.getString("permission"));
        assertTrue(status.getBoolean("asked"));  // the page turns the reminders off and says why
        assertEquals(0, shown());
    }

    @Test
    public void grantedPermissionIsNotAskedAgain() throws Exception {
        allowNotifications(true);
        settings(false, "21:30", true, "07:30", "[1, 2, 3, 4, 5, 6, 7]");
        final HelioActivityLifecycleTest.TestHelioActivity a = activity();
        a.remindersChanged();
        assertNull(shadowOf(a).getLastRequestedPermission());
        final String js = lastScript(a);
        assertTrue(js, js.contains("\"permission\":\"granted\""));
        assertTrue(js, js.contains("\"asked\":false"));
        final ShadowAlarmManager alarms = shadowOf((AlarmManager) app.getSystemService(Context.ALARM_SERVICE));
        assertEquals(1, alarms.getScheduledAlarms().size());
    }
}
