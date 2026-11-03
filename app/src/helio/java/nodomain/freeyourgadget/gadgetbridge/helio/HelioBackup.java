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

import android.content.SharedPreferences;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import org.json.JSONException;
import org.json.JSONObject;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Pattern;

/**
 * Automatic backup of Ritmo's data: the same archive as the manual export (Python's export_state), written into a
 * folder the user chose once with Android's picker (persistent permission), every day, week or month, keeping the
 * last {@link #KEEP} backups. Local only: the folder is wherever the user put it, nothing goes on the network.
 * <p>
 * Ritmo does no background work: a backup that is due starts when Ritmo opens or comes back to the foreground. The
 * data in the archive changes only while Ritmo is open, so no change is ever left out of the next backup.
 * Settings and the last outcome are in the :helio process's own preferences ({@link #PREFS}), never exported.
 */
final class HelioBackup {
    private static final Logger LOG = LoggerFactory.getLogger(HelioBackup.class);

    static final String PREFS = "helio_backup";
    static final int KEEP = 5;
    static final String PREFIX = "ritmo-backup-";
    static final String EVERY_DAY = "day";
    static final String EVERY_WEEK = "week";
    static final String EVERY_MONTH = "month";
    static final List<String> EVERY = Collections.unmodifiableList(Arrays.asList(EVERY_DAY, EVERY_WEEK, EVERY_MONTH));
    /** After a failed automatic backup, the next automatic try waits this long (a manual one runs at once). */
    static final long RETRY_MS = 60 * 60 * 1000L;

    /** Error codes for the page (backup.err.&lt;code&gt;). */
    static final String ERR_NO_FOLDER = "no_folder";
    static final String ERR_FOLDER_GONE = "folder_gone";
    static final String ERR_NO_SPACE = "no_space";
    static final String ERR_WRITE = "write";
    static final String ERR_EXPORT = "export";

    /** Only these files are ever deleted by the rotation: the backups Ritmo made (a provider may add " (1)"). */
    static final Pattern BACKUP_NAME = Pattern.compile("^ritmo-backup-\\d{8}-\\d{6}(?: \\(\\d{1,3}\\))?\\.tgz$");

    private static final String K_TREE = "tree";
    private static final String K_FOLDER = "folder";
    private static final String K_EVERY = "every";
    private static final String K_LAST_OK = "last_ok";
    private static final String K_LAST_FILE = "last_file";
    private static final String K_ERROR = "error";
    private static final String K_ERROR_AT = "error_at";
    private static final String K_KEPT = "kept";

    /** The archive: the same call as the manual export. */
    interface Exporter {
        @NonNull
        byte[] export() throws Exception;
    }

    /** The chosen folder (a documents tree in the app, a plain folder in the tests). */
    interface Folder {
        @NonNull
        List<String> list() throws IOException;

        /** Writes a new file and returns its name as the folder saved it; on failure nothing is left behind. */
        @NonNull
        String write(@NonNull String name, @NonNull byte[] data) throws IOException;

        void delete(@NonNull String name) throws IOException;
    }

    /** The folder is not there anymore, or Ritmo may not write in it anymore. */
    static final class FolderGoneException extends IOException {
        FolderGoneException(@NonNull final String message) {
            super(message);
        }

        FolderGoneException(@NonNull final Throwable cause) {
            super(cause);
        }
    }

    private final SharedPreferences prefs;
    private final AtomicBoolean running = new AtomicBoolean();

    HelioBackup(@NonNull final SharedPreferences prefs) {
        this.prefs = prefs;
    }

    /** Manual export and backup write the same bytes: those of the exporter, untouched. */
    static void writeArchive(@NonNull final OutputStream out, @NonNull final Exporter exporter) throws Exception {
        out.write(exporter.export());
    }

    @NonNull
    static String fileName(final long now) {
        return PREFIX + new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date(now)) + ".tgz";
    }

    @Nullable
    String tree() {
        return prefs.getString(K_TREE, null);
    }

    @NonNull
    String every() {
        final String every = prefs.getString(K_EVERY, EVERY_WEEK);
        return EVERY.contains(every) ? every : EVERY_WEEK;
    }

    void setEvery(@NonNull final String every) {
        if (EVERY.contains(every)) {
            prefs.edit().putString(K_EVERY, every).apply();
        }
    }

    /** A new folder: the outcome of the old one does not apply to it. */
    void setFolder(@NonNull final String tree, @Nullable final String displayName) {
        prefs.edit().putString(K_TREE, tree).putString(K_FOLDER, displayName)
                .remove(K_ERROR).remove(K_ERROR_AT).remove(K_KEPT).remove(K_LAST_FILE).remove(K_LAST_OK).apply();
    }

    /** Backups stopped: the files already in the folder stay there. */
    void clearFolder() {
        prefs.edit().remove(K_TREE).remove(K_FOLDER).remove(K_ERROR).remove(K_ERROR_AT).remove(K_KEPT)
                .remove(K_LAST_FILE).remove(K_LAST_OK).apply();
    }

    boolean isRunning() {
        return running.get();
    }

    /**
     * Whether an automatic backup is due: a folder is chosen and a day, week or month (calendar, local time) went by
     * since the last one; after a failure, not before {@link #RETRY_MS}.
     */
    boolean due(final long now) {
        if (tree() == null) {
            return false;
        }
        final long errorAt = prefs.getLong(K_ERROR_AT, 0);
        if (errorAt > 0 && now >= errorAt && now - errorAt < RETRY_MS) {
            return false;
        }
        final long lastOk = prefs.getLong(K_LAST_OK, 0);
        if (lastOk <= 0 || now < lastOk) {
            // never made, or the clock went backwards
            return true;
        }
        return now >= nextDue(lastOk, every());
    }

    /** Midnight of the day, week or month after the last backup's day. */
    static long nextDue(final long lastOk, @NonNull final String every) {
        final Calendar c = Calendar.getInstance();
        c.setTimeInMillis(lastOk);
        c.set(Calendar.HOUR_OF_DAY, 0);
        c.set(Calendar.MINUTE, 0);
        c.set(Calendar.SECOND, 0);
        c.set(Calendar.MILLISECOND, 0);
        if (EVERY_DAY.equals(every)) {
            c.add(Calendar.DAY_OF_MONTH, 1);
        } else if (EVERY_MONTH.equals(every)) {
            c.add(Calendar.MONTH, 1);
        } else {
            c.add(Calendar.DAY_OF_MONTH, 7);
        }
        return c.getTimeInMillis();
    }

    /**
     * Exports, writes the archive into the folder and then deletes the oldest backups beyond {@link #KEEP} (only
     * files named like Ritmo's backups; nothing is deleted when the new one could not be written). Returns null on
     * success, otherwise the error code, saved for the page with the time. One backup at a time.
     */
    @Nullable
    String run(@Nullable final Folder folder, @NonNull final Exporter exporter, final long now) {
        if (!running.compareAndSet(false, true)) {
            return null;
        }
        try {
            final String error = backup(folder, exporter, now);
            if (error != null) {
                prefs.edit().putString(K_ERROR, error).putLong(K_ERROR_AT, now).apply();
            }
            return error;
        } finally {
            running.set(false);
        }
    }

    @Nullable
    private String backup(@Nullable final Folder folder, @NonNull final Exporter exporter, final long now) {
        if (folder == null || tree() == null) {
            return ERR_NO_FOLDER;
        }
        final byte[] archive;
        try {
            final ByteArrayOutputStream out = new ByteArrayOutputStream();
            writeArchive(out, exporter);
            archive = out.toByteArray();
        } catch (final Exception e) {
            LOG.error("Ritmo backup: export failed", e);
            return ERR_EXPORT;
        }
        final String saved;
        try {
            saved = folder.write(fileName(now), archive);
        } catch (final IOException | RuntimeException e) {
            LOG.warn("Ritmo backup: could not write into the folder", e);
            return classify(e);
        }
        int kept;
        try {
            kept = rotate(folder);
        } catch (final IOException | RuntimeException e) {
            // the new backup is there: an old one left over is not an error
            LOG.warn("Ritmo backup: rotation failed", e);
            kept = -1;
        }
        final SharedPreferences.Editor edit = prefs.edit().putLong(K_LAST_OK, now).putString(K_LAST_FILE, saved)
                .remove(K_ERROR).remove(K_ERROR_AT);
        if (kept >= 0) {
            edit.putInt(K_KEPT, kept);
        } else {
            edit.remove(K_KEPT);
        }
        edit.apply();
        return null;
    }

    /** Deletes the oldest of Ritmo's backups beyond KEEP; returns how many are left. */
    static int rotate(@NonNull final Folder folder) throws IOException {
        final List<String> backups = new ArrayList<>();
        for (final String name : folder.list()) {
            if (BACKUP_NAME.matcher(name).matches()) {
                backups.add(name);
            }
        }
        // the name carries the date and time: in name order, the oldest first
        Collections.sort(backups);
        int left = backups.size();
        for (int i = 0; i < backups.size() - KEEP; i++) {
            try {
                folder.delete(backups.get(i));
                left--;
            } catch (final IOException e) {
                LOG.warn("Ritmo backup: could not delete an old backup", e);
            }
        }
        return left;
    }

    @NonNull
    static String classify(@NonNull final Throwable e) {
        for (Throwable t = e; t != null; t = t.getCause()) {
            if (t instanceof FolderGoneException || t instanceof SecurityException
                    || t instanceof java.io.FileNotFoundException) {
                return ERR_FOLDER_GONE;
            }
            final String message = String.valueOf(t.getMessage());
            if (message.contains("ENOSPC") || message.contains("No space left")) {
                return ERR_NO_SPACE;
            }
        }
        return ERR_WRITE;
    }

    /** For the page (GET /api/android/backup and window.helioBackup): times in seconds. */
    @NonNull
    JSONObject status() {
        final JSONObject o = new JSONObject();
        try {
            final boolean on = tree() != null;
            o.put("on", on);
            o.put("folder", on ? prefs.getString(K_FOLDER, "") : JSONObject.NULL);
            o.put("every", every());
            o.put("keep", KEEP);
            o.put("running", running.get());
            final long lastOk = prefs.getLong(K_LAST_OK, 0);
            o.put("last_ok", lastOk > 0 ? lastOk / 1000 : JSONObject.NULL);
            final String lastFile = prefs.getString(K_LAST_FILE, null);
            o.put("last_file", lastFile != null ? lastFile : JSONObject.NULL);
            o.put("kept", prefs.contains(K_KEPT) ? prefs.getInt(K_KEPT, 0) : JSONObject.NULL);
            final String error = prefs.getString(K_ERROR, null);
            o.put("error", error != null ? error : JSONObject.NULL);
            final long errorAt = prefs.getLong(K_ERROR_AT, 0);
            o.put("error_at", errorAt > 0 ? errorAt / 1000 : JSONObject.NULL);
            o.put("next", on && lastOk > 0 ? nextDue(lastOk, every()) / 1000 : JSONObject.NULL);
        } catch (final JSONException e) {
            throw new IllegalStateException(e);
        }
        return o;
    }
}
