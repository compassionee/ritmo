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

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.TextView;

import androidx.activity.ComponentActivity;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.core.content.ContextCompat;

import com.google.common.util.concurrent.ListenableFuture;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.ByteBuffer;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import nodomain.freeyourgadget.gadgetbridge.R;

/**
 * Barcode scanner for the food search: camera preview (CameraX) and ZXing on the frames, in Helio's process.
 * The camera permission is asked here, the first time the user scans (never when the app starts). The code
 * goes back to Helio, which looks it up in the local catalog only: nothing is sent anywhere.
 */
public class HelioScanActivity extends ComponentActivity {
    private static final Logger LOG = LoggerFactory.getLogger(HelioScanActivity.class);
    static final String EXTRA_CODE = "code";
    static final String EXTRA_ERROR = "error";
    static final String ERROR_PERMISSION = "permission";
    static final String ERROR_CAMERA = "camera";

    private final HelioBarcodeDecoder decoder = new HelioBarcodeDecoder();
    private ExecutorService analysis;
    private ProcessCameraProvider provider;
    private PreviewView preview;
    private volatile boolean done;

    private final ActivityResultLauncher<String> permission = registerForActivityResult(
            new ActivityResultContracts.RequestPermission(), granted -> {
                if (granted) {
                    startCamera();
                } else {
                    finishWith(null, ERROR_PERMISSION);
                }
            });

    @Override
    protected void onCreate(@Nullable final Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        final FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        preview = new PreviewView(this);
        root.addView(preview, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        final TextView hint = new TextView(this);
        hint.setText(R.string.helio_scan_hint);
        hint.setTextColor(Color.WHITE);
        hint.setShadowLayer(4, 0, 0, Color.BLACK);
        hint.setPadding(32, 32, 32, 32);
        root.addView(hint, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.CENTER_HORIZONTAL));
        final Button cancel = new Button(this);
        cancel.setText(R.string.helio_scan_cancel);
        cancel.setOnClickListener(v -> finishWith(null, null));
        final FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        lp.bottomMargin = 64;
        root.addView(cancel, lp);
        setContentView(root);

        if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)) {
            finishWith(null, ERROR_CAMERA);
            return;
        }
        if (hasCameraPermission()) {
            startCamera();
        } else {
            permission.launch(Manifest.permission.CAMERA);
        }
    }

    boolean hasCameraPermission() {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED;
    }

    void startCamera() {
        analysis = Executors.newSingleThreadExecutor();
        final ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(this);
        future.addListener(() -> {
            try {
                provider = future.get();
                final Preview p = new Preview.Builder().build();
                p.setSurfaceProvider(preview.getSurfaceProvider());
                final ImageAnalysis frames = new ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build();
                frames.setAnalyzer(analysis, this::analyze);
                provider.unbindAll();
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, p, frames);
            } catch (final Exception e) {
                LOG.error("Camera not available for the barcode scanner", e);
                finishWith(null, ERROR_CAMERA);
            }
        }, ContextCompat.getMainExecutor(this));
    }

    private void analyze(@NonNull final ImageProxy image) {
        try {
            if (done) {
                return;
            }
            final ImageProxy.PlaneProxy plane = image.getPlanes()[0];
            final ByteBuffer buffer = plane.getBuffer();
            final byte[] y = new byte[buffer.remaining()];
            buffer.get(y);
            final String code = decoder.decode(y, image.getWidth(), image.getHeight(), plane.getRowStride());
            if (code != null && !done) {
                done = true;
                runOnUiThread(() -> finishWith(code, null));
            }
        } catch (final RuntimeException e) {
            LOG.warn("Frame not decoded", e);
        } finally {
            image.close();
        }
    }

    void finishWith(@Nullable final String code, @Nullable final String error) {
        final Intent data = new Intent();
        if (code != null) {
            data.putExtra(EXTRA_CODE, code);
        }
        if (error != null) {
            data.putExtra(EXTRA_ERROR, error);
        }
        setResult(code != null ? RESULT_OK : RESULT_CANCELED, data);
        finish();
    }

    @Override
    protected void onDestroy() {
        if (provider != null) {
            provider.unbindAll();
        }
        if (analysis != null) {
            analysis.shutdown();
        }
        super.onDestroy();
    }
}
