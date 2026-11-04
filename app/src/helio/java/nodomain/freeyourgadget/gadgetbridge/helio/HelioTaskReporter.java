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

import android.os.SystemClock;

import androidx.annotation.NonNull;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.List;

/**
 * Tells the page how a long operation of the app goes (window.helioTask): its start, its phases with the percentage
 * when known, its end. Events are spaced out (a new phase, a new percentage point, at most one every
 * {@link #MIN_INTERVAL_MS} otherwise) so that the page is not flooded. Only names of tasks and phases and numbers.
 */
public final class HelioTaskReporter implements HelioProgress {
    /** Receives each event as JSON. */
    public interface Sink {
        void send(@NonNull String json);
    }

    static final long MIN_INTERVAL_MS = 250;
    static final List<String> TASKS = Arrays.asList("catalog_import", "export", "import", "csv", "backup", "capture");
    static final List<String> PHASES = Arrays.asList("start", "copy", "unpack", "check", "run");

    /** Milliseconds, for the spacing only (SystemClock.uptimeMillis in the app). */
    public interface Clock {
        long now();
    }

    private final String task;
    private final Sink sink;
    private final Clock clock;
    private String lastPhase;
    private long lastPercent = -2;
    private long lastAt;

    public HelioTaskReporter(@NonNull final String task, @NonNull final Sink sink) {
        this(task, sink, SystemClock::uptimeMillis);
    }

    HelioTaskReporter(@NonNull final String task, @NonNull final Sink sink, @NonNull final Clock clock) {
        if (!TASKS.contains(task)) {
            throw new IllegalArgumentException(task);
        }
        this.task = task;
        this.sink = sink;
        this.clock = clock;
    }

    public void start() {
        report("start", 0, 0);
    }

    @Override
    public synchronized void report(@NonNull final String phase, final long done, final long total) {
        if (!PHASES.contains(phase)) {
            return;
        }
        final long percent = total > 0 ? Math.max(0, Math.min(100, done * 100 / total)) : -1;
        final long now = clock.now();
        if (phase.equals(lastPhase) && (percent == lastPercent || now - lastAt < MIN_INTERVAL_MS)) {
            return;
        }
        lastPhase = phase;
        lastPercent = percent;
        lastAt = now;
        sink.send(event(phase, percent, false, true));
    }

    public synchronized void end(final boolean ok) {
        sink.send(event(lastPhase != null ? lastPhase : "start", -1, true, ok));
    }

    @NonNull
    private String event(@NonNull final String phase, final long percent, final boolean end, final boolean ok) {
        try {
            final JSONObject o = new JSONObject().put("task", task).put("phase", phase).put("end", end).put("ok", ok);
            if (percent >= 0) {
                o.put("percent", percent);
            }
            return o.toString();
        } catch (final JSONException e) {
            throw new IllegalStateException(e);
        }
    }
}
