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

import android.content.BroadcastReceiver;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.database.Cursor;
import android.net.Uri;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.localbroadcastmanager.content.LocalBroadcastManager;

import nodomain.freeyourgadget.gadgetbridge.impl.GBDevice;

/**
 * Not a real provider: it only gives the helio flavor a hook at the start of the main
 * Gadgetbridge process (providers are created before the Application), to record the band state
 * in {@link HelioBandState}. Not exported, and it serves no data.
 */
public class HelioBandStateProvider extends ContentProvider {
    @Override
    public boolean onCreate() {
        final Context context = getContext();
        if (context == null) {
            return false;
        }
        final Context app = context.getApplicationContext() != null ? context.getApplicationContext() : context;
        HelioBandState.resetConnections(app, System.currentTimeMillis());
        LocalBroadcastManager.getInstance(app).registerReceiver(new BroadcastReceiver() {
            @Override
            public void onReceive(final Context ctx, final Intent intent) {
                final GBDevice device = intent.getParcelableExtra(GBDevice.EXTRA_DEVICE);
                if (device != null && device.getAddress() != null) {
                    HelioBandState.record(app, device.getAddress(), device.getState().name(),
                            device.getBusyTask(), device.isInitialized(), System.currentTimeMillis());
                }
            }
        }, new IntentFilter(GBDevice.ACTION_DEVICE_CHANGED));
        return true;
    }

    @Nullable
    @Override
    public Cursor query(@NonNull final Uri uri, @Nullable final String[] projection, @Nullable final String selection,
                        @Nullable final String[] selectionArgs, @Nullable final String sortOrder) {
        return null;
    }

    @Nullable
    @Override
    public String getType(@NonNull final Uri uri) {
        return null;
    }

    @Nullable
    @Override
    public Uri insert(@NonNull final Uri uri, @Nullable final ContentValues values) {
        return null;
    }

    @Override
    public int delete(@NonNull final Uri uri, @Nullable final String selection, @Nullable final String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(@NonNull final Uri uri, @Nullable final ContentValues values, @Nullable final String selection,
                      @Nullable final String[] selectionArgs) {
        return 0;
    }
}
