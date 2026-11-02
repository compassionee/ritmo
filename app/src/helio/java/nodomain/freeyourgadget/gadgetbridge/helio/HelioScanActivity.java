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
import android.util.Size;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.activity.ComponentActivity;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.annotation.VisibleForTesting;
import androidx.camera.core.Camera;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
import androidx.camera.core.resolutionselector.ResolutionSelector;
import androidx.camera.core.resolutionselector.ResolutionStrategy;
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
 * The camera permission is asked here, the first time the user scans (never when the app starts). Frames are
 * analysed at 1280x720 (or the closest size the camera has), the central band first, where the guide line is;
 * a code is accepted only when several reads agree (HelioScanConfirm). The code goes back to Helio,
 * which shows it, editable, and looks it up in the local catalog only: nothing is sent anywhere.
 */
public class HelioScanActivity extends ComponentActivity {
    private static final Logger LOG = LoggerFactory.getLogger(HelioScanActivity.class);
    static final String EXTRA_CODE = "code";
    static final String EXTRA_ERROR = "error";
    static final String ERROR_PERMISSION = "permission";
    static final String ERROR_CAMERA = "camera";

    /** Size asked for the analysed frames: enough pixels per bar for EAN-13 at a normal distance. */
    static final Size ANALYSIS_SIZE = new Size(1280, 720);

    private final HelioBarcodeDecoder decoder = new HelioBarcodeDecoder();
    private final HelioScanConfirm confirm = new HelioScanConfirm();
    @VisibleForTesting
    final HelioScanStats.Scan stats = new HelioScanStats.Scan();
    @Nullable
    private String outcome;
    private ExecutorService analysis;
    private ProcessCameraProvider provider;
    private PreviewView preview;
    @VisibleForTesting
    Button torch;
    @VisibleForTesting
    View guide;
    private Camera camera;
    private boolean torchOn;
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
        final float dp = getResources().getDisplayMetrics().density;
        final TextView hint = new TextView(this);
        hint.setText(R.string.helio_scan_hint);
        hint.setTextColor(Color.WHITE);
        hint.setShadowLayer(4, 0, 0, Color.BLACK);
        hint.setPadding(32, 32, 32, 32);
        root.addView(hint, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.CENTER_HORIZONTAL));
        // guide line across the middle: the band of the frame decoded first (HelioBarcodeDecoder.BAND)
        guide = new View(this);
        guide.setBackgroundColor(0xFFFF3B30);
        final FrameLayout.LayoutParams gl = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                Math.round(2 * dp), Gravity.CENTER);
        gl.leftMargin = gl.rightMargin = Math.round(24 * dp);
        root.addView(guide, gl);
        final LinearLayout buttons = new LinearLayout(this);
        buttons.setGravity(Gravity.CENTER);
        torch = new Button(this);
        torch.setText(R.string.helio_scan_torch_on);
        torch.setVisibility(View.GONE);  // shown once the camera says it has a flash
        torch.setOnClickListener(v -> setTorch(!torchOn));
        buttons.addView(torch);
        final Button cancel = new Button(this);
        cancel.setText(R.string.helio_scan_cancel);
        cancel.setOnClickListener(v -> finishWith(null, null));
        buttons.addView(cancel);
        final FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        lp.bottomMargin = 64;
        root.addView(buttons, lp);
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
        stats.cameraAsked(HelioStartup.now());
        analysis = Executors.newSingleThreadExecutor();
        final ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(this);
        future.addListener(() -> {
            try {
                provider = future.get();
                final Preview p = new Preview.Builder().build();
                p.setSurfaceProvider(preview.getSurfaceProvider());
                try {
                    camera = bind(p, analysisSelector());
                } catch (final IllegalArgumentException e) {
                    // the size cannot be combined with the preview on this camera: CameraX's default size
                    LOG.warn("Barcode scanner: analysis at {} not supported, default size", ANALYSIS_SIZE, e);
                    camera = bind(p, null);
                }
                setTorchAvailable(camera.getCameraInfo().hasFlashUnit());
            } catch (final Exception e) {
                LOG.error("Camera not available for the barcode scanner", e);
                finishWith(null, ERROR_CAMERA);
            }
        }, ContextCompat.getMainExecutor(this));
    }

    /**
     * 1280x720, else the closest size above, else below (ResolutionStrategy fallback); the 16:9 ratio of the
     * size is only a wish, CameraX keeps the camera's own ratios.
     */
    @NonNull
    static ResolutionSelector analysisSelector() {
        return new ResolutionSelector.Builder()
                .setResolutionStrategy(new ResolutionStrategy(ANALYSIS_SIZE,
                        ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                .build();
    }

    @NonNull
    private Camera bind(@NonNull final Preview p, @Nullable final ResolutionSelector size) {
        final ImageAnalysis.Builder b = new ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST);
        if (size != null) {
            b.setResolutionSelector(size);
        }
        final ImageAnalysis frames = b.build();
        frames.setAnalyzer(analysis, this::analyze);
        provider.unbindAll();
        return provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, p, frames);
    }

    void setTorchAvailable(final boolean available) {
        torch.setVisibility(available ? View.VISIBLE : View.GONE);
    }

    void setTorch(final boolean on) {
        torchOn = on;
        torch.setText(on ? R.string.helio_scan_torch_off : R.string.helio_scan_torch_on);
        if (camera != null) {
            camera.getCameraControl().enableTorch(on);
        }
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
            final int rotation = image.getImageInfo().getRotationDegrees();
            final long start = HelioStartup.now();
            final HelioBarcodeDecoder.Read read = decoder.read(y, image.getWidth(), image.getHeight(), plane.getRowStride(), rotation);
            stats.frame(start, HelioStartup.now(), rotation % 180 == 0 ? image.getWidth() + "x" + image.getHeight()
                    : image.getHeight() + "x" + image.getWidth(), read, decoder.rejected);
            final String code = confirm.offer(read, HelioStartup.now());
            if (code != null && !done) {
                stats.accepted(read.format);
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
        outcome = code != null ? "ok" : error != null ? error : "cancel";
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
        // also when the scanner is left with Back: a scan without a code
        HelioScanStats.add(stats.json(outcome != null ? outcome : "cancel", HelioStartup.now()));
        super.onDestroy();
    }
}
