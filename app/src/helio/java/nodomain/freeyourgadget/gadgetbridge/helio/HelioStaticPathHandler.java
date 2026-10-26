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

import android.content.res.AssetManager;
import android.webkit.WebResourceResponse;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.webkit.WebViewAssetLoader;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * Serves the Helio web interface from the APK assets ({@code helio/static/}), for
 * {@link WebViewAssetLoader}. Only the listed files exist: the service worker (sw.js) is left out
 * on purpose, since its requests would bypass the WebView interception and its cache would keep
 * an old interface after an update. Every file gets the same Content-Security-Policy that
 * Helio's server sends.
 */
public class HelioStaticPathHandler implements WebViewAssetLoader.PathHandler {
    private static final Logger LOG = LoggerFactory.getLogger(HelioStaticPathHandler.class);

    /** Same policy as helio_app.Handler._send. */
    public static final String CSP = "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            + "script-src 'self'; connect-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'";

    static final String SHIM = "helio-android.js";
    static final String APP_SCRIPT_TAG = "<script src=\"app.js\"></script>";
    static final String SHIM_SCRIPT_TAG = "<script src=\"" + SHIM + "\"></script>";

    /** URL path (without the leading slash) to asset path and MIME type. */
    static final Map<String, String[]> FILES = new HashMap<>();

    static {
        FILES.put("", new String[]{"helio/static/index.html", "text/html"});
        FILES.put("index.html", new String[]{"helio/static/index.html", "text/html"});
        FILES.put("i18n.js", new String[]{"helio/static/i18n.js", "text/javascript"});
        FILES.put("i18n/it.json", new String[]{"helio/static/i18n/it.json", "application/json"});
        FILES.put("i18n/en.json", new String[]{"helio/static/i18n/en.json", "application/json"});
        FILES.put("app.js", new String[]{"helio/static/app.js", "text/javascript"});
        FILES.put("style.css", new String[]{"helio/static/style.css", "text/css"});
        FILES.put("manifest.json", new String[]{"helio/static/manifest.json", "application/manifest+json"});
        FILES.put("icon.svg", new String[]{"helio/static/icon.svg", "image/svg+xml"});
        FILES.put(SHIM, new String[]{"helio/android/" + SHIM, "text/javascript"});
    }

    public interface AssetSource {
        @NonNull
        InputStream open(@NonNull String path) throws IOException;
    }

    private final AssetSource assets;

    public HelioStaticPathHandler(@NonNull final AssetSource assets) {
        this.assets = assets;
    }

    public HelioStaticPathHandler(@NonNull final AssetManager assetManager) {
        this(assetManager::open);
    }

    @Nullable
    @Override
    public WebResourceResponse handle(@NonNull final String path) {
        final String[] file = FILES.get(path);
        if (file == null) {
            return null;
        }
        final byte[] data;
        try (InputStream in = assets.open(file[0])) {
            data = readAll(in);
        } catch (final IOException e) {
            LOG.error("Missing Helio asset {}", file[0], e);
            return null;
        }
        final byte[] body = "text/html".equals(file[1]) ? injectShim(data) : data;

        final Map<String, String> headers = new HashMap<>();
        headers.put("Content-Security-Policy", CSP);
        headers.put("Cache-Control", "no-cache");
        headers.put("X-Content-Type-Options", "nosniff");
        headers.put("Referrer-Policy", "no-referrer");
        return new WebResourceResponse(file[1], "utf-8", 200, "OK", headers, new ByteArrayInputStream(body));
    }

    /**
     * Loads the bridge script right before app.js, so that its fetch wrapper is in place first.
     */
    @NonNull
    static byte[] injectShim(@NonNull final byte[] html) {
        final String text = new String(html, StandardCharsets.UTF_8);
        if (!text.contains(APP_SCRIPT_TAG)) {
            throw new IllegalStateException("index.html does not load app.js as expected");
        }
        return text.replace(APP_SCRIPT_TAG, SHIM_SCRIPT_TAG + APP_SCRIPT_TAG).getBytes(StandardCharsets.UTF_8);
    }

    @NonNull
    private static byte[] readAll(@NonNull final InputStream in) throws IOException {
        final ByteArrayOutputStream out = new ByteArrayOutputStream();
        final byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
        }
        return out.toByteArray();
    }
}
