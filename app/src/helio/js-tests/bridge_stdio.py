"""Helio behind the bridge, for the JavaScript tests: one JSON request per line on stdin,
one answer per line on stdout. Uses a test database in a temporary folder."""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

HELIO_SRC = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HELIO_SRC / "python"), str(HELIO_SRC / "helio-app"), str(HELIO_SRC / "helio-app" / "tests")]

import helio_android  # noqa: E402
from test_helio import D0, build_db  # noqa: E402


def main():
    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)
    build_db(d / "gb.db")
    os.environ["HELIO_NOW"] = str(D0 + 23 * 3600 + 59 * 60)
    helio_android.configure(str(d / "gb.db"), str(d / "state"), None, "Europe/Rome")
    print(json.dumps({"ready": True, "state": str(d / "state")}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        status, reason, headers, body = helio_android.handle(
            req["method"], req["path"], req.get("headers", {}), req.get("body", "").encode())
        print(json.dumps({"status": status, "reason": reason, "headers": dict(json.loads(headers)),
                          "body64": base64.b64encode(body).decode()}), flush=True)
    tmp.cleanup()


if __name__ == "__main__":
    main()
