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
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import nodomain.freeyourgadget.gadgetbridge.GBApplication;
import nodomain.freeyourgadget.gadgetbridge.R;

/**
 * Field to type or paste the band's auth key, from the Helio band menu. It runs in the main Gadgetbridge
 * process (where the device preferences live), is not exported and is started only by HelioGbActionActivity.
 * The key is checked (32 hex digits, optionally after "0x") and saved in the band's preferences, by MAC
 * address; it is never logged nor shown again. Without a band in Gadgetbridge the MAC address is typed too,
 * so the key is ready when the band is added.
 */
public class HelioAuthKeyActivity extends Activity {
    static final String EXTRA_MAC = "nodomain.freeyourgadget.gadgetbridge.helio.EXTRA_MAC";
    static final String EXTRA_NAME = "nodomain.freeyourgadget.gadgetbridge.helio.EXTRA_NAME";

    @Nullable
    private String bandMac;
    EditText macField;
    EditText keyField;
    TextView errorText;
    TextView savedText;
    Button saveButton;

    @Override
    protected void onCreate(@Nullable final Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle(R.string.helio_authkey_title);
        bandMac = HelioAuthKey.normalizeMac(getIntent().getStringExtra(EXTRA_MAC));
        final String name = getIntent().getStringExtra(EXTRA_NAME);

        final int pad = dp(20);
        final LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, pad, pad, pad);

        final TextView title = new TextView(this);
        title.setText(R.string.helio_authkey_title);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
        box.addView(title);

        final TextView band = new TextView(this);
        band.setPadding(0, dp(8), 0, dp(8));
        if (bandMac != null) {
            band.setText(getString(R.string.helio_authkey_band, name == null || name.isEmpty() ? bandMac : name, bandMac));
        } else {
            band.setText(R.string.helio_authkey_no_band);
        }
        box.addView(band);

        macField = new EditText(this);
        macField.setHint(R.string.helio_authkey_mac_hint);
        macField.setSingleLine(true);
        macField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
                | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS);
        macField.setVisibility(bandMac == null ? View.VISIBLE : View.GONE);
        box.addView(macField);

        final TextView help = new TextView(this);
        help.setText(R.string.helio_authkey_help);
        help.setPadding(0, dp(8), 0, 0);
        box.addView(help);

        keyField = new EditText(this);
        keyField.setHint(R.string.helio_authkey_hint);
        keyField.setSingleLine(true);
        keyField.setTypeface(Typeface.MONOSPACE);
        // no suggestions and no keyboard learning: the key is a secret
        keyField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        keyField.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        box.addView(keyField);

        errorText = new TextView(this);
        errorText.setTextColor(0xFFE5484D);
        errorText.setVisibility(View.GONE);
        box.addView(errorText);

        savedText = new TextView(this);
        savedText.setText(R.string.helio_authkey_already_saved);
        savedText.setPadding(0, dp(8), 0, 0);
        box.addView(savedText);
        showSaved();

        final LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        buttons.setPadding(0, dp(16), 0, 0);
        final Button cancel = new Button(this);
        cancel.setText(R.string.helio_scan_cancel);
        cancel.setOnClickListener(v -> finish());
        saveButton = new Button(this);
        saveButton.setText(R.string.helio_authkey_save);
        saveButton.setOnClickListener(v -> save());
        buttons.addView(cancel);
        buttons.addView(saveButton);
        box.addView(buttons);

        final ScrollView scroll = new ScrollView(this);
        scroll.addView(box);
        setContentView(scroll);
    }

    private int dp(final int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    /** The band's MAC: the one from Helio, or the typed one (null when invalid). */
    @Nullable
    private String mac() {
        return bandMac != null ? bandMac : HelioAuthKey.normalizeMac(macField.getText().toString());
    }

    private void showSaved() {
        final String mac = mac();
        savedText.setVisibility(mac != null && HelioAuthKey.hasKey(devicePrefs(mac)) ? View.VISIBLE : View.GONE);
    }

    /** The band's own preferences, the same file Gadgetbridge reads. */
    @Nullable
    SharedPreferences devicePrefs(@NonNull final String mac) {
        return GBApplication.getDeviceSpecificSharedPrefs(mac);
    }

    void save() {
        final String mac = mac();
        if (mac == null) {
            showError(getString(R.string.helio_authkey_bad_mac));
            return;
        }
        final String typed = keyField.getText().toString();
        switch (HelioAuthKey.check(typed)) {
            case EMPTY:
                showError(getString(R.string.helio_authkey_empty));
                return;
            case BAD_CHARACTERS:
                showError(getString(R.string.helio_authkey_bad_characters));
                return;
            case BAD_LENGTH:
                showError(getString(R.string.helio_authkey_bad_length, HelioAuthKey.HEX_DIGITS,
                        HelioAuthKey.digits(typed).length()));
                return;
            default:
                break;
        }
        final SharedPreferences prefs = devicePrefs(mac);
        if (prefs == null || !HelioAuthKey.save(prefs, typed)) {
            showError(getString(R.string.helio_authkey_save_failed));
            return;
        }
        keyField.setText("");
        Toast.makeText(this, R.string.helio_authkey_saved, Toast.LENGTH_LONG).show();
        setResult(RESULT_OK);
        finish();
    }

    private void showError(@NonNull final String message) {
        errorText.setText(message);
        errorText.setVisibility(View.VISIBLE);
    }
}
