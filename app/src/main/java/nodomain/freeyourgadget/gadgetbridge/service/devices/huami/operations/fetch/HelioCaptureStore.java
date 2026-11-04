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
package nodomain.freeyourgadget.gadgetbridge.service.devices.huami.operations.fetch;

import android.content.Context;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import org.json.JSONException;
import org.json.JSONObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The capture mode of the Helio build: a private folder ({@code files/helio-capture/}) with the raw answers of the band
 * to fetch types Gadgetbridge does not handle yet, never interpreted, never written to Gadgetbridge's database or
 * preferences, nor to Ritmo's data. Shared by the main process (which fetches) and the :helio process (Profile, export):
 * only files, read again at every use.
 * <pre>
 *   config.json               {"on": true}                       off when missing
 *   state.json                {"since": {"4e": ms, ...}}         when each type was last answered
 *   log.jsonl                 one line per request: type, outcome, bytes, the band's answer (hex)
 *   YYYYMMDD/HHMMSS-4e.bin    the payload as received (packet counters removed), nothing else
 * </pre>
 */
public final class HelioCaptureStore {
    private static final Logger LOG = LoggerFactory.getLogger(HelioCaptureStore.class);

    public static final String DIR = "helio-capture";
    /**
     * The types asked for. SDNN (0x4e) and the heart rate of every second (0x57) are fetched and saved by the normal
     * sync now (FetchHrvSdnnOperation, FetchHeartRateSecondsOperation): not captured, their ack drops them from the band.
     */
    public static final HuamiFetchDataType[] TYPES = {
            HuamiFetchDataType.REST_HR,
            HuamiFetchDataType.RECOVERY_HEART_RATE,
            HuamiFetchDataType.BODY_BATTERY,
            HuamiFetchDataType.SPO2_OSA_EVENT,
            HuamiFetchDataType.SEC_HR,
    };
    /** Outcomes in the log. */
    public static final String DATA = "data";
    public static final String EMPTY = "empty";
    public static final String REJECTED = "rejected";
    public static final String INVALID = "invalid";
    public static final String NO_ANSWER = "no_answer";
    public static final String SKIPPED = "skipped";
    public static final String FULL = "full";

    /** First capture of a type: this far back; later ones overlap the previous by a day (late records). */
    static final long FIRST_SINCE_MS = 3L * 24 * 60 * 60 * 1000;
    static final long OVERLAP_MS = 24L * 60 * 60 * 1000;
    /** The folder stops growing here: a capture beyond it is logged as "full" and not saved. */
    public static final long MAX_BYTES = 200L * 1024 * 1024;

    private final File dir;

    public HelioCaptureStore(@NonNull final File dir) {
        this.dir = dir;
    }

    @NonNull
    public static HelioCaptureStore of(@NonNull final Context context) {
        return new HelioCaptureStore(new File(context.getFilesDir(), DIR));
    }

    @NonNull
    public File dir() {
        return dir;
    }

    @NonNull
    public static String code(@NonNull final HuamiFetchDataType type) {
        return String.format(Locale.ROOT, "%02x", type.getCode() & 0xff);
    }

    @NonNull
    public static String hex(@Nullable final byte[] bytes) {
        if (bytes == null) {
            return "";
        }
        final StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (final byte b : bytes) {
            sb.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        }
        return sb.toString();
    }

    @Nullable
    private JSONObject readJson(@NonNull final String name) {
        try {
            return new JSONObject(new String(Files.readAllBytes(new File(dir, name).toPath()), StandardCharsets.UTF_8));
        } catch (final IOException | JSONException | RuntimeException e) {
            return null;
        }
    }

    private void writeJson(@NonNull final String name, @NonNull final JSONObject o) throws IOException {
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new IOException("cannot create " + dir);
        }
        final File tmp = new File(dir, name + ".tmp");
        Files.write(tmp.toPath(), o.toString().getBytes(StandardCharsets.UTF_8));
        if (!tmp.renameTo(new File(dir, name))) {
            throw new IOException("cannot replace " + name);
        }
    }

    public boolean isOn() {
        final JSONObject config = readJson("config.json");
        return config != null && config.optBoolean("on", false);
    }

    public void setOn(final boolean on) throws IOException {
        try {
            writeJson("config.json", new JSONObject().put("on", on));
        } catch (final JSONException e) {
            throw new IOException(e);
        }
    }

    /** Where the next request of a type starts: a day before the last answer, or three days ago. */
    public long since(@NonNull final HuamiFetchDataType type, final long now) {
        final JSONObject state = readJson("state.json");
        final JSONObject since = state != null ? state.optJSONObject("since") : null;
        final long last = since != null ? since.optLong(code(type), 0) : 0;
        if (last <= 0 || last > now) {
            return now - FIRST_SINCE_MS;
        }
        return last - OVERLAP_MS;
    }

    public void answered(@NonNull final HuamiFetchDataType type, final long now) {
        try {
            JSONObject state = readJson("state.json");
            if (state == null) {
                state = new JSONObject();
            }
            JSONObject since = state.optJSONObject("since");
            if (since == null) {
                since = new JSONObject();
                state.put("since", since);
            }
            since.put(code(type), now);
            writeJson("state.json", state);
        } catch (final IOException | JSONException e) {
            LOG.warn("Capture: could not save the time of {}", type, e);
        }
    }

    /** Total size of the saved payloads. */
    public long dataBytes() {
        long total = 0;
        for (final File f : dataFiles()) {
            total += f.length();
        }
        return total;
    }

    @NonNull
    public List<File> dataFiles() {
        final List<File> out = new ArrayList<>();
        final File[] days = dir.listFiles(f -> f.isDirectory() && f.getName().matches("\\d{8}"));
        if (days == null) {
            return out;
        }
        for (final File day : days) {
            final File[] files = day.listFiles(f -> f.isFile() && f.getName().endsWith(".bin"));
            if (files != null) {
                for (final File f : files) {
                    out.add(f);
                }
            }
        }
        return out;
    }

    /** Saves a payload as received; returns its path inside the folder ("20261103/221500-4e.bin"). */
    @NonNull
    public String saveData(@NonNull final HuamiFetchDataType type, final long now, @NonNull final byte[] payload)
            throws IOException {
        final Date at = new Date(now);
        final String day = new SimpleDateFormat("yyyyMMdd", Locale.ROOT).format(at);
        final File dayDir = new File(dir, day);
        if (!dayDir.isDirectory() && !dayDir.mkdirs()) {
            throw new IOException("cannot create " + dayDir);
        }
        String name = new SimpleDateFormat("HHmmss", Locale.ROOT).format(at) + "-" + code(type);
        File f = new File(dayDir, name + ".bin");
        for (int i = 2; f.exists(); i++) {
            f = new File(dayDir, name + "-" + i + ".bin");
        }
        try (OutputStream out = new FileOutputStream(f)) {
            out.write(payload);
        }
        return day + "/" + f.getName();
    }

    /** One line in log.jsonl. */
    public void log(@NonNull final HuamiFetchDataType type, final long now, @NonNull final String outcome,
                    @NonNull final Map<String, Object> details) {
        try {
            final JSONObject line = new JSONObject()
                    .put("at", new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ", Locale.ROOT).format(new Date(now)))
                    .put("ms", now)
                    .put("type", code(type))
                    .put("name", type.name())
                    .put("outcome", outcome);
            for (final Map.Entry<String, Object> e : details.entrySet()) {
                line.put(e.getKey(), e.getValue());
            }
            if (!dir.isDirectory() && !dir.mkdirs()) {
                throw new IOException("cannot create " + dir);
            }
            try (OutputStream out = new FileOutputStream(new File(dir, "log.jsonl"), true)) {
                out.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            }
        } catch (final IOException | JSONException e) {
            LOG.warn("Capture: could not write the log", e);
        }
    }

    @NonNull
    public List<JSONObject> logLines() {
        final List<JSONObject> out = new ArrayList<>();
        try {
            for (final String line : Files.readAllLines(new File(dir, "log.jsonl").toPath(), StandardCharsets.UTF_8)) {
                try {
                    out.add(new JSONObject(line));
                } catch (final JSONException ignored) {
                    // a line cut by a full disk: skipped
                }
            }
        } catch (final IOException ignored) {
            // no log yet
        }
        return out;
    }

    /** Everything captured goes; the on/off choice stays. */
    public void clear() {
        final File[] files = dir.listFiles();
        if (files == null) {
            return;
        }
        for (final File f : files) {
            if (!"config.json".equals(f.getName())) {
                deleteTree(f);
            }
        }
    }

    private static void deleteTree(@NonNull final File f) {
        final File[] children = f.isDirectory() ? f.listFiles() : null;
        if (children != null) {
            for (final File c : children) {
                deleteTree(c);
            }
        }
        //noinspection ResultOfMethodCallIgnored
        f.delete();
    }

    /** For the Profile: on, files, bytes and, per type, the last outcome and how many of each. */
    @NonNull
    public JSONObject summary() {
        final JSONObject o = new JSONObject();
        try {
            final List<File> files = dataFiles();
            long bytes = 0;
            for (final File f : files) {
                bytes += f.length();
            }
            o.put("on", isOn());
            o.put("files", files.size());
            o.put("bytes", bytes);
            final Map<String, JSONObject> types = new LinkedHashMap<>();
            for (final HuamiFetchDataType t : TYPES) {
                types.put(code(t), new JSONObject().put("code", code(t)).put("name", t.name())
                        .put("counts", new JSONObject()).put("bytes", 0L).put("last", JSONObject.NULL));
            }
            for (final JSONObject line : logLines()) {
                final JSONObject t = types.get(line.optString("type"));
                if (t == null) {
                    continue;
                }
                final String outcome = line.optString("outcome");
                final JSONObject counts = t.getJSONObject("counts");
                counts.put(outcome, counts.optInt(outcome, 0) + 1);
                t.put("bytes", t.getLong("bytes") + line.optLong("bytes", 0));
                t.put("last", new JSONObject().put("outcome", outcome).put("at", line.optLong("ms") / 1000)
                        .put("bytes", line.optLong("bytes", 0)).put("status", line.opt("status")));
            }
            final org.json.JSONArray list = new org.json.JSONArray();
            for (final JSONObject t : types.values()) {
                list.put(t);
            }
            o.put("types", list);
        } catch (final JSONException e) {
            throw new IllegalStateException(e);
        }
        return o;
    }
}
