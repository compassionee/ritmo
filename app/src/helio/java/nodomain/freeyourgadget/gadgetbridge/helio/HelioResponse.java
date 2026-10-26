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

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import java.util.List;
import java.util.Locale;

/**
 * An HTTP response produced by Helio's Handler.
 */
public class HelioResponse {
    public final int status;
    @NonNull
    public final String reason;
    /** Header name and value pairs, in order. */
    @NonNull
    public final List<String[]> headers;
    @NonNull
    public final byte[] body;

    public HelioResponse(final int status, @NonNull final String reason,
                         @NonNull final List<String[]> headers, @NonNull final byte[] body) {
        this.status = status;
        this.reason = reason;
        this.headers = headers;
        this.body = body;
    }

    @Nullable
    public String header(@NonNull final String name) {
        for (final String[] header : headers) {
            if (header[0].toLowerCase(Locale.ROOT).equals(name.toLowerCase(Locale.ROOT))) {
                return header[1];
            }
        }
        return null;
    }
}
