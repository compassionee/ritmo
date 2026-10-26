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

import android.net.Uri;
import android.webkit.WebResourceResponse;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.webkit.WebViewAssetLoader;

import org.json.JSONException;
import org.json.JSONObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Decides what the Helio WebView may load, and connects it to the Python code:
 * <ul>
 *     <li>only the app origin (https://appassets.androidplatform.net) is ever loaded, everything
 *     else gets an empty 403;</li>
 *     <li>static files come from the APK assets through {@link WebViewAssetLoader};</li>
 *     <li>GET /api/* requests are intercepted and run by Helio's Handler;</li>
 *     <li>POST /api/* requests arrive as bridge messages (the WebView does not expose the body of
 *     intercepted requests) and are run by the same Handler.</li>
 *     <li>/api/android/* exists only in the app: the band state (read only) and the actions of the
 *     band menu. An action runs only when it comes as a bridge message from the app origin and
 *     the Python side approves it (X-Helio, JSON, known action).</li>
 * </ul>
 */
public class HelioWebRouter {
    private static final Logger LOG = LoggerFactory.getLogger(HelioWebRouter.class);

    public static final String HOST = "appassets.androidplatform.net";
    public static final String ORIGIN = "https://" + HOST;
    public static final String START_URL = ORIGIN + "/";

    /** Largest bridge message: the body limit of the Python side (64 KiB) plus JSON escaping. */
    public static final int MAX_MESSAGE_CHARS = 256 * 1024;

    static final String ACTION_PATH = "/api/android/action";
    /** Set by the Python side (helio_android) on an approved action. */
    static final String ACTION_HEADER = "X-Helio-Action";
    /** Set here, never taken from the page: is the main Gadgetbridge process running? */
    static final String GB_PROCESS_HEADER = "X-Helio-Gb-Process";

    private static final Pattern MESSAGE_ID = Pattern.compile("^\\{\"id\":(\\d{1,9})[,}]");
    /** Bridge message with the page's view (section, day, scroll), kept for a process restart. */
    static final String VIEW_MESSAGE = "view";
    private static final Pattern VIEW_TAB = Pattern.compile("^[a-z]{1,16}$");
    private static final Pattern VIEW_DATE = Pattern.compile("^\\d{4}-\\d{2}-\\d{2}$");
    static final int MAX_VIEW_SCROLL = 1_000_000;

    public enum Navigation {
        /** An app page: load it in the WebView. */
        ALLOW,
        /** An https link tapped by the user: open it in the browser, never in the WebView. */
        OPEN_EXTERNALLY,
        /** Anything else. */
        BLOCK,
    }

    private final HelioBackend backend;
    private final WebViewAssetLoader assetLoader;
    private final HelioGbActions actions;
    private final HelioGbActions.ProcessProbe processProbe;
    /** Last view sent by the page, already checked (JSON), or null. */
    private volatile String view;

    public HelioWebRouter(@NonNull final HelioBackend backend, @NonNull final WebViewAssetLoader assetLoader,
                          @NonNull final HelioGbActions actions, @NonNull final HelioGbActions.ProcessProbe processProbe) {
        this.backend = backend;
        this.assetLoader = assetLoader;
        this.actions = actions;
        this.processProbe = processProbe;
    }

    public static boolean isAppUrl(@Nullable final Uri uri) {
        return uri != null
                && "https".equals(uri.getScheme())
                && HOST.equals(uri.getHost())
                && uri.getPort() == -1
                && uri.getUserInfo() == null;
    }

    @NonNull
    public static Navigation navigationFor(@NonNull final Uri uri, final boolean hasGesture) {
        if (isAppUrl(uri)) {
            return Navigation.ALLOW;
        }
        if (hasGesture && "https".equals(uri.getScheme()) && uri.getHost() != null && !uri.getHost().isEmpty()) {
            return Navigation.OPEN_EXTERNALLY;
        }
        return Navigation.BLOCK;
    }

    /**
     * Answers every request of the WebView: it never goes to the network.
     */
    @NonNull
    public WebResourceResponse intercept(@NonNull final Uri url, @NonNull final String method,
                                         @Nullable final Map<String, String> headers) {
        if (!isAppUrl(url)) {
            LOG.warn("Blocked request to {}", url.getScheme() + "://" + url.getHost());
            return emptyResponse(403, "Forbidden");
        }

        final String path = url.getPath() != null ? url.getPath() : "/";
        if (path.startsWith("/api/")) {
            if (!"GET".equals(method)) {
                // POST requests go through the bridge, which carries the body
                return toWebResponse(errorResponse(405, "Method Not Allowed", "err.invalid_request"));
            }
            final String query = url.getEncodedQuery();
            return toWebResponse(runBackend("GET", query != null ? path + "?" + query : path,
                    trustedHeaders(headers), new byte[0]));
        }

        final WebResourceResponse asset = assetLoader.shouldInterceptRequest(url);
        return asset != null ? asset : emptyResponse(404, "Not Found");
    }

    /**
     * Handles a message of the JavaScript bridge, and returns the reply to post back, or null
     * when the message must be ignored.
     */
    @Nullable
    public String handleBridgeMessage(@Nullable final String data, @Nullable final Uri sourceOrigin,
                                      final boolean isMainFrame) {
        if (!isMainFrame || sourceOrigin == null || !ORIGIN.equals(sourceOrigin.toString()) || data == null) {
            LOG.warn("Ignored bridge message from {}", sourceOrigin);
            return null;
        }

        if (data.length() > MAX_MESSAGE_CHARS) {
            // same answer as Handler for a body over the limit, without parsing it
            final Matcher m = MESSAGE_ID.matcher(data);
            return m.find() ? reply(Integer.parseInt(m.group(1)),
                    errorResponse(400, "Bad Request", "err.invalid_request")) : null;
        }

        final JSONObject msg;
        try {
            msg = new JSONObject(data);
        } catch (final JSONException e) {
            LOG.warn("Invalid bridge message", e);
            return null;
        }
        if (VIEW_MESSAGE.equals(msg.opt("type"))) {
            // only kept in memory, for onSaveInstanceState: no answer, nothing reaches Python
            final String checked = cleanView(msg.optJSONObject("view"));
            if (checked != null) {
                view = checked;
            }
            return null;
        }

        final int id;
        final String method;
        final String path;
        final Map<String, String> headers = new HashMap<>();
        final String body;
        try {
            id = msg.getInt("id");
            method = msg.getString("method");
            path = msg.getString("path");
            body = msg.optString("body", "");
            final JSONObject h = msg.optJSONObject("headers");
            if (h != null) {
                for (final Iterator<String> it = h.keys(); it.hasNext(); ) {
                    final String name = it.next();
                    headers.put(name, h.optString(name, ""));
                }
            }
        } catch (final JSONException e) {
            LOG.warn("Invalid bridge message", e);
            return null;
        }

        if (!"POST".equals(method) || !path.startsWith("/api/")) {
            return reply(id, errorResponse(400, "Bad Request", "err.invalid_request"));
        }
        final HelioResponse response = runBackend("POST", path, trustedHeaders(headers),
                body.getBytes(StandardCharsets.UTF_8));
        runApprovedAction(path, response);
        return reply(id, response);
    }

    /**
     * Runs a band menu action, only when Helio's Python side approved this exact request.
     */
    private void runApprovedAction(@NonNull final String path, @NonNull final HelioResponse response) {
        final String action = response.header(ACTION_HEADER);
        if (action == null) {
            return;
        }
        final String pathOnly = path.contains("?") ? path.substring(0, path.indexOf('?')) : path;
        if (response.status != 200 || !ACTION_PATH.equals(pathOnly) || !HelioGbActions.ALL.contains(action)) {
            LOG.warn("Ignored action {} for {} ({})", action, pathOnly, response.status);
            return;
        }
        actions.run(action);
    }

    /** The page's last view (section, day, scroll) as checked JSON, or null. */
    @Nullable
    public String savedView() {
        return view;
    }

    /**
     * The view, rebuilt only from valid fields: a lowercase section name, a yyyy-MM-dd day and a
     * scroll offset. Null when nothing valid is left. app.js checks the values again.
     */
    @Nullable
    static String cleanView(@Nullable final JSONObject in) {
        if (in == null) {
            return null;
        }
        try {
            final JSONObject out = new JSONObject();
            final Object tab = in.opt("tab");
            if (tab instanceof String && VIEW_TAB.matcher((String) tab).matches()) {
                out.put("tab", tab);
            }
            final Object date = in.opt("date");
            if (date instanceof String && VIEW_DATE.matcher((String) date).matches()) {
                out.put("date", date);
            }
            final Object y = in.opt("y");
            if (y instanceof Integer && (Integer) y >= 0 && (Integer) y <= MAX_VIEW_SCROLL) {
                out.put("y", y);
            }
            return out.length() > 0 ? out.toString() : null;
        } catch (final JSONException e) {
            return null;
        }
    }

    /** Where Helio opens: the start page, with the view to restore in the fragment (read by helio-android.js). */
    @NonNull
    public static String startUrl(@Nullable final String savedView) {
        final String view = savedView != null ? cleanView(parseObject(savedView)) : null;
        return view != null ? START_URL + "#view=" + Uri.encode(view) : START_URL;
    }

    @Nullable
    private static JSONObject parseObject(@NonNull final String json) {
        try {
            return new JSONObject(json);
        } catch (final JSONException e) {
            return null;
        }
    }

    /** The page headers, without the ones that only this router may set. */
    @NonNull
    private Map<String, String> trustedHeaders(@Nullable final Map<String, String> pageHeaders) {
        final Map<String, String> out = new HashMap<>();
        if (pageHeaders != null) {
            for (final Map.Entry<String, String> h : pageHeaders.entrySet()) {
                if (!GB_PROCESS_HEADER.equalsIgnoreCase(h.getKey())) {
                    out.put(h.getKey(), h.getValue());
                }
            }
        }
        out.put(GB_PROCESS_HEADER, processProbe.isGadgetbridgeRunning() ? "running" : "stopped");
        return out;
    }

    @NonNull
    HelioResponse runBackend(@NonNull final String method, @NonNull final String pathAndQuery,
                             @NonNull final Map<String, String> headers, @NonNull final byte[] body) {
        try {
            return backend.handle(method, pathAndQuery, headers, body);
        } catch (final Exception e) {
            LOG.error("Helio failed on {} {}", method, pathAndQuery, e);
            return errorResponse(500, "Internal Server Error", "err.internal");
        }
    }

    @NonNull
    /** Error body like Helio's: {"error": {"$": key}}, translated by the page (i18n/*.json). */
    static HelioResponse errorResponse(final int status, @NonNull final String reason, @NonNull final String key) {
        final String json;
        try {
            json = new JSONObject().put("error", new JSONObject().put("$", key)).toString();
        } catch (final JSONException e) {
            throw new IllegalStateException(e);
        }
        return new HelioResponse(status, reason,
                Collections.singletonList(new String[]{"Content-Type", "application/json"}),
                json.getBytes(StandardCharsets.UTF_8));
    }

    @NonNull
    static String reply(final int id, @NonNull final HelioResponse response) {
        try {
            final JSONObject headers = new JSONObject();
            for (final String[] header : response.headers) {
                if (!"content-length".equals(header[0].toLowerCase(Locale.ROOT))) {
                    headers.put(header[0], header[1]);
                }
            }
            return new JSONObject()
                    .put("id", id)
                    .put("status", response.status)
                    .put("reason", response.reason)
                    .put("headers", headers)
                    .put("body", new String(response.body, StandardCharsets.UTF_8))
                    .toString();
        } catch (final JSONException e) {
            throw new IllegalStateException(e);
        }
    }

    @NonNull
    static WebResourceResponse toWebResponse(@NonNull final HelioResponse response) {
        String mimeType = "application/octet-stream";
        String encoding = null;
        final String contentType = response.header("Content-Type");
        if (contentType != null) {
            final String[] parts = contentType.split(";");
            mimeType = parts[0].trim();
            for (int i = 1; i < parts.length; i++) {
                final String param = parts[i].trim();
                if (param.toLowerCase(Locale.ROOT).startsWith("charset=")) {
                    encoding = param.substring("charset=".length());
                }
            }
        }

        // WebResourceResponse accepts only 1xx, 2xx, 4xx and 5xx with a reason phrase
        int status = response.status;
        String reason = response.reason;
        if (status < 100 || status > 599 || (status >= 300 && status < 400)) {
            status = 500;
            reason = "Internal Server Error";
        }
        if (reason.isEmpty()) {
            reason = status < 400 ? "OK" : "Error";
        }

        final Map<String, String> headers = new HashMap<>();
        for (final String[] header : response.headers) {
            final String name = header[0].toLowerCase(Locale.ROOT);
            if (!name.equals("content-type") && !name.equals("content-length")) {
                headers.put(header[0], header[1]);
            }
        }
        return new WebResourceResponse(mimeType, encoding, status, reason, headers,
                new ByteArrayInputStream(response.body));
    }

    @NonNull
    static WebResourceResponse emptyResponse(final int status, @NonNull final String reason) {
        return new WebResourceResponse("text/plain", "utf-8", status, reason, new HashMap<>(),
                new ByteArrayInputStream(new byte[0]));
    }
}
