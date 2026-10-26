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

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;

import nodomain.freeyourgadget.gadgetbridge.GBApplication;
import nodomain.freeyourgadget.gadgetbridge.R;
import nodomain.freeyourgadget.gadgetbridge.activities.ControlCenterv2;
import nodomain.freeyourgadget.gadgetbridge.activities.DataManagementActivity;
import nodomain.freeyourgadget.gadgetbridge.activities.charts.ActivityChartsActivity;
import nodomain.freeyourgadget.gadgetbridge.activities.devicesettings.DeviceSettingsActivity;
import nodomain.freeyourgadget.gadgetbridge.impl.GBDevice;
import nodomain.freeyourgadget.gadgetbridge.model.DeviceType;
import nodomain.freeyourgadget.gadgetbridge.model.RecordedDataTypes;

/**
 * Runs an action of the Helio band menu in the main Gadgetbridge process, where the devices
 * live: Helio (in the :helio process) starts it with an explicit intent, and it is not exported.
 * It shows nothing and finishes right away.
 */
public class HelioGbActionActivity extends Activity {
    private static final Logger LOG = LoggerFactory.getLogger(HelioGbActionActivity.class);

    public static final String EXTRA_ACTION = "nodomain.freeyourgadget.gadgetbridge.helio.EXTRA_ACTION";

    @Override
    protected void onCreate(@Nullable final Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        try {
            run(String.valueOf(getIntent().getStringExtra(EXTRA_ACTION)));
        } catch (final Exception e) {
            LOG.error("Helio action failed", e);
        } finally {
            finish();
        }
    }

    private void run(@NonNull final String action) {
        if (!HelioGbActions.ALL.contains(action)) {
            LOG.warn("Unknown Helio action {}", action);
            return;
        }
        if (HelioGbActions.DATA.equals(action)) {
            startActivity(new Intent(this, DataManagementActivity.class));
            return;
        }
        if (HelioGbActions.GADGETBRIDGE.equals(action)) {
            startActivity(new Intent(this, ControlCenterv2.class));
            return;
        }

        final GBDevice device = findBand(GBApplication.app().getDeviceManager().getDevices());
        if (HelioGbActions.AUTH_KEY.equals(action)) {
            // without a band the MAC address is typed in the screen
            startActivity(authKeyIntent(device));
            return;
        }
        if (device == null) {
            Toast.makeText(this, R.string.helio_no_band, Toast.LENGTH_LONG).show();
            startActivity(new Intent(this, ControlCenterv2.class));
            return;
        }
        switch (action) {
            case HelioGbActions.SYNC:
                if (device.isInitialized()) {
                    GBApplication.deviceService(device).onFetchRecordedData(RecordedDataTypes.TYPE_SYNC);
                } else {
                    GBApplication.deviceService(device).connect();
                    Toast.makeText(this, R.string.helio_band_connecting, Toast.LENGTH_LONG).show();
                }
                break;
            case HelioGbActions.SETTINGS:
                DeviceSettingsActivity.start(this, device);
                break;
            case HelioGbActions.CHARTS:
                startActivity(new Intent(this, ActivityChartsActivity.class).putExtra(GBDevice.EXTRA_DEVICE, device));
                break;
            default:
                break;
        }
    }

    @NonNull
    Intent authKeyIntent(@Nullable final GBDevice device) {
        final Intent i = new Intent(this, HelioAuthKeyActivity.class);
        if (device != null) {
            i.putExtra(HelioAuthKeyActivity.EXTRA_MAC, device.getAddress())
                    .putExtra(HelioAuthKeyActivity.EXTRA_NAME, device.getAliasOrName());
        }
        return i;
    }

    /** The Helio Strap, or the first device when there is none. */
    @Nullable
    static GBDevice findBand(@NonNull final List<GBDevice> devices) {
        for (final GBDevice device : devices) {
            if (device.getType() == DeviceType.AMAZFITHELIOSTRAP) {
                return device;
            }
        }
        return devices.isEmpty() ? null : devices.get(0);
    }
}
