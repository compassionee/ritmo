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

import android.content.ContentResolver;
import android.content.Intent;
import android.content.UriPermission;
import android.database.Cursor;
import android.net.Uri;
import android.provider.DocumentsContract;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import java.io.IOException;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The backup folder chosen with Android's picker (ACTION_OPEN_DOCUMENT_TREE), through the documents provider that
 * holds it. Every failure that means "the folder is gone" (permission revoked, folder deleted, storage removed,
 * provider uninstalled) becomes a {@link HelioBackup.FolderGoneException}.
 */
final class HelioBackupFolder implements HelioBackup.Folder {
    static final int PERMISSION_FLAGS = Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION;

    private final ContentResolver resolver;
    private final Uri tree;
    /** Display name -> document id, from the last list(). */
    private final Map<String, String> ids = new HashMap<>();

    HelioBackupFolder(@NonNull final ContentResolver resolver, @NonNull final Uri tree) {
        this.resolver = resolver;
        this.tree = tree;
    }

    /** The persistent permission on the folder is still held, for reading and writing. */
    static boolean permitted(@NonNull final ContentResolver resolver, @NonNull final Uri tree) {
        for (final UriPermission p : resolver.getPersistedUriPermissions()) {
            if (tree.equals(p.getUri()) && p.isReadPermission() && p.isWritePermission()) {
                return true;
            }
        }
        return false;
    }

    /** The folder's name as the provider shows it, or null if it cannot be read. */
    @Nullable
    static String displayName(@NonNull final ContentResolver resolver, @NonNull final Uri tree) {
        try {
            return nameOf(resolver, DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree)));
        } catch (final RuntimeException e) {
            return null;
        }
    }

    @Nullable
    private static String nameOf(@NonNull final ContentResolver resolver, @NonNull final Uri doc) {
        try (Cursor c = resolver.query(doc, new String[]{DocumentsContract.Document.COLUMN_DISPLAY_NAME}, null, null, null)) {
            return c != null && c.moveToFirst() ? c.getString(0) : null;
        } catch (final RuntimeException e) {
            return null;
        }
    }

    @NonNull
    private Uri root() throws HelioBackup.FolderGoneException {
        if (!permitted(resolver, tree)) {
            throw new HelioBackup.FolderGoneException("no persisted permission");
        }
        try {
            return DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree));
        } catch (final IllegalArgumentException e) {
            throw new HelioBackup.FolderGoneException(e);
        }
    }

    @NonNull
    @Override
    public List<String> list() throws IOException {
        final Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, DocumentsContract.getDocumentId(root()));
        final List<String> names = new ArrayList<>();
        ids.clear();
        try (Cursor c = resolver.query(children, new String[]{DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME}, null, null, null)) {
            if (c == null) {
                throw new HelioBackup.FolderGoneException("folder not readable");
            }
            while (c.moveToNext()) {
                final String id = c.getString(0);
                final String name = c.getString(1);
                if (id != null && name != null) {
                    names.add(name);
                    ids.put(name, id);
                }
            }
        } catch (final SecurityException | IllegalArgumentException | IllegalStateException e) {
            throw new HelioBackup.FolderGoneException(e);
        }
        return names;
    }

    @NonNull
    @Override
    public String write(@NonNull final String name, @NonNull final byte[] data) throws IOException {
        final Uri parent = root();
        final Uri doc;
        try {
            doc = DocumentsContract.createDocument(resolver, parent, HelioArchive.EXPORT_MIME_TYPE, name);
        } catch (final SecurityException | IllegalArgumentException | IllegalStateException e) {
            throw new HelioBackup.FolderGoneException(e);
        }
        if (doc == null) {
            // the provider could not create it: the folder is gone or read-only
            throw new HelioBackup.FolderGoneException("document not created");
        }
        try (OutputStream out = resolver.openOutputStream(doc, "w")) {
            if (out == null) {
                throw new IOException("no output stream");
            }
            out.write(data);
        } catch (final IOException | RuntimeException e) {
            // no half-written backup is left behind, nor counted by the rotation
            try {
                DocumentsContract.deleteDocument(resolver, doc);
            } catch (final Exception ignored) {
                // already gone with the folder
            }
            throw e instanceof IOException ? (IOException) e : new IOException(e);
        }
        final String saved = nameOf(resolver, doc);
        return saved != null ? saved : name;
    }

    @Override
    public void delete(@NonNull final String name) throws IOException {
        final String id = ids.get(name);
        if (id == null) {
            throw new IOException("not listed");
        }
        try {
            if (!DocumentsContract.deleteDocument(resolver, DocumentsContract.buildDocumentUriUsingTree(tree, id))) {
                throw new IOException("not deleted");
            }
        } catch (final SecurityException | IllegalArgumentException | IllegalStateException e) {
            throw new IOException(e);
        }
    }
}
