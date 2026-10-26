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
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.app.Application;
import android.content.ComponentCallbacks2;
import android.net.Uri;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.ValueCallback;
import android.webkit.WebView;

import androidx.annotation.NonNull;

import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;

/**
 * Helio in the background: Home, another app or the screen turning off must leave the activity
 * (and the :helio process) alive, in the recent apps; only Back from Today closes it.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioActivityLifecycleTest {
    /** HelioActivity without the parts that need a real WebView provider or ending the process. */
    public static class TestHelioActivity extends HelioActivity {
        String pageBackAnswer = "false";
        int endProcessCalls;

        @Override
        boolean webViewSupported() {
            return true;
        }

        @Override
        void installBridge(@NonNull final WebView webView) {
        }

        @Override
        void blockServiceWorkers() {
        }

        @Override
        void askPageBack(@NonNull final ValueCallback<String> result) {
            result.onReceiveValue(pageBackAnswer);
        }

        @Override
        void endProcess() {
            endProcessCalls++;
        }

        WebView webView() {
            return (WebView) ((ViewGroup) findViewById(android.R.id.content)).getChildAt(0);
        }
    }

    private static ActivityController<TestHelioActivity> start(final Bundle saved) {
        final ActivityController<TestHelioActivity> c = Robolectric.buildActivity(TestHelioActivity.class);
        return saved == null ? c.setup() : c.setup(saved);
    }

    @Test
    public void homeAndScreenOffKeepHelio() {
        final ActivityController<TestHelioActivity> c = start(null);
        final TestHelioActivity a = c.get();
        assertEquals(HelioWebRouter.START_URL, shadowOf(a.webView()).getLastLoadedUrl());

        // Home: onUserLeaveHint, onPause, onStop, onSaveInstanceState, then the memory callbacks
        c.userLeaving().pause().saveInstanceState(new Bundle()).stop();
        a.onTrimMemory(ComponentCallbacks2.TRIM_MEMORY_UI_HIDDEN);
        a.onTrimMemory(ComponentCallbacks2.TRIM_MEMORY_BACKGROUND);

        assertFalse(a.isFinishing());
        assertFalse(a.isDestroyed());
        assertEquals(0, a.endProcessCalls);
        assertTrue(shadowOf(a.webView()).wasOnPauseCalled());
    }

    @Test
    public void backToHelioWhereItWas() {
        final ActivityController<TestHelioActivity> c = start(null);
        final TestHelioActivity a = c.get();
        final WebView webView = a.webView();
        c.pause().stop();
        c.restart().start().resume();
        assertSame(a, c.get());
        assertSame(webView, a.webView());  // the same page, not reloaded
        assertTrue(shadowOf(webView).wasOnResumeCalled());
        assertEquals(HelioWebRouter.START_URL, shadowOf(webView).getLastLoadedUrl());
        assertFalse(a.isFinishing());
    }

    @Test
    public void backFromTodayClosesHelioAndItsProcess() {
        final ActivityController<TestHelioActivity> c = start(null);
        final TestHelioActivity a = c.get();
        a.pageBackAnswer = "false";
        a.onHelioBack();
        assertTrue(a.isFinishing());
        c.pause().stop().destroy();
        assertEquals(1, a.endProcessCalls);
    }

    @Test
    public void backInsideThePageKeepsHelio() {
        final ActivityController<TestHelioActivity> c = start(null);
        final TestHelioActivity a = c.get();
        for (final String answer : new String[]{"true", "true"}) {
            a.pageBackAnswer = answer;
            a.onHelioBack();
        }
        assertFalse(a.isFinishing());
        // and also with the system key (onBackPressed, API < 33 or without predictive back)
        a.onBackPressed();
        assertFalse(a.isFinishing());
    }

    @Test
    public void processEndedInBackgroundRestoresTheView() throws Exception {
        // first life: the page sends the view, Android saves the state and kills the process
        final ActivityController<TestHelioActivity> first = start(null);
        final HelioWebRouter router = first.get().router();
        assertNotNull(router);
        router.handleBridgeMessage(new JSONObject().put("type", "view").put("view",
                new JSONObject().put("tab", "sleep").put("date", "2026-10-11").put("y", 420)).toString(),
                Uri.parse(HelioWebRouter.ORIGIN), true);
        final Bundle saved = new Bundle();
        first.pause().saveInstanceState(saved).stop();
        assertEquals(0, first.get().endProcessCalls);

        // second life: new activity with the saved state
        final TestHelioActivity second = start(saved).get();
        final Uri url = Uri.parse(shadowOf(second.webView()).getLastLoadedUrl());
        assertTrue(HelioWebRouter.isAppUrl(url));
        final JSONObject view = new JSONObject(url.getFragment().substring("view=".length()));
        assertEquals("sleep", view.getString("tab"));
        assertEquals("2026-10-11", view.getString("date"));
        assertEquals(420, view.getInt("y"));
    }

    @Test
    public void removedFromRecentsEndsTheProcess() {
        // removing Helio from recents destroys the "finishing" activity: the process ends with it
        final ActivityController<TestHelioActivity> c = start(null);
        c.get().finishAndRemoveTask();
        c.pause().stop().destroy();
        assertEquals(1, c.get().endProcessCalls);
    }

    @Test
    public void configurationRecreationDoesNotEndTheProcess() {
        final ActivityController<TestHelioActivity> c = start(null);
        final TestHelioActivity old = c.get();
        c.recreate();
        assertTrue(old.isDestroyed());
        assertEquals(0, old.endProcessCalls);
        assertFalse(c.get().isFinishing());
    }
}
