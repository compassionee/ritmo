"""Export and import of Helio data (helio_transfer.py)."""
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import helio_transfer as ht  # noqa: E402

ENV_FILE = """# Edit these values and then: systemctl restart helio-app
HELIO_DB=/srv/example/Gadgetbridge.db
HELIO_DEVICE_ID=1
HELIO_BIND=127.0.0.1
HELIO_PORT=8080
HELIO_TZ=Europe/Rome
HELIO_PLAN=/etc/helio-plan.json
#HELIO_AGE=30
HELIO_ZONE_GOAL=150
HELIO_HEIGHT=171
HELIO_WEIGHT=74
HELIO_STRIDE_M=0.71
HELIO_ACTIVE_GOAL=60
HELIO_SEX=X
HELIO_TLS_BIND=192.0.2.20
HELIO_TLS_KEY=/etc/helio/tls.key
HELIO_USER=helio
HELIO_PASSWORD=must-not-leave
"""


def tgz(members):
    """Archive built by hand, for cases export_bytes would never produce."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = data
                tar.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
    return buf.getvalue()


def manifest(files, profile=None):
    return json.dumps({"format": ht.FORMAT, "version": ht.VERSION, "created": "2026-10-13T10:00:00+00:00",
                       "files": {n: hashlib.sha256(d).hexdigest() for n, d in files.items()},
                       "profile": profile or {}}).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "state"
        self.state.mkdir()
        self.files = {
            "labels.json": {"2026-03-10T07:00": {"type": "run"}},
            "plan.json": {"days": {"1": "strength"}},
            "weights.json": {"2026-03-10": 81.5},
            "strength.json": {"2026-03-09": [{"name": "Squat", "sets": [[8, 60]]}]},
            "foods.json": {"foods": {"f1": {"name": "Crème brûlée"}}, "meals": {}},
            "nutrition.json": {"goal": "mantenimento"},
            "diary-2026-03.json": {"2026-03-10": [{"food": "f1", "g": 80}]},
        }
        for name, obj in self.files.items():
            (self.state / name).write_text(json.dumps(obj, ensure_ascii=False))

    def tearDown(self):
        self.tmp.cleanup()

    def tree(self, d):
        return {p.name: p.read_bytes() for p in sorted(Path(d).iterdir()) if p.is_file()}


class RoundTrip(Base):
    def test_export_import_same_files_and_profile(self):
        profile = {"HELIO_HEIGHT": "171", "HELIO_WEIGHT": "74", "HELIO_SEX": "M"}
        data = ht.export_bytes(self.state, profile)
        target = self.dir / "phone" / "helio-state"
        got_profile, n, backup = ht.import_bytes(data, target)
        self.assertEqual(n, len(self.files))
        self.assertIsNone(backup)
        self.assertEqual(self.tree(target), self.tree(self.state))
        self.assertEqual(got_profile, profile)
        # and back again: export from the app, import elsewhere, same files
        again = self.dir / "again"
        ht.import_bytes(ht.export_bytes(target, got_profile), again)
        self.assertEqual(self.tree(again), self.tree(self.state))

    def test_import_replaces_and_keeps_backup(self):
        target = self.dir / "app-state"
        target.mkdir()
        (target / "weights.json").write_text('{"vecchio": 1}')
        (target / "labels.json").write_text('{"vecchio": 2}')
        _, _, backup = ht.import_bytes(ht.export_bytes(self.state), target)
        self.assertEqual(self.tree(target), self.tree(self.state))  # no old files left
        self.assertEqual((Path(backup) / "weights.json").read_text(), '{"vecchio": 1}')

    def test_only_last_backups_kept(self):
        target = self.dir / "app-state"
        data = ht.export_bytes(self.state)
        for _ in range(ht.KEEP_BACKUPS + 3):
            ht.import_bytes(data, target)
        backups = list(self.dir.glob("app-state.bak-*"))
        self.assertEqual(len(backups), ht.KEEP_BACKUPS)
        self.assertEqual(list(self.dir.glob(".app-state.import-*")), [])

    def test_unknown_and_broken_files_are_not_exported(self):
        (self.state / "appunti.txt").write_text("x")
        (self.state / "labels.json.tmp").write_text("{")
        os.symlink(self.state / "weights.json", self.state / "foods-link.json")
        _, files = ht.read_archive(ht.export_bytes(self.state))
        self.assertEqual(set(files), set(self.files))

    def test_invalid_state_file_stops_export(self):
        (self.state / "weights.json").write_text("{rotto")
        with self.assertRaises(ht.TransferError):
            ht.export_bytes(self.state)

    def test_empty_state(self):
        target = self.dir / "vuota"
        profile, n, _ = ht.import_bytes(ht.export_bytes(self.dir / "non-esiste"), target)
        self.assertEqual((profile, n), ({}, 0))
        self.assertEqual(self.tree(target), {})

    def test_server_plan_exported_only_if_app_has_none(self):
        conf = self.dir / "helio-plan.json"
        conf.write_text('{"days": {"2": "run"}}')
        _, files = ht.read_archive(ht.export_bytes(self.state, plan_file=conf))
        self.assertEqual(json.loads(files["plan.json"]), self.files["plan.json"])
        (self.state / "plan.json").unlink()
        _, files = ht.read_archive(ht.export_bytes(self.state, plan_file=conf))
        self.assertEqual(json.loads(files["plan.json"]), {"days": {"2": "run"}})


class Profile(Base):
    def test_env_file_keeps_only_profile(self):
        env = self.dir / "helio-app.env"
        env.write_text(ENV_FILE)
        profile = ht.read_env_profile(env)
        self.assertEqual(profile, {"HELIO_ZONE_GOAL": "150", "HELIO_HEIGHT": "171", "HELIO_WEIGHT": "74",
                                   "HELIO_STRIDE_M": "0.71", "HELIO_ACTIVE_GOAL": "60"})
        data = ht.export_bytes(self.state, profile)
        for secret in (b"must-not-leave", b"tls.key", b"192.0.2.20", b"/srv/example"):
            self.assertNotIn(secret, tarfile.open(fileobj=io.BytesIO(data)).extractfile(ht.MANIFEST).read())

    def test_profile_values_checked(self):
        self.assertEqual(ht.clean_profile({"HELIO_HEIGHT": "high", "HELIO_AGE": "30", "HELIO_SEX": "F",
                                           "HELIO_GOAL_NAME": "Monte\nBianco", "HELIO_PASSWORD": "x",
                                           "HELIO_WEIGHT": 74}),
                         {"HELIO_AGE": "30", "HELIO_SEX": "F"})

    def test_profile_file(self):
        p = self.dir / "helio-config.json"
        self.assertEqual(ht.load_profile(p), {})
        ht.save_profile(p, {"HELIO_HEIGHT": "180", "HELIO_DB": "/x"})
        self.assertEqual(ht.load_profile(p), {"HELIO_HEIGHT": "180"})
        p.write_text("[1]")
        self.assertEqual(ht.load_profile(p), {})


class RejectedArchives(Base):
    def assertRejected(self, data, target=None):
        target = target or self.dir / "app-state"
        target.mkdir(exist_ok=True)
        (target / "weights.json").write_text('{"mine": 1}')
        with self.assertRaises(ht.TransferError):
            ht.import_bytes(data, target)
        self.assertEqual(self.tree(target), {"weights.json": b'{"mine": 1}'})  # nothing changed

    def test_not_an_archive(self):
        self.assertRejected(b"not a tgz")

    def test_path_traversal_and_odd_names(self):
        good = {"weights.json": b"{}"}
        for name in ("state/../weights.json", "../state/weights.json", "/state/weights.json",
                     "state/sub/weights.json", "state/evil.sh", "other/weights.json"):
            with self.subTest(name=name):
                self.assertRejected(tgz([(ht.MANIFEST, manifest(good), "file"), (name, b"{}", "file")]))

    def test_links_and_dirs(self):
        good = {}
        self.assertRejected(tgz([(ht.MANIFEST, manifest(good), "file"), ("state/weights.json", "/etc/passwd", "symlink")]))
        self.assertRejected(tgz([(ht.MANIFEST, manifest(good), "file"), ("state", None, "dir")]))

    def test_hash_and_manifest_mismatch(self):
        files = {"weights.json": b'{"a": 1}'}
        self.assertRejected(tgz([(ht.MANIFEST, manifest(files), "file"), ("state/weights.json", b'{"a": 2}', "file")]))
        self.assertRejected(tgz([(ht.MANIFEST, manifest(files), "file")]))
        self.assertRejected(tgz([("state/weights.json", b'{"a": 1}', "file")]))
        wrong = json.loads(manifest(files))
        wrong["format"] = "other"
        self.assertRejected(tgz([(ht.MANIFEST, json.dumps(wrong).encode(), "file"),
                                 ("state/weights.json", b'{"a": 1}', "file")]))
        wrong["format"], wrong["version"] = ht.FORMAT, 99
        self.assertRejected(tgz([(ht.MANIFEST, json.dumps(wrong).encode(), "file"),
                                 ("state/weights.json", b'{"a": 1}', "file")]))

    def test_invalid_json_inside(self):
        files = {"weights.json": b"{rotto"}
        self.assertRejected(tgz([(ht.MANIFEST, manifest(files), "file"), ("state/weights.json", b"{rotto", "file")]))
        files = {"weights.json": b"[1, 2]"}
        self.assertRejected(tgz([(ht.MANIFEST, manifest(files), "file"), ("state/weights.json", b"[1, 2]", "file")]))

    def test_size_limits(self):
        big = b'{"x": "' + b"a" * ht.MAX_FILE + b'"}'
        files = {"weights.json": big}
        self.assertRejected(tgz([(ht.MANIFEST, manifest(files), "file"), ("state/weights.json", big, "file")]))

    def test_profile_secrets_dropped_on_import(self):
        files = {"weights.json": b"{}"}
        data = tgz([(ht.MANIFEST, manifest(files, {"HELIO_PASSWORD": "x", "HELIO_HEIGHT": "170"}), "file"),
                    ("state/weights.json", b"{}", "file")])
        profile, _, _ = ht.import_bytes(data, self.dir / "ok")
        self.assertEqual(profile, {"HELIO_HEIGHT": "170"})


class CommandLine(Base):
    def test_export_and_check(self):
        env = self.dir / "helio-app.env"
        env.write_text(ENV_FILE)
        out = self.dir / "helio-state.tgz"
        self.assertEqual(ht.main(["export", "--state", str(self.state), "--env", str(env), "-o", str(out)]), 0)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ht.main(["check", str(out)]), 0)
        profile, files = ht.read_archive(out.read_bytes())
        self.assertEqual(set(files), set(self.files))
        self.assertEqual(profile["HELIO_HEIGHT"], "171")

    def test_check_reports_bad_archive(self):
        bad = self.dir / "x.tgz"
        bad.write_bytes(b"xx")
        self.assertEqual(ht.main(["check", str(bad)]), 1)


if __name__ == "__main__":
    unittest.main()
