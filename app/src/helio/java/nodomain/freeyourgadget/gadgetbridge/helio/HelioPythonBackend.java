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

import android.content.Context;

import androidx.annotation.NonNull;

import com.chaquo.python.Kwarg;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

import nodomain.freeyourgadget.gadgetbridge.GBDatabase;

/**
 * Runs Helio in Python (Chaquopy), in the :helio process. Python is started on the first request.
 */
public class HelioPythonBackend implements HelioBackend {
    private final Context context;
    private PyObject bridge;

    public HelioPythonBackend(@NonNull final Context context) {
        this.context = context.getApplicationContext();
    }

    /** Private folder with Helio's own data (labels, plan, weights, diary...). */
    @NonNull
    public static File stateDir(@NonNull final Context context) {
        return new File(context.getFilesDir(), "helio-state");
    }

    /**
     * Food catalog folder (community databases, read only): next to the data folder, never inside it, so that
     * the export and import of the user's data never see it.
     */
    @NonNull
    public static File catalogDir(@NonNull final Context context) {
        return new File(context.getFilesDir(), "helio-catalog");
    }

    /** The catalog package shipped in the APK (USDA + Ciqual), copied out of the assets for Python. */
    @NonNull
    static File bundledCatalog(@NonNull final Context context) {
        return new File(context.getFilesDir(), "helio-catalog-bundled.tar");
    }

    static final String BUNDLED_CATALOG_ASSET = "helio/catalog/generic.tar";

    /**
     * Copies the shipped catalog package when missing or older than the installed APK. Python installs it (after
     * checking it) the first time the catalog is used.
     */
    static void copyBundledCatalog(@NonNull final Context context) {
        final File dest = bundledCatalog(context);
        long apkTime = 0;
        try {
            apkTime = context.getPackageManager().getPackageInfo(context.getPackageName(), 0).lastUpdateTime;
        } catch (final Exception ignored) {
            // unknown: copy only if missing
        }
        if (dest.isFile() && dest.lastModified() >= apkTime) {
            return;
        }
        final File tmp = new File(dest.getPath() + ".tmp");
        try (InputStream in = context.getAssets().open(BUNDLED_CATALOG_ASSET);
             OutputStream out = new FileOutputStream(tmp)) {
            final byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
        } catch (final IOException e) {
            //noinspection ResultOfMethodCallIgnored
            tmp.delete();
            return;
        }
        //noinspection ResultOfMethodCallIgnored
        tmp.renameTo(dest);
    }

    /** Helio profile (measures and goals), as imported from the server. */
    @NonNull
    public static File profileFile(@NonNull final Context context) {
        return new File(context.getFilesDir(), "helio-config.json");
    }

    @NonNull
    private synchronized PyObject bridge() {
        if (bridge == null) {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(context));
            }
            copyBundledCatalog(context);
            final PyObject module = Python.getInstance().getModule("helio_android");
            module.callAttr("configure",
                    context.getDatabasePath(GBDatabase.DATABASE_NAME).getAbsolutePath(),
                    stateDir(context).getAbsolutePath(),
                    null,
                    TimeZone.getDefault().getID(),
                    new Kwarg("profile_path", profileFile(context).getAbsolutePath()),
                    new Kwarg("band_state_path", HelioBandState.file(context).getAbsolutePath()),
                    new Kwarg("catalog_dir", catalogDir(context).getAbsolutePath()),
                    new Kwarg("catalog_bundled", bundledCatalog(context).getAbsolutePath()));
            bridge = module;
        }
        return bridge;
    }

    /**
     * The Helio data and profile as a .tgz, in the same format as helio_transfer.py on the server.
     */
    @NonNull
    public byte[] exportState() {
        return bridge().callAttr("export_state", stateDir(context).getAbsolutePath(),
                profileFile(context).getAbsolutePath()).toJava(byte[].class);
    }

    /**
     * Replaces the Helio data and profile with the archive content, after checking it; the
     * previous data is kept as a backup next to it. Returns a JSON summary.
     *
     * @throws com.chaquo.python.PyException if the archive is not valid (nothing is changed)
     */
    @NonNull
    public String importState(@NonNull final byte[] archive) {
        // the reason of a rejected archive comes back in the system language, like the native dialogs
        return bridge().callAttr("import_state", archive, stateDir(context).getAbsolutePath(),
                profileFile(context).getAbsolutePath(), java.util.Locale.getDefault().getLanguage()).toString();
    }

    /**
     * Installs a food catalog package (a file chosen by the user, copied into a temporary file). Returns a JSON
     * summary; the user's data is never touched.
     *
     * @throws com.chaquo.python.PyException if the package is not valid (the installed catalog stays)
     */
    @NonNull
    public String importCatalog(@NonNull final File packageFile) {
        return bridge().callAttr("catalog_import", packageFile.getAbsolutePath(),
                java.util.Locale.getDefault().getLanguage()).toString();
    }

    @NonNull
    @Override
    public HelioResponse handle(@NonNull final String method, @NonNull final String pathAndQuery,
                                @NonNull final Map<String, String> headers, @NonNull final byte[] body) throws Exception {
        final List<PyObject> result = bridge().callAttr("handle", method, pathAndQuery,
                new JSONObject(headers).toString(), body).asList();

        final List<String[]> responseHeaders = new ArrayList<>();
        final JSONArray headersJson = new JSONArray(result.get(2).toString());
        for (int i = 0; i < headersJson.length(); i++) {
            final JSONArray header = headersJson.getJSONArray(i);
            responseHeaders.add(new String[]{header.getString(0), header.getString(1)});
        }
        return new HelioResponse(result.get(0).toInt(), result.get(1).toString(), responseHeaders,
                result.get(3).toJava(byte[].class));
    }
}
