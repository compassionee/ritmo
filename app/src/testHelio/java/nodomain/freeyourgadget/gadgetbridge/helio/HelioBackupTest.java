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
import static org.junit.Assert.fail;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.provider.DocumentsContract;

import androidx.annotation.NonNull;

import org.json.JSONObject;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.List;
import java.util.Random;

@RunWith(RobolectricTestRunner.class)
public class HelioBackupTest {
    @Rule
    public TemporaryFolder tmp = new TemporaryFolder();

    private SharedPreferences prefs;
    private HelioBackup backup;
    private File dir;
    private DirFolder folder;
    /** A different archive at every export, as the real one (date in the manifest). */
    private final Random random = new Random(7);
    private final List<byte[]> exported = new ArrayList<>();
    private final HelioBackup.Exporter exporter = () -> {
        final byte[] data = new byte[64 + random.nextInt(64)];
        random.nextBytes(data);
        exported.add(data);
        return data;
    };

    /** The chosen folder as a plain directory: the same contract as the documents tree. */
    static class DirFolder implements HelioBackup.Folder {
        final File dir;
        boolean failWrites;

        DirFolder(final File dir) {
            this.dir = dir;
        }

        @NonNull
        @Override
        public List<String> list() throws IOException {
            final String[] names = dir.list();
            if (names == null) {
                throw new HelioBackup.FolderGoneException("folder missing");
            }
            return Arrays.asList(names);
        }

        @NonNull
        @Override
        public String write(@NonNull final String name, @NonNull final byte[] data) throws IOException {
            final File f = new File(dir, name);
            try (OutputStream out = new FileOutputStream(f)) {
                if (failWrites) {
                    throw new IOException("write failed (ENOSPC)");
                }
                out.write(data);
            } catch (final IOException e) {
                //noinspection ResultOfMethodCallIgnored
                f.delete();
                throw e;
            }
            return name;
        }

        @Override
        public void delete(@NonNull final String name) throws IOException {
            if (!new File(dir, name).delete()) {
                throw new IOException("not deleted");
            }
        }
    }

    @Before
    public void setUp() throws IOException {
        final Context context = RuntimeEnvironment.getApplication();
        prefs = context.getSharedPreferences(HelioBackup.PREFS, Context.MODE_PRIVATE);
        prefs.edit().clear().commit();
        backup = new HelioBackup(prefs);
        dir = tmp.newFolder("Backups");
        folder = new DirFolder(dir);
        backup.setFolder("content://test/tree/backups", "Backups");
    }

    private static long at(final int year, final int month, final int day, final int hour) {
        final Calendar c = Calendar.getInstance();
        c.clear();
        c.set(year, month - 1, day, hour, 0, 0);
        return c.getTimeInMillis();
    }

    private List<String> files() {
        final String[] names = dir.list();
        final List<String> out = new ArrayList<>(Arrays.asList(names != null ? names : new String[0]));
        Collections.sort(out);
        return out;
    }

    @Test
    public void rotationKeepsTheLastFiveAndNothingElseIsTouched() throws Exception {
        // files of the user, and names that only look like a backup
        for (final String other : new String[]{"notes.txt", "ritmo-backup-old.tgz", "ritmo-backup-20200101-000000.tgz.partial",
                "helio-state-20260101-1200.tgz"}) {
            Files.write(new File(dir, other).toPath(), new byte[]{1});
        }
        final List<String> made = new ArrayList<>();
        for (int i = 0; i < 8; i++) {
            final long now = at(2026, 10, 1 + 7 * i, 9);
            assertNull(backup.run(folder, exporter, now));
            made.add(HelioBackup.fileName(now));
            final JSONObject s = backup.status();
            assertEquals(Math.min(i + 1, HelioBackup.KEEP), s.getInt("kept"));
            assertEquals(made.get(i), s.getString("last_file"));
        }
        final List<String> expected = new ArrayList<>(made.subList(3, 8));
        expected.addAll(Arrays.asList("helio-state-20260101-1200.tgz", "notes.txt", "ritmo-backup-20200101-000000.tgz.partial",
                "ritmo-backup-old.tgz"));
        Collections.sort(expected);
        assertEquals(expected, files());
        // each kept backup is the archive exported for it
        for (int i = 3; i < 8; i++) {
            assertArrayEquals(exported.get(i), Files.readAllBytes(new File(dir, made.get(i)).toPath()));
        }
    }

    @Test
    public void rotationOrdersByDateAndTimeAndKeepsProviderCopies() throws Exception {
        for (final String name : new String[]{"ritmo-backup-20261231-235959.tgz", "ritmo-backup-20270101-000000.tgz",
                "ritmo-backup-20270101-000000 (1).tgz", "ritmo-backup-20260101-080000.tgz", "ritmo-backup-20261001-120000.tgz",
                "ritmo-backup-20250505-050505.tgz", "ritmo-backup-20260615-101010.tgz"}) {
            Files.write(new File(dir, name).toPath(), new byte[]{1});
        }
        assertEquals(5, HelioBackup.rotate(folder));
        assertEquals(Arrays.asList("ritmo-backup-20260615-101010.tgz", "ritmo-backup-20261001-120000.tgz",
                "ritmo-backup-20261231-235959.tgz", "ritmo-backup-20270101-000000 (1).tgz",
                "ritmo-backup-20270101-000000.tgz"), files());
    }

    @Test
    public void nothingIsDeletedWhenTheNewBackupFails() throws Exception {
        for (int i = 0; i < 6; i++) {
            Files.write(new File(dir, HelioBackup.fileName(at(2026, 1, 1 + i, 9))).toPath(), new byte[]{1});
        }
        final List<String> before = files();
        folder.failWrites = true;
        assertEquals(HelioBackup.ERR_NO_SPACE, backup.run(folder, exporter, at(2026, 11, 3, 9)));
        assertEquals(before, files());
        assertEquals(HelioBackup.ERR_WRITE, HelioBackup.classify(new IOException("I/O error")));
    }

    @Test
    public void folderGoneIsAClearErrorAndTheLastBackupStaysKnown() throws Exception {
        final long first = at(2026, 10, 1, 9);
        assertNull(backup.run(folder, exporter, first));
        // the user deletes the folder (or removes the memory card)
        for (final File f : dir.listFiles()) {
            assertTrue(f.delete());
        }
        assertTrue(dir.delete());
        final long later = at(2026, 10, 9, 9);
        assertTrue(backup.due(later));
        assertEquals(HelioBackup.ERR_FOLDER_GONE, backup.run(folder, exporter, later));
        assertFalse(dir.exists());
        final JSONObject s = backup.status();
        assertEquals("folder_gone", s.getString("error"));
        assertEquals(later / 1000, s.getLong("error_at"));
        assertEquals(first / 1000, s.getLong("last_ok"));
        assertTrue(s.getBoolean("on"));
        // no new try at every opening: one hour later
        assertFalse(backup.due(later + 10 * 60 * 1000));
        assertTrue(backup.due(later + HelioBackup.RETRY_MS));
        // a new folder: the error is gone and a backup is due at once
        backup.setFolder("content://test/tree/other", "Other");
        assertTrue(backup.status().isNull("error"));
        assertTrue(backup.due(later + 1000));
    }

    @Test
    public void revokedPermissionOnTheDocumentsTreeIsFolderGone() {
        // a tree Ritmo holds no persisted permission for: revoked from the system settings, or the provider is gone
        final Context context = RuntimeEnvironment.getApplication();
        final Uri tree = DocumentsContract.buildTreeDocumentUri("com.android.externalstorage.documents", "primary:Backups");
        assertFalse(HelioBackupFolder.permitted(context.getContentResolver(), tree));
        final HelioBackupFolder saf = new HelioBackupFolder(context.getContentResolver(), tree);
        try {
            saf.write("ritmo-backup-20261103-090000.tgz", new byte[]{1});
            fail("written without permission");
        } catch (final HelioBackup.FolderGoneException expected) {
            // the case the Profile explains
        } catch (final IOException e) {
            fail(e.toString());
        }
        assertEquals(HelioBackup.ERR_FOLDER_GONE, backup.run(saf, exporter, at(2026, 11, 3, 9)));
        assertEquals(HelioBackup.ERR_FOLDER_GONE, HelioBackup.classify(new SecurityException("revoked")));
        assertEquals(HelioBackup.ERR_FOLDER_GONE, HelioBackup.classify(new IOException(new java.io.FileNotFoundException("x"))));
    }

    @Test
    public void backupIsByteForByteTheManualExport() throws Exception {
        // a fixed archive: the manual export (the ⋮ menu) and the backup get it from the same exporter
        final byte[] archive = new byte[4096];
        new Random(1).nextBytes(archive);
        final HelioBackup.Exporter same = () -> archive.clone();
        final ByteArrayOutputStream manual = new ByteArrayOutputStream();
        HelioBackup.writeArchive(manual, same);
        final long now = at(2026, 11, 3, 9);
        assertNull(backup.run(folder, same, now));
        final byte[] saved = Files.readAllBytes(new File(dir, HelioBackup.fileName(now)).toPath());
        assertArrayEquals(manual.toByteArray(), saved);
        assertArrayEquals(archive, saved);
    }

    @Test
    public void failedExportWritesNothing() throws Exception {
        assertEquals(HelioBackup.ERR_EXPORT, backup.run(folder, () -> {
            throw new IllegalStateException("python");
        }, at(2026, 11, 3, 9)));
        assertEquals(Collections.emptyList(), files());
        assertEquals("export", backup.status().getString("error"));
    }

    @Test
    public void withoutFolderNothingRuns() throws Exception {
        backup.clearFolder();
        assertFalse(backup.due(at(2026, 11, 3, 9)));
        assertEquals(HelioBackup.ERR_NO_FOLDER, backup.run(null, exporter, at(2026, 11, 3, 9)));
        assertTrue(exported.isEmpty());
        final JSONObject s = backup.status();
        assertFalse(s.getBoolean("on"));
        assertTrue(s.isNull("folder"));
        assertTrue(s.isNull("last_ok"));
    }

    @Test
    public void dueByCalendarDayWeekOrMonth() {
        final long last = at(2026, 1, 31, 22);
        assertNull(backup.run(folder, exporter, last));
        assertEquals("week", backup.every());
        assertFalse(backup.due(at(2026, 2, 6, 23)));
        assertTrue(backup.due(at(2026, 2, 7, 0)));

        backup.setEvery(HelioBackup.EVERY_DAY);
        assertFalse(backup.due(at(2026, 1, 31, 23)));
        assertTrue(backup.due(at(2026, 2, 1, 0)));  // the next calendar day, not 24 hours

        backup.setEvery(HelioBackup.EVERY_MONTH);
        assertFalse(backup.due(at(2026, 2, 27, 12)));
        assertTrue(backup.due(at(2026, 2, 28, 0)));  // 31 January + 1 month: end of February

        backup.setEvery("hour");  // unknown: ignored
        assertEquals("month", backup.every());
        // a clock that went backwards does not stop the backups
        assertTrue(backup.due(last - 1000));
    }

    @Test
    public void statusForThePage() throws Exception {
        final long now = at(2026, 11, 3, 9);
        assertNull(backup.run(folder, exporter, now));
        backup.setEvery(HelioBackup.EVERY_MONTH);
        final JSONObject s = backup.status();
        assertEquals("Backups", s.getString("folder"));
        assertEquals("month", s.getString("every"));
        assertEquals(5, s.getInt("keep"));
        assertEquals(now / 1000, s.getLong("last_ok"));
        assertEquals(at(2026, 12, 3, 0) / 1000, s.getLong("next"));
        assertFalse(s.getBoolean("running"));
        assertTrue(s.isNull("error"));
        // the content URI of the folder never reaches the page
        assertFalse(s.toString().contains("content://"));
    }

    @Test
    public void namesMatchTheRotationPattern() {
        final String name = HelioBackup.fileName(at(2026, 11, 3, 9));
        assertEquals("ritmo-backup-20261103-090000.tgz", name);
        assertTrue(HelioBackup.BACKUP_NAME.matcher(name).matches());
        assertFalse(HelioBackup.BACKUP_NAME.matcher("ritmo-backup-20261103-090000.tgz.tgz").matches());
        assertFalse(HelioBackup.BACKUP_NAME.matcher("../ritmo-backup-20261103-090000.tgz").matches());
    }

    @Test
    public void everyBackupActionIsApprovedByThePythonSide() {
        for (final String action : new String[]{HelioGbActions.BACKUP_FOLDER, HelioGbActions.BACKUP_NOW,
                HelioGbActions.BACKUP_OFF, HelioGbActions.BACKUP_DAY, HelioGbActions.BACKUP_WEEK, HelioGbActions.BACKUP_MONTH}) {
            assertTrue(action, HelioGbActions.ALL.contains(action));
            assertTrue(action, action.startsWith("backup_"));
        }
        assertTrue(HelioTaskReporter.TASKS.contains("backup"));
    }
}
