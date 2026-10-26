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

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.ActivityManager;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.view.Menu;
import android.view.MenuItem;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.window.OnBackInvokedCallback;
import android.window.OnBackInvokedDispatcher;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.webkit.ServiceWorkerClientCompat;
import androidx.webkit.ServiceWorkerControllerCompat;
import androidx.webkit.ServiceWorkerWebSettingsCompat;
import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewAssetLoader;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;

import com.chaquo.python.PyException;

import org.json.JSONObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.text.SimpleDateFormat;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import nodomain.freeyourgadget.gadgetbridge.BuildConfig;
import nodomain.freeyourgadget.gadgetbridge.R;

/**
 * The Helio app: its web interface in a WebView that cannot reach the network, backed by the
 * Helio Python code in the same (:helio) process.
 * <p>
 * Leaving Helio (Home, another app, screen off) keeps the process, idle: the WebView and its
 * timers are paused. Back from the main screen closes Helio and ends the process. If Android
 * ends the process in the background, the section, day and scroll position come back from the
 * saved instance state.
 */
public class HelioActivity extends Activity {
    private static final Logger LOG = LoggerFactory.getLogger(HelioActivity.class);

    private static final int REQUEST_SAVE_CSV = 1;
    private static final int REQUEST_EXPORT = 2;
    private static final int REQUEST_IMPORT = 3;
    private static final int REQUEST_CATALOG = 4;
    private static final int REQUEST_SCAN = 5;
    private static final int MENU_EXPORT = 1;
    private static final int MENU_IMPORT = 2;
    /** Same limit as helio_transfer.MAX_TOTAL. */
    private static final int MAX_ARCHIVE_BYTES = 64 * 1024 * 1024;
    private static final String STATE_VIEW = "helio_view";
    private static final String STATE_CSV_PATH = "helio_csv_path";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private WebView webView;
    private HelioPythonBackend backend;
    private HelioWebRouter router;
    private String pendingCsvPath;
    @Nullable
    private Object backCallback;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(final Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        if (!webViewSupported()) {
            Toast.makeText(this, R.string.helio_webview_too_old, Toast.LENGTH_LONG).show();
            finish();
            return;
        }

        // never inspectable from a computer, unless built with -PhelioWebViewDebug=true
        WebView.setWebContentsDebuggingEnabled(BuildConfig.HELIO_WEBVIEW_DEBUG);
        CookieManager.getInstance().setAcceptCookie(false);

        final WebViewAssetLoader assetLoader = new WebViewAssetLoader.Builder()
                .setDomain(HelioWebRouter.HOST)
                .addPathHandler("/", new HelioStaticPathHandler(getAssets()))
                .build();
        backend = new HelioPythonBackend(this);
        router = new HelioWebRouter(backend, assetLoader, this::runGbAction, this::isGadgetbridgeRunning);

        blockServiceWorkers();

        webView = new WebView(this);
        final WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setBlockNetworkLoads(true);
        settings.setGeolocationEnabled(false);
        settings.setSupportMultipleWindows(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        if (WebViewFeature.isFeatureSupported(WebViewFeature.SAFE_BROWSING_ENABLE)) {
            // Safe Browsing would send the visited URLs to Google
            WebSettingsCompat.setSafeBrowsingEnabled(settings, false);
        }

        webView.setWebViewClient(new WebViewClient() {
            @Nullable
            @Override
            public WebResourceResponse shouldInterceptRequest(final WebView view, final WebResourceRequest request) {
                return router.intercept(request.getUrl(), request.getMethod(), request.getRequestHeaders());
            }

            @Override
            public boolean shouldOverrideUrlLoading(final WebView view, final WebResourceRequest request) {
                return handleNavigation(request.getUrl(), request.hasGesture());
            }
        });

        installBridge(webView);

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) ->
                saveDownload(Uri.parse(url), contentDisposition, mimeType));

        setContentView(webView);
        String savedView = null;
        if (savedInstanceState != null) {
            savedView = savedInstanceState.getString(STATE_VIEW);
            final String csvPath = savedInstanceState.getString(STATE_CSV_PATH);
            if (csvPath != null && csvPath.startsWith("/api/")) {
                pendingCsvPath = csvPath;
            }
        }
        webView.loadUrl(HelioWebRouter.startUrl(savedView));
        registerBack();
    }

    @Nullable
    HelioWebRouter router() {
        return router;
    }

    boolean webViewSupported() {
        return WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER);
    }

    /** The page's POST requests (helio-android.js) arrive here, only from the app origin. */
    void installBridge(@NonNull final WebView webView) {
        WebViewCompat.addWebMessageListener(webView, "HelioBridge",
                Collections.singleton(HelioWebRouter.ORIGIN),
                (view, message, sourceOrigin, isMainFrame, replyProxy) -> {
                    final String data = message.getData();
                    executor.execute(() -> {
                        final String reply = router.handleBridgeMessage(data, sourceOrigin, isMainFrame);
                        if (reply != null) {
                            mainHandler.post(() -> replyProxy.postMessage(reply));
                        }
                    });
                });
    }

    @Override
    protected void onSaveInstanceState(@NonNull final Bundle outState) {
        super.onSaveInstanceState(outState);
        if (router != null && router.savedView() != null) {
            outState.putString(STATE_VIEW, router.savedView());
        }
        if (pendingCsvPath != null) {
            outState.putString(STATE_CSV_PATH, pendingCsvPath);
        }
    }

    /**
     * Back goes to the page first (open sheet, then the main section); only from the main
     * section it closes Helio. API 33+ needs a callback: onBackPressed is not called anymore with
     * predictive back.
     */
    private void registerBack() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            final OnBackInvokedCallback callback = this::onHelioBack;
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, callback);
            backCallback = callback;
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        onHelioBack();
    }

    void onHelioBack() {
        if (webView == null) {
            finishAndRemoveTask();
            return;
        }
        askPageBack(result -> {
            if (!"true".equals(result) && !isFinishing()) {
                finishAndRemoveTask();
            }
        });
    }

    /** Back in the page: "true" when it closed a sheet or went back to Today. */
    void askPageBack(@NonNull final ValueCallback<String> result) {
        webView.evaluateJavascript("typeof window.helioBack === \"function\" ? window.helioBack() : false", result);
    }

    /**
     * Band menu action, approved by the router: Gadgetbridge runs it in its own process.
     */
    private void runGbAction(@NonNull final String action) {
        if (HelioGbActions.CATALOG_IMPORT.equals(action)) {
            mainHandler.post(() -> startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT)
                    .addCategory(Intent.CATEGORY_OPENABLE)
                    .setType("*/*")
                    .putExtra(Intent.EXTRA_MIME_TYPES, HelioArchive.CATALOG_MIME_TYPES), REQUEST_CATALOG));
            return;
        }
        if (HelioGbActions.SCAN.equals(action)) {
            mainHandler.post(() -> startActivityForResult(new Intent(this, HelioScanActivity.class), REQUEST_SCAN));
            return;
        }
        mainHandler.post(() -> {
            // a Gadgetbridge screen goes on top of Helio, in the same task: Back returns here
            startActivity(new Intent(this, HelioGbActionActivity.class)
                    .putExtra(HelioGbActionActivity.EXTRA_ACTION, action));
        });
    }

    private boolean isGadgetbridgeRunning() {
        final ActivityManager am = (ActivityManager) getSystemService(ACTIVITY_SERVICE);
        final List<ActivityManager.RunningAppProcessInfo> processes = am != null ? am.getRunningAppProcesses() : null;
        if (processes != null) {
            for (final ActivityManager.RunningAppProcessInfo p : processes) {
                if (getPackageName().equals(p.processName)) {
                    return true;
                }
            }
        }
        return false;
    }

    void blockServiceWorkers() {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_BASIC_USAGE)) {
            return;
        }
        final ServiceWorkerControllerCompat controller = ServiceWorkerControllerCompat.getInstance();
        controller.setServiceWorkerClient(new ServiceWorkerClientCompat() {
            @Nullable
            @Override
            public WebResourceResponse shouldInterceptRequest(@NonNull final WebResourceRequest request) {
                return HelioWebRouter.emptyResponse(403, "Forbidden");
            }
        });
        final ServiceWorkerWebSettingsCompat swSettings = controller.getServiceWorkerWebSettings();
        if (WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_BLOCK_NETWORK_LOADS)) {
            swSettings.setBlockNetworkLoads(true);
        }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_FILE_ACCESS)) {
            swSettings.setAllowFileAccess(false);
        }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_CONTENT_ACCESS)) {
            swSettings.setAllowContentAccess(false);
        }
    }

    private boolean handleNavigation(@NonNull final Uri uri, final boolean hasGesture) {
        switch (HelioWebRouter.navigationFor(uri, hasGesture)) {
            case ALLOW:
                return false;
            case OPEN_EXTERNALLY:
                final Intent intent = new Intent(Intent.ACTION_VIEW, uri)
                        .addCategory(Intent.CATEGORY_BROWSABLE);
                try {
                    startActivity(intent);
                } catch (final ActivityNotFoundException e) {
                    Toast.makeText(this, R.string.helio_no_browser, Toast.LENGTH_SHORT).show();
                }
                return true;
            default:
                LOG.warn("Blocked navigation to {}", uri.getScheme());
                return true;
        }
    }

    /**
     * The CSV export links are downloads: ask where to save the file, then write Helio's answer.
     */
    private void saveDownload(@NonNull final Uri uri, @Nullable final String contentDisposition,
                              @Nullable final String mimeType) {
        if (!HelioWebRouter.isAppUrl(uri) || uri.getPath() == null || !uri.getPath().startsWith("/api/")) {
            return;
        }
        final String query = uri.getEncodedQuery();
        pendingCsvPath = query != null ? uri.getPath() + "?" + query : uri.getPath();
        final Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType(mimeType != null ? mimeType : "text/csv")
                .putExtra(Intent.EXTRA_TITLE, URLUtil.guessFileName(uri.toString(), contentDisposition, mimeType));
        startActivityForResult(intent, REQUEST_SAVE_CSV);
    }

    @Override
    public boolean onCreateOptionsMenu(final Menu menu) {
        menu.add(Menu.NONE, MENU_EXPORT, 0, R.string.helio_export);
        menu.add(Menu.NONE, MENU_IMPORT, 1, R.string.helio_import);
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(@NonNull final MenuItem item) {
        if (item.getItemId() == MENU_EXPORT) {
            final String name = "helio-state-" + new SimpleDateFormat("yyyyMMdd-HHmm", Locale.ROOT).format(new Date()) + ".tgz";
            startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT)
                    .addCategory(Intent.CATEGORY_OPENABLE)
                    .setType(HelioArchive.EXPORT_MIME_TYPE)
                    .putExtra(Intent.EXTRA_TITLE, name), REQUEST_EXPORT);
            return true;
        }
        if (item.getItemId() == MENU_IMPORT) {
            startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT)
                    .addCategory(Intent.CATEGORY_OPENABLE)
                    .setType("*/*")
                    .putExtra(Intent.EXTRA_MIME_TYPES, HelioArchive.IMPORT_MIME_TYPES), REQUEST_IMPORT);
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    @Override
    protected void onActivityResult(final int requestCode, final int resultCode, @Nullable final Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        final Uri uri = resultCode == RESULT_OK && data != null ? data.getData() : null;
        if (requestCode == REQUEST_SAVE_CSV) {
            final String path = pendingCsvPath;
            pendingCsvPath = null;
            if (uri != null && path != null) {
                executor.execute(() -> writeCsv(path, uri));
            }
        } else if (requestCode == REQUEST_EXPORT && uri != null) {
            executor.execute(() -> exportTo(uri));
        } else if (requestCode == REQUEST_IMPORT && uri != null) {
            executor.execute(() -> readForImport(uri));
        } else if (requestCode == REQUEST_CATALOG && uri != null) {
            executor.execute(() -> importCatalog(uri));
        } else if (requestCode == REQUEST_SCAN) {
            sendToPage("window.helioScanned && window.helioScanned(" + scanResult(resultCode, data) + ")");
        }
    }

    private void exportTo(@NonNull final Uri target) {
        boolean ok;
        try (OutputStream out = getContentResolver().openOutputStream(target, "wt")) {
            if (out == null) {
                throw new IOException("no output stream");
            }
            out.write(backend.exportState());
            ok = true;
        } catch (final Exception e) {
            LOG.error("Helio export failed", e);
            ok = false;
        }
        final boolean saved = ok;
        mainHandler.post(() -> Toast.makeText(this,
                saved ? R.string.helio_exported : R.string.helio_export_failed, Toast.LENGTH_LONG).show());
    }

    private void readForImport(@NonNull final Uri source) {
        final byte[] archive;
        try (InputStream in = getContentResolver().openInputStream(source)) {
            if (in == null) {
                throw new IOException("no input stream");
            }
            final ByteArrayOutputStream out = new ByteArrayOutputStream();
            final byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > MAX_ARCHIVE_BYTES) {
                    throw new IOException("archive too large");
                }
            }
            archive = out.toByteArray();
        } catch (final IOException e) {
            LOG.error("Could not read the Helio archive", e);
            mainHandler.post(() -> showImportError(getString(R.string.helio_import_unreadable)));
            return;
        }
        mainHandler.post(() -> new AlertDialog.Builder(this)
                .setTitle(R.string.helio_import)
                .setMessage(R.string.helio_import_confirm)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(R.string.helio_import_do, (d, w) -> executor.execute(() -> importArchive(archive)))
                .show());
    }

    private void importArchive(@NonNull final byte[] archive) {
        try {
            final JSONObject summary = new JSONObject(backend.importState(archive));
            final String message = getString(R.string.helio_imported, summary.getInt("files"));
            mainHandler.post(() -> new AlertDialog.Builder(this)
                    .setTitle(R.string.helio_import)
                    .setMessage(message)
                    .setCancelable(false)
                    // the profile is read when Python starts: Helio closes and is opened again
                    .setPositiveButton(android.R.string.ok, (d, w) -> finishAndRemoveTask())
                    .show());
        } catch (final PyException e) {
            LOG.warn("Helio archive rejected", e);
            mainHandler.post(() -> showImportError(pythonMessage(e)));
        } catch (final Exception e) {
            LOG.error("Helio import failed", e);
            mainHandler.post(() -> showImportError(String.valueOf(e.getMessage())));
        }
    }

    /**
     * Food catalog package chosen by the user: copied into a temporary file, checked and installed by Python
     * (only the catalog folder changes, never the user's data); the result goes back to the page.
     */
    private void importCatalog(@NonNull final Uri source) {
        final File tmp = new File(getCacheDir(), "catalog-import.tar");
        JSONObject result;
        try {
            try (InputStream in = getContentResolver().openInputStream(source);
                 OutputStream out = new FileOutputStream(tmp)) {
                if (in == null) {
                    throw new IOException("no input stream");
                }
                final byte[] buf = new byte[65536];
                long total = 0;
                int n;
                while ((n = in.read(buf)) > 0) {
                    total += n;
                    if (total > HelioArchive.MAX_CATALOG_BYTES) {
                        throw new IOException("package too large");
                    }
                    out.write(buf, 0, n);
                }
            }
            result = new JSONObject(backend.importCatalog(tmp)).put("ok", true);
        } catch (final PyException e) {
            LOG.warn("Food catalog rejected", e);
            result = catalogFailure(pythonMessage(e));
        } catch (final Exception e) {
            LOG.error("Food catalog import failed", e);
            result = catalogFailure(getString(R.string.helio_catalog_unreadable));
        } finally {
            //noinspection ResultOfMethodCallIgnored
            tmp.delete();
        }
        sendToPage("window.helioCatalogImported && window.helioCatalogImported(" + result + ")");
    }

    /** {"code": "800..."} or {"error": "permission" | "camera" | "cancel"} for the page. */
    @NonNull
    static JSONObject scanResult(final int resultCode, @Nullable final Intent data) {
        final JSONObject o = new JSONObject();
        final String code = data != null ? data.getStringExtra(HelioScanActivity.EXTRA_CODE) : null;
        final String error = data != null ? data.getStringExtra(HelioScanActivity.EXTRA_ERROR) : null;
        try {
            if (resultCode == RESULT_OK && code != null && code.matches("\\d{6,14}")) {
                o.put("code", code);
            } else {
                o.put("error", error != null ? error : "cancel");
            }
        } catch (final org.json.JSONException ignored) {
            // cannot happen with string values
        }
        return o;
    }

    private void sendToPage(@NonNull final String js) {
        mainHandler.post(() -> {
            if (webView != null) {
                webView.evaluateJavascript(js, null);
            }
        });
    }

    @NonNull
    static JSONObject catalogFailure(@NonNull final String message) {
        final JSONObject o = new JSONObject();
        try {
            o.put("ok", false).put("message", message);
        } catch (final org.json.JSONException ignored) {
            // cannot happen with a string value
        }
        return o;
    }

    /** "helio_transfer.TransferError: archivio non leggibile" -> "archivio non leggibile" */
    @NonNull
    static String pythonMessage(@NonNull final PyException e) {
        final String message = String.valueOf(e.getMessage());
        final int colon = message.indexOf(": ");
        return colon >= 0 ? message.substring(colon + 2) : message;
    }

    private void showImportError(@NonNull final String detail) {
        new AlertDialog.Builder(this)
                .setTitle(R.string.helio_import_failed)
                .setMessage(detail)
                .setPositiveButton(android.R.string.ok, null)
                .show();
    }

    private void writeCsv(@NonNull final String path, @NonNull final Uri target) {
        final HelioResponse response = router.runBackend("GET", path, new HashMap<>(), new byte[0]);
        boolean ok = response.status == 200;
        if (ok) {
            try (OutputStream out = getContentResolver().openOutputStream(target, "wt")) {
                if (out == null) {
                    throw new IOException("no output stream");
                }
                out.write(response.body);
            } catch (final Exception e) {
                LOG.error("Failed to save CSV", e);
                ok = false;
            }
        }
        final boolean saved = ok;
        mainHandler.post(() -> Toast.makeText(this,
                saved ? R.string.helio_saved : R.string.helio_save_failed, Toast.LENGTH_SHORT).show());
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.resumeTimers();
            webView.onResume();
        }
    }

    @Override
    protected void onPause() {
        // in the background nothing runs: no JavaScript timers (band polling, refresh), no layout
        if (webView != null) {
            webView.onPause();
            webView.pauseTimers();
        }
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && backCallback != null) {
            getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback((OnBackInvokedCallback) backCallback);
        }
        if (webView != null) {
            webView.destroy();
        }
        executor.shutdown();
        super.onDestroy();
        if (isFinishing()) {
            // the :helio process (Python, WebView) ends with the activity
            endProcess();
        }
    }

    /**
     * Only from onDestroy while finishing: Back from Today, the end of an import, or Helio removed
     * from the recent apps. Never when Helio just goes to the background (onPause, onStop).
     */
    void endProcess() {
        Process.killProcess(Process.myPid());
    }
}
