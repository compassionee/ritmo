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

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.app.Application;
import android.net.Uri;
import android.webkit.WebResourceResponse;

import androidx.annotation.NonNull;
import androidx.webkit.WebViewAssetLoader;

import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

import java.io.ByteArrayInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import nodomain.freeyourgadget.gadgetbridge.helio.HelioWebRouter.Navigation;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, application = Application.class)
public class HelioWebRouterTest {
    private static final Uri APP_ORIGIN = Uri.parse(HelioWebRouter.ORIGIN);

    /** Records the calls and answers like Helio's Handler. */
    static class FakeBackend implements HelioBackend {
        final List<String[]> calls = new ArrayList<>();
        final List<Map<String, String>> headers = new ArrayList<>();
        final List<byte[]> bodies = new ArrayList<>();
        Exception failure;
        /** When set, the answer of the Python side to an action: status and approved action. */
        int actionStatus = 200;
        String approvedAction;

        @NonNull
        @Override
        public HelioResponse handle(@NonNull final String method, @NonNull final String pathAndQuery,
                                    @NonNull final Map<String, String> h, @NonNull final byte[] body) throws Exception {
            if (failure != null) {
                throw failure;
            }
            calls.add(new String[]{method, pathAndQuery});
            headers.add(h);
            bodies.add(body);
            if (approvedAction != null) {
                final List<String[]> rh = new ArrayList<>();
                rh.add(new String[]{"Content-Type", "application/json"});
                if (actionStatus == 200) {
                    rh.add(new String[]{HelioWebRouter.ACTION_HEADER, approvedAction});
                }
                return new HelioResponse(actionStatus, "X", rh, "{}".getBytes(StandardCharsets.UTF_8));
            }
            return new HelioResponse(200, "OK", Arrays.asList(
                    new String[]{"Content-Type", "application/json"},
                    new String[]{"Content-Length", "11"},
                    new String[]{"Content-Security-Policy", HelioStaticPathHandler.CSP},
                    new String[]{"Cache-Control", "no-store"}),
                    "{\"ok\": \"è\"}".getBytes(StandardCharsets.UTF_8));
        }
    }

    static final Map<String, String> ASSETS = new HashMap<>();

    static {
        ASSETS.put("helio/static/index.html", "<html><body><script src=\"app.js\"></script></body></html>");
        ASSETS.put("helio/static/app.js", "console.log(1)");
        ASSETS.put("helio/static/sw.js", "self.addEventListener('fetch', () => {})");
        ASSETS.put("helio/android/helio-android.js", "// shim");
    }

    static InputStream openAsset(final String path) throws IOException {
        final String text = ASSETS.get(path);
        if (text == null) {
            throw new FileNotFoundException(path);
        }
        return new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8));
    }

    private FakeBackend backend;
    private HelioWebRouter router;
    private final List<String> actionsRun = new ArrayList<>();
    private boolean gbRunning = true;

    @Before
    public void setUp() {
        backend = new FakeBackend();
        final WebViewAssetLoader loader = new WebViewAssetLoader.Builder()
                .setDomain(HelioWebRouter.HOST)
                .addPathHandler("/", new HelioStaticPathHandler(HelioWebRouterTest::openAsset))
                .build();
        router = new HelioWebRouter(backend, loader, actionsRun::add, () -> gbRunning);
    }

    private static String actionMessage(final int id, final String action) throws Exception {
        return new JSONObject().put("id", id).put("method", "POST").put("path", "/api/android/action")
                .put("headers", new JSONObject().put("content-type", "application/json").put("x-helio", "1"))
                .put("body", new JSONObject().put("action", action).toString())
                .toString();
    }

    private static String bodyOf(final WebResourceResponse response) throws IOException {
        final java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        final byte[] buf = new byte[4096];
        int n;
        while ((n = response.getData().read(buf)) > 0) {
            out.write(buf, 0, n);
        }
        return out.toString("UTF-8");
    }

    @Test
    public void appUrlIsOnlyTheHttpsAppOrigin() {
        assertTrue(HelioWebRouter.isAppUrl(Uri.parse("https://appassets.androidplatform.net/api/plan")));
        assertFalse(HelioWebRouter.isAppUrl(Uri.parse("http://appassets.androidplatform.net/")));
        assertFalse(HelioWebRouter.isAppUrl(Uri.parse("https://appassets.androidplatform.net:8443/")));
        assertFalse(HelioWebRouter.isAppUrl(Uri.parse("https://user@appassets.androidplatform.net/")));
        assertFalse(HelioWebRouter.isAppUrl(Uri.parse("https://appassets.androidplatform.net.example.org/")));
        assertFalse(HelioWebRouter.isAppUrl(Uri.parse("https://example.org/")));
        assertFalse(HelioWebRouter.isAppUrl(Uri.parse("file:///android_asset/helio/static/index.html")));
        assertFalse(HelioWebRouter.isAppUrl(null));
    }

    @Test
    public void externalLinksOnlyHttpsAndOnlyOnTap() {
        assertEquals(Navigation.ALLOW, HelioWebRouter.navigationFor(Uri.parse(HelioWebRouter.START_URL), false));
        assertEquals(Navigation.OPEN_EXTERNALLY,
                HelioWebRouter.navigationFor(Uri.parse("https://pubmed.ncbi.nlm.nih.gov/123/"), true));
        assertEquals(Navigation.BLOCK,
                HelioWebRouter.navigationFor(Uri.parse("https://pubmed.ncbi.nlm.nih.gov/123/"), false));
        for (final String url : new String[]{"http://example.org/", "intent://scan/#Intent;scheme=zxing;end",
                "javascript:alert(1)", "file:///sdcard/x", "content://x/y", "mailto:a@b.c", "https:///nohost"}) {
            assertEquals(url, Navigation.BLOCK, HelioWebRouter.navigationFor(Uri.parse(url), true));
        }
    }

    @Test
    public void otherOriginsGetAnEmpty403() throws IOException {
        for (final String url : new String[]{"https://example.org/x.js", "http://appassets.androidplatform.net/",
                "https://fonts.googleapis.com/css"}) {
            final WebResourceResponse r = router.intercept(Uri.parse(url), "GET", new HashMap<>());
            assertEquals(url, 403, r.getStatusCode());
            assertEquals("", bodyOf(r));
        }
        assertTrue(backend.calls.isEmpty());
    }

    @Test
    public void apiGetGoesToHelioWithQueryAndHeaders() throws IOException {
        final Map<String, String> headers = new HashMap<>();
        headers.put("Accept", "application/json");
        final WebResourceResponse r = router.intercept(
                Uri.parse(HelioWebRouter.ORIGIN + "/api/summary?date=2026-03-10&x=a%20b"), "GET", headers);
        assertEquals(1, backend.calls.size());
        assertArrayEquals(new String[]{"GET", "/api/summary?date=2026-03-10&x=a%20b"}, backend.calls.get(0));
        assertEquals("application/json", backend.headers.get(0).get("Accept"));
        assertEquals(200, r.getStatusCode());
        assertEquals("application/json", r.getMimeType());
        assertEquals(HelioStaticPathHandler.CSP, r.getResponseHeaders().get("Content-Security-Policy"));
        assertFalse(r.getResponseHeaders().containsKey("Content-Type"));
        assertFalse(r.getResponseHeaders().containsKey("Content-Length"));
        assertEquals("{\"ok\": \"è\"}", bodyOf(r));
    }

    @Test
    public void apiPostCannotBeIntercepted() {
        final WebResourceResponse r = router.intercept(Uri.parse(HelioWebRouter.ORIGIN + "/api/weight"), "POST", null);
        assertEquals(405, r.getStatusCode());
        assertTrue(backend.calls.isEmpty());
    }

    @Test
    public void staticFilesFromAssetsWithCspAndShim() throws IOException {
        final WebResourceResponse index = router.intercept(Uri.parse(HelioWebRouter.START_URL), "GET", null);
        assertEquals(200, index.getStatusCode());
        assertEquals("text/html", index.getMimeType());
        assertEquals(HelioStaticPathHandler.CSP, index.getResponseHeaders().get("Content-Security-Policy"));
        assertEquals("<html><body><script src=\"helio-android.js\"></script><script src=\"app.js\"></script></body></html>",
                bodyOf(index));

        final WebResourceResponse shim = router.intercept(Uri.parse(HelioWebRouter.ORIGIN + "/helio-android.js"), "GET", null);
        assertEquals("text/javascript", shim.getMimeType());
        assertEquals("// shim", bodyOf(shim));
        assertTrue(backend.calls.isEmpty());
    }

    @Test
    public void serviceWorkerAndUnknownFilesAreMissing() {
        for (final String path : new String[]{"/sw.js", "/x.html", "/helio/static/app.js", "/../app.js", "/api"}) {
            final WebResourceResponse r = router.intercept(Uri.parse(HelioWebRouter.ORIGIN + path), "GET", null);
            assertEquals(path, 404, r.getStatusCode());
        }
    }

    @Test
    public void backendFailureIs500() throws Exception {
        backend.failure = new RuntimeException("boom");
        final WebResourceResponse r = router.intercept(Uri.parse(HelioWebRouter.ORIGIN + "/api/plan"), "GET", null);
        assertEquals(500, r.getStatusCode());
        assertEquals("err.internal", new JSONObject(bodyOf(r)).getJSONObject("error").getString("$"));
    }

    @Test
    public void bridgePostReachesHelioUnchanged() throws Exception {
        final String msg = new JSONObject()
                .put("id", 7).put("method", "POST").put("path", "/api/weight")
                .put("headers", new JSONObject().put("content-type", "application/json").put("x-helio", "1"))
                .put("body", "{\"date\":\"2026-03-10\",\"kg\":80,\"nota\":\"è\"}")
                .toString();
        final JSONObject reply = new JSONObject(router.handleBridgeMessage(msg, APP_ORIGIN, true));
        assertArrayEquals(new String[]{"POST", "/api/weight"}, backend.calls.get(0));
        assertEquals("1", backend.headers.get(0).get("x-helio"));
        assertEquals("application/json", backend.headers.get(0).get("content-type"));
        assertArrayEquals("{\"date\":\"2026-03-10\",\"kg\":80,\"nota\":\"è\"}".getBytes(StandardCharsets.UTF_8),
                backend.bodies.get(0));
        assertEquals(7, reply.getInt("id"));
        assertEquals(200, reply.getInt("status"));
        assertEquals("{\"ok\": \"è\"}", reply.getString("body"));
        assertFalse(reply.getJSONObject("headers").has("Content-Length"));
        assertEquals("application/json", reply.getJSONObject("headers").getString("Content-Type"));
    }

    @Test
    public void bridgeIgnoresOtherOriginsAndFrames() throws Exception {
        final String msg = new JSONObject().put("id", 1).put("method", "POST").put("path", "/api/weight")
                .put("body", "{}").toString();
        assertNull(router.handleBridgeMessage(msg, Uri.parse("https://example.org"), true));
        assertNull(router.handleBridgeMessage(msg, Uri.parse("http://appassets.androidplatform.net"), true));
        assertNull(router.handleBridgeMessage(msg, APP_ORIGIN, false));
        assertNull(router.handleBridgeMessage(msg, null, true));
        assertNull(router.handleBridgeMessage("not json", APP_ORIGIN, true));
        assertTrue(backend.calls.isEmpty());
    }

    @Test
    public void bridgeOnlyPostToApi() throws Exception {
        for (final String[] c : new String[][]{{"GET", "/api/plan"}, {"POST", "/index.html"}, {"PUT", "/api/plan"}}) {
            final String msg = new JSONObject().put("id", 3).put("method", c[0]).put("path", c[1]).toString();
            final JSONObject reply = new JSONObject(router.handleBridgeMessage(msg, APP_ORIGIN, true));
            assertEquals(400, reply.getInt("status"));
        }
        assertTrue(backend.calls.isEmpty());
    }

    @Test
    public void oversizedMessageGets400WithoutParsing() throws Exception {
        final StringBuilder body = new StringBuilder();
        while (body.length() <= HelioWebRouter.MAX_MESSAGE_CHARS) {
            body.append("xxxxxxxxxxxxxxxx");
        }
        final String msg = "{\"id\":42,\"method\":\"POST\",\"path\":\"/api/plan\",\"headers\":{},\"body\":\"" + body + "\"}";
        final JSONObject reply = new JSONObject(router.handleBridgeMessage(msg, APP_ORIGIN, true));
        assertEquals(42, reply.getInt("id"));
        assertEquals(400, reply.getInt("status"));
        assertTrue(backend.calls.isEmpty());
    }

    @Test
    public void webResponseNeverInvalid() {
        final WebResourceResponse redirect = HelioWebRouter.toWebResponse(
                new HelioResponse(302, "Found", new ArrayList<>(), new byte[0]));
        assertEquals(500, redirect.getStatusCode());
        final WebResourceResponse noReason = HelioWebRouter.toWebResponse(new HelioResponse(404, "",
                Arrays.asList(new String[][]{{"Content-Type", "text/csv; charset=utf-8"}}), new byte[0]));
        assertEquals(404, noReason.getStatusCode());
        assertEquals("Error", noReason.getReasonPhrase());
        assertEquals("text/csv", noReason.getMimeType());
        assertEquals("utf-8", noReason.getEncoding());
    }

    @Test
    public void approvedActionRunsOnceFromTheAppOrigin() throws Exception {
        for (final String action : HelioGbActions.ALL) {
            backend.approvedAction = action;
            final JSONObject reply = new JSONObject(router.handleBridgeMessage(actionMessage(5, action), APP_ORIGIN, true));
            assertEquals(200, reply.getInt("status"));
        }
        assertEquals(HelioGbActions.ALL.size(), actionsRun.size());
        assertTrue(actionsRun.containsAll(HelioGbActions.ALL));
        assertArrayEquals(new String[]{"POST", "/api/android/action"}, backend.calls.get(0));
    }

    @Test
    public void actionsNeverRunFromOtherOrigins() throws Exception {
        backend.approvedAction = HelioGbActions.SYNC;
        final String msg = actionMessage(1, HelioGbActions.SYNC);
        for (final String origin : new String[]{"https://example.org", "http://appassets.androidplatform.net",
                "https://appassets.androidplatform.net:8443", "https://appassets.androidplatform.net.evil.org",
                "null", "file://", "content://nodomain.freeyourgadget.gadgetbridge.helio.screenshot_provider"}) {
            assertNull(origin, router.handleBridgeMessage(msg, Uri.parse(origin), true));
        }
        assertNull(router.handleBridgeMessage(msg, null, true));
        // not even from an iframe of the page
        assertNull(router.handleBridgeMessage(msg, APP_ORIGIN, false));
        assertTrue(actionsRun.isEmpty());
        assertTrue(backend.calls.isEmpty());
    }

    @Test
    public void pullRunsSyncOnlyWhenPythonApprovesIt() throws Exception {
        // pull down: Python answers with X-Helio-Action: sync only if a sync is needed
        backend.approvedAction = HelioGbActions.SYNC;
        router.handleBridgeMessage(actionMessage(6, "pull"), APP_ORIGIN, true);
        assertEquals(Collections.singletonList(HelioGbActions.SYNC), actionsRun);
        // sync in progress or recent: answer without approval, no action
        backend.approvedAction = null;
        final JSONObject reply = new JSONObject(router.handleBridgeMessage(actionMessage(7, "pull"), APP_ORIGIN, true));
        assertEquals(200, reply.getInt("status"));
        assertEquals(1, actionsRun.size());
        // "pull" is not a Gadgetbridge action: even if approved like this, nothing starts
        backend.approvedAction = "pull";
        router.handleBridgeMessage(actionMessage(8, "pull"), APP_ORIGIN, true);
        assertEquals(1, actionsRun.size());
    }

    @Test
    public void pullNeverRunsFromOtherOriginsOrFrames() throws Exception {
        backend.approvedAction = HelioGbActions.SYNC;
        final String msg = actionMessage(9, "pull");
        for (final String origin : new String[]{"https://example.org", "http://appassets.androidplatform.net",
                "https://appassets.androidplatform.net.evil.org", "null"}) {
            assertNull(origin, router.handleBridgeMessage(msg, Uri.parse(origin), true));
        }
        assertNull(router.handleBridgeMessage(msg, APP_ORIGIN, false));
        router.intercept(Uri.parse(HelioWebRouter.ORIGIN + "/api/android/action"), "POST", new HashMap<>());
        assertTrue(actionsRun.isEmpty());
        assertTrue(backend.calls.isEmpty());
    }

    @Test
    public void pullCarriesTheGadgetbridgeProcessState() throws Exception {
        // Python ignores an old "busy" if Gadgetbridge is not running: the router adds that information
        gbRunning = false;
        final String msg = new JSONObject(actionMessage(10, "pull"))
                .put("headers", new JSONObject().put("content-type", "application/json").put("x-helio", "1")
                        .put("x-helio-gb-process", "running")).toString();
        router.handleBridgeMessage(msg, APP_ORIGIN, true);
        assertEquals("stopped", backend.headers.get(0).get(HelioWebRouter.GB_PROCESS_HEADER));
        assertFalse(backend.headers.get(0).containsKey("x-helio-gb-process"));
    }

    private static String viewMessage(final JSONObject view) throws Exception {
        return new JSONObject().put("type", "view").put("view", view).toString();
    }

    @Test
    public void viewIsKeptWithoutReplyAndWithoutPython() throws Exception {
        assertNull(router.savedView());
        final JSONObject view = new JSONObject().put("tab", "sleep").put("date", "2026-10-11").put("y", 420);
        assertNull(router.handleBridgeMessage(viewMessage(view), APP_ORIGIN, true));
        assertEquals(view.toString(), new JSONObject(router.savedView()).toString());
        assertTrue(backend.calls.isEmpty());
        assertTrue(actionsRun.isEmpty());
    }

    @Test
    public void viewKeepsOnlyValidFields() throws Exception {
        router.handleBridgeMessage(viewMessage(new JSONObject().put("tab", "Fitness<script>").put("date", "2026-10-11")
                .put("y", -5).put("extra", "x")), APP_ORIGIN, true);
        assertEquals("{\"date\":\"2026-10-11\"}", router.savedView());
        // nothing valid: the previous view stays
        router.handleBridgeMessage(viewMessage(new JSONObject().put("tab", "a".repeat(17)).put("date", "11/10/2026")
                .put("y", HelioWebRouter.MAX_VIEW_SCROLL + 1)), APP_ORIGIN, true);
        router.handleBridgeMessage(new JSONObject().put("type", "view").put("view", "sleep").toString(), APP_ORIGIN, true);
        router.handleBridgeMessage(viewMessage(new JSONObject().put("y", "420")), APP_ORIGIN, true);
        assertEquals("{\"date\":\"2026-10-11\"}", router.savedView());
    }

    @Test
    public void viewFromOtherOriginsOrFramesIsIgnored() throws Exception {
        final String msg = viewMessage(new JSONObject().put("tab", "food"));
        assertNull(router.handleBridgeMessage(msg, Uri.parse("https://example.org"), true));
        assertNull(router.handleBridgeMessage(msg, APP_ORIGIN, false));
        assertNull(router.handleBridgeMessage(msg, null, true));
        assertNull(router.savedView());
    }

    @Test
    public void startUrlCarriesTheCheckedView() throws Exception {
        assertEquals(HelioWebRouter.START_URL, HelioWebRouter.startUrl(null));
        assertEquals(HelioWebRouter.START_URL, HelioWebRouter.startUrl("non json"));
        assertEquals(HelioWebRouter.START_URL, HelioWebRouter.startUrl("{\"tab\":\"../x\"}"));
        final String url = HelioWebRouter.startUrl("{\"tab\":\"fitness\",\"date\":\"2026-10-01\",\"y\":1200,\"x\":\"#\"}");
        final Uri uri = Uri.parse(url);
        assertTrue(HelioWebRouter.isAppUrl(uri));
        assertEquals("/", uri.getPath());
        assertTrue(uri.getEncodedFragment().startsWith("view="));
        final JSONObject back = new JSONObject(uri.getFragment().substring("view=".length()));
        assertEquals("fitness", back.getString("tab"));
        assertEquals("2026-10-01", back.getString("date"));
        assertEquals(1200, back.getInt("y"));
        assertFalse(back.has("x"));
        // the fragment never reaches the router: the start page is always the same
        assertEquals(-1, uri.getEncodedFragment().indexOf('#'));
    }

    @Test
    public void actionsNeverRunThroughInterception() {
        backend.approvedAction = HelioGbActions.SYNC;
        router.intercept(Uri.parse(HelioWebRouter.ORIGIN + "/api/android/action?action=sync"), "GET", new HashMap<>());
        router.intercept(Uri.parse(HelioWebRouter.ORIGIN + "/api/android/action"), "POST", new HashMap<>());
        router.intercept(Uri.parse("https://example.org/api/android/action"), "GET", new HashMap<>());
        assertTrue(actionsRun.isEmpty());
    }

    @Test
    public void actionsRejectedByPythonDoNotRun() throws Exception {
        backend.approvedAction = HelioGbActions.SYNC;
        for (final int status : new int[]{400, 403, 404, 500}) {
            backend.actionStatus = status;
            router.handleBridgeMessage(actionMessage(2, HelioGbActions.SYNC), APP_ORIGIN, true);
        }
        assertTrue(actionsRun.isEmpty());
    }

    @Test
    public void unknownOrMisplacedApprovalsDoNotRun() throws Exception {
        // an unknown action, even if approved
        backend.approvedAction = "reboot";
        router.handleBridgeMessage(actionMessage(3, "reboot"), APP_ORIGIN, true);
        // un'approvazione arrivata su un altro percorso
        backend.approvedAction = HelioGbActions.SYNC;
        final String other = new JSONObject().put("id", 4).put("method", "POST").put("path", "/api/weight")
                .put("body", "{}").toString();
        router.handleBridgeMessage(other, APP_ORIGIN, true);
        assertTrue(actionsRun.isEmpty());
    }

    @Test
    public void gadgetbridgeProcessHeaderComesOnlyFromTheRouter() throws Exception {
        final Map<String, String> spoofed = new HashMap<>();
        spoofed.put("x-helio-gb-process", "running");
        spoofed.put("X-Helio-Gb-Process", "running");
        gbRunning = false;
        router.intercept(Uri.parse(HelioWebRouter.ORIGIN + "/api/android/band"), "GET", spoofed);
        final Map<String, String> sent = backend.headers.get(0);
        assertEquals("stopped", sent.get(HelioWebRouter.GB_PROCESS_HEADER));
        assertFalse(sent.containsKey("x-helio-gb-process"));

        gbRunning = true;
        final String msg = new JSONObject().put("id", 9).put("method", "POST").put("path", "/api/weight")
                .put("headers", new JSONObject().put("X-HELIO-GB-PROCESS", "stopped")).put("body", "{}").toString();
        router.handleBridgeMessage(msg, APP_ORIGIN, true);
        assertEquals("running", backend.headers.get(1).get(HelioWebRouter.GB_PROCESS_HEADER));
        assertFalse(backend.headers.get(1).containsKey("X-HELIO-GB-PROCESS"));
    }
}
