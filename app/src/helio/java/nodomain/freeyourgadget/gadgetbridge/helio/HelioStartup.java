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

import android.os.Process;
import android.os.SystemClock;

import androidx.annotation.NonNull;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Startup timeline of the :helio process (Profile > About > Startup times): Java's steps and, for each API path, the
 * first answer as the WebView saw it (waiting for Python included). Wall clock milliseconds, the clock of the page and
 * of Python too, so the page can put the three sides on one line. Only in memory: nothing is written or sent anywhere,
 * no data, paths without their query.
 */
public final class HelioStartup {
    static final int MAX_MARKS = 32;
    static final int MAX_API = 64;
    private static final Pattern API_PATH = Pattern.compile("^/api/[a-z0-9_./-]{1,58}$");
    /** Asked to show the timeline: not part of the start. */
    static final String PATH = "/api/android/startup";
    private static final String PYTHON_PATH = "/api/startup";

    private static final List<long[]> marks = new ArrayList<>();
    private static final List<String> markKeys = new ArrayList<>();
    private static final Map<String, long[]> api = new LinkedHashMap<>();

    private HelioStartup() {
    }

    public static long now() {
        return System.currentTimeMillis();
    }

    /** When Android started this process, on the wall clock. */
    public static long processStart() {
        return now() - (SystemClock.elapsedRealtime() - Process.getStartElapsedRealtime());
    }

    /** The first time of each step only (a new Activity in the same process starts nothing again). */
    public static synchronized void mark(@NonNull final String key, final long start, final long end) {
        if (marks.size() < MAX_MARKS && !markKeys.contains(key)) {
            markKeys.add(key);
            marks.add(new long[]{start, end});
        }
    }

    /** The first answer of each path only: later ones say nothing about the start. */
    public static synchronized void api(@NonNull final String pathAndQuery, final long start, final long end) {
        final int q = pathAndQuery.indexOf('?');
        final String path = q >= 0 ? pathAndQuery.substring(0, q) : pathAndQuery;
        if (!API_PATH.matcher(path).matches() || PATH.equals(path) || PYTHON_PATH.equals(path)
                || api.containsKey(path) || api.size() >= MAX_API) {
            return;
        }
        api.put(path, new long[]{start, end});
    }

    @NonNull
    public static synchronized String json() {
        try {
            final JSONArray m = new JSONArray();
            for (int i = 0; i < marks.size(); i++) {
                m.put(new JSONObject().put("key", markKeys.get(i))
                        .put("start", marks.get(i)[0]).put("end", marks.get(i)[1]));
            }
            final JSONArray a = new JSONArray();
            for (final Map.Entry<String, long[]> e : api.entrySet()) {
                a.put(new JSONObject().put("path", e.getKey())
                        .put("start", e.getValue()[0]).put("end", e.getValue()[1]));
            }
            return new JSONObject().put("marks", m).put("api", a).put("now", now()).toString();
        } catch (final JSONException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Tests only. */
    static synchronized void clear() {
        marks.clear();
        markKeys.clear();
        api.clear();
    }
}
