"""Export and import of Helio's data as a single .tgz, the same on the server and in the app.

Archive content:
    helio-export.json      format, version, date, SHA-256 hashes of the files and profile
    state/<name>.json      the files of the HELIO_STATE folder (labels, plan, weight, strength, foods, diary, settings)

The profile contains only measurements and goals (PROFILE_KEYS): never passwords, certificates,
addresses or paths.

On the server:
    python3 helio_transfer.py export --state /var/lib/helio-app --env /etc/helio-app.env \\
        [--plan /etc/helio-plan.json] -o helio-state.tgz
    python3 helio_transfer.py check helio-state.tgz
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "helio-state"
VERSION = 1
MANIFEST = "helio-export.json"
STATE_RE = re.compile(r"^(?:labels|plan|strength|weights|foods|nutrition|profile|settings|diary-\d{4}-\d{2})\.json$")
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 1000
KEEP_BACKUPS = 3

# measurements and goals: the only settings that move from one device to another
PROFILE_KEYS = {
    "HELIO_AGE": "int", "HELIO_SEX": "sex", "HELIO_HRMAX": "int", "HELIO_HEIGHT": "num", "HELIO_WEIGHT": "num",
    "HELIO_STEP_GOAL": "int", "HELIO_SLEEP_GOAL": "int", "HELIO_STRIDE_M": "num", "HELIO_ACTIVE_GOAL": "int",
    "HELIO_ZONE_GOAL": "int", "HELIO_GOAL_ALT": "int", "HELIO_GOAL_NAME": "text",
}


I18N_DIR = Path(__file__).resolve().parent / "static" / "i18n"


class TransferError(ValueError):
    """Archive error: key and parameters (msg), translated by the page or by text() on the command line."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.msg = {"$": key, **params}

    def text(self, lang="en"):
        try:
            raw = json.loads((I18N_DIR / f"{lang}.json").read_text()).get(self.msg["$"], self.msg["$"])
        except (OSError, ValueError):
            raw = self.msg["$"]
        return re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: str(self.msg.get(m.group(1), "")), raw)

    def __str__(self):
        return self.text("en")


def clean_profile(raw):
    """Keeps only the profile keys, with valid values. The others are ignored."""
    out = {}
    for key, value in (raw or {}).items():
        kind = PROFILE_KEYS.get(key)
        if kind is None or not isinstance(value, str):
            continue
        value = value.strip()
        if not value or len(value) > 60 or any(ord(c) < 0x20 for c in value):
            continue
        try:
            if kind == "int":
                int(value)
            elif kind == "num":
                float(value)
            elif kind == "sex" and value not in ("M", "F"):
                continue
        except ValueError:
            continue
        out[key] = value
    return out


def read_env_profile(path):
    """The profile from a KEY=VALUE file such as /etc/helio-app.env (lines with # are ignored)."""
    raw = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        raw[key.strip()] = value.strip().strip('"').strip("'")
    return clean_profile(raw)


def load_profile(path):
    """The profile saved in the app (JSON); empty if missing or invalid."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return clean_profile(data) if isinstance(data, dict) else {}


def save_profile(path, profile):
    _write_atomic(Path(path), json.dumps(clean_profile(profile), ensure_ascii=False, indent=1).encode())


def _check_state_json(name, data):
    try:
        obj = json.loads(data)
    except ValueError:
        raise TransferError("x.json_invalid", name=name)
    if not isinstance(obj, dict):
        raise TransferError("x.json_not_object", name=name)


def state_files(state_dir):
    """The recognised state files, with their content."""
    out = {}
    d = Path(state_dir)
    if not d.is_dir():
        return out
    for p in sorted(d.iterdir()):
        if STATE_RE.match(p.name) and p.is_file() and not p.is_symlink():
            data = p.read_bytes()
            if len(data) > MAX_FILE:
                raise TransferError("x.file_too_big", name=p.name)
            _check_state_json(p.name, data)
            out[p.name] = data
    if len(out) > MAX_FILES:
        raise TransferError("x.too_many_files")
    return out


def export_bytes(state_dir, profile=None, plan_file=None):
    """Creates the archive in memory.

    plan_file: the server's configuration plan (/etc/helio-plan.json). If the data folder has no plan
    edited in the app, it is exported as plan.json: the app has no plan of its own to fall back on,
    so the server's plan travels with the data.
    """
    files = state_files(state_dir)
    if plan_file and "plan.json" not in files and Path(plan_file).is_file():
        data = Path(plan_file).read_bytes()
        _check_state_json("plan.json", data)
        files["plan.json"] = data
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
        "profile": clean_profile(profile or {}),
    }
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = now
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(data))

        add(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1).encode())
        for name, data in sorted(files.items()):
            add(f"state/{name}", data)
    return buf.getvalue()


def read_archive(data):
    """Checks the archive and returns (profile, {name: content}). Writes nothing to disk."""
    if len(data) > MAX_TOTAL:
        raise TransferError("x.archive_too_big")
    manifest = None
    files = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for n, member in enumerate(tar):
                if n > MAX_FILES:
                    raise TransferError("x.too_many_files")
                if not member.isreg():
                    raise TransferError("x.not_regular", name=member.name)
                if member.size > MAX_FILE:
                    raise TransferError("x.file_too_big", name=member.name)
                total += member.size
                if total > MAX_TOTAL:
                    raise TransferError("x.content_too_big")
                if member.name == MANIFEST:
                    if manifest is not None:
                        raise TransferError("x.manifest_repeated")
                    content = tar.extractfile(member).read()
                    try:
                        manifest = json.loads(content)
                    except ValueError:
                        raise TransferError("x.manifest_invalid")
                    continue
                prefix, _, name = member.name.partition("/")
                if prefix != "state" or not STATE_RE.match(name):
                    raise TransferError("x.unexpected_name", name=member.name)
                if name in files:
                    raise TransferError("x.repeated", name=name)
                files[name] = tar.extractfile(member).read()
    except (tarfile.TarError, EOFError, OSError) as e:
        raise TransferError("x.unreadable", detail=str(e))

    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise TransferError("x.not_export")
    if manifest.get("version") != VERSION:
        raise TransferError("x.version", version=str(manifest.get("version")))
    hashes = manifest.get("files")
    if not isinstance(hashes, dict) or set(hashes) != set(files):
        raise TransferError("x.list_mismatch")
    for name, content in files.items():
        if hashlib.sha256(content).hexdigest() != hashes[name]:
            raise TransferError("x.hash_mismatch", name=name)
        _check_state_json(name, content)
    profile = manifest.get("profile", {})
    if not isinstance(profile, dict):
        raise TransferError("x.profile_invalid")
    return clean_profile(profile), files


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def import_bytes(data, state_dir, keep_backups=KEEP_BACKUPS):
    """Replaces the data folder with the archive content, after checking it.

    The previous folder stays next to it as <name>.bak-<date> (the last keep_backups).
    Returns (profile, number of files, backup path or None).
    """
    profile, files = read_archive(data)
    state = Path(state_dir)
    parent = state.parent
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    tmp = parent / f".{state.name}.import-{stamp}"
    tmp.mkdir(mode=0o700)
    try:
        for name, content in files.items():
            with open(tmp / name, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        _fsync_dir(tmp)
        backup = None
        if state.exists():
            backup = parent / f"{state.name}.bak-{stamp}"
            os.replace(state, backup)
        os.replace(tmp, state)
        _fsync_dir(parent)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    old = sorted(parent.glob(f"{state.name}.bak-*"))
    for extra in old[:-keep_backups] if keep_backups else old:
        shutil.rmtree(extra, ignore_errors=True)
    return profile, len(files), str(backup) if backup else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export or check Helio data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="create the archive")
    ex.add_argument("--state", default=os.environ.get("HELIO_STATE", "/var/lib/helio-app"))
    ex.add_argument("--env", help="KEY=VALUE file with the profile, for example /etc/helio-app.env")
    ex.add_argument("--plan", help="configuration plan, for example /etc/helio-plan.json")
    ex.add_argument("-o", "--output", required=True)
    ck = sub.add_parser("check", help="check an archive and list its content")
    ck.add_argument("archive")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "export":
            profile = read_env_profile(args.env) if args.env else {}
            data = export_bytes(args.state, profile, args.plan)
            out = Path(args.output)
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            _, files = read_archive(data)
            print(f"Created {out}: {len(files)} data files, profile: {', '.join(sorted(profile)) or 'none'}")
        else:
            profile, files = read_archive(Path(args.archive).read_bytes())
            for name in sorted(files):
                print(f"state/{name}  {len(files[name])} byte")
            print("profile:", json.dumps(profile, ensure_ascii=False))
    except (OSError, TransferError) as e:
        print(f"Error: {e.text('en') if isinstance(e, TransferError) else e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
