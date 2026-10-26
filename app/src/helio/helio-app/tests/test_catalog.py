"""Food catalog tests: conversions of the sources (USDA, Ciqual, Open Food Facts) to the app's values per 100 g,
the package (format, determinism, every integrity check), installation next to the user's data, search
(ranking, accents, barcodes in every form), the Open Food Facts records (European filter, names, merging of
the same code) and the Italian names of the generic foods."""
import hashlib
import io
import json
import lzma
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "tools"))

import build_food_db as b  # noqa: E402
import helio_catalog as hc  # noqa: E402
import helio_science as sci  # noqa: E402

GENERIC = APP_DIR / "catalog" / "generic.tar"
ALIASES = APP_DIR / "catalog" / "aliases-it.tsv"


def vals(**kw):
    v = {k: None for k in hc.NUTRIENTS}
    v.update(kw)
    if v["kcal"] is None:
        v["kcal"] = sci.atwater_kcal(v)
    return hc.rounded(v)


def row(src, ref, name, name_it=None, brand=None, barcode=None, portions=None, countries=None, **v):
    return {"src": src, "ref": ref, "name": name, "name_it": name_it, "brand": brand, "barcode": hc.gtin_key(barcode),
            "countries": countries, "values": vals(**v), "portions": portions or []}


def sample_generic():
    return [row("ciqual", "13039", "Apple, flesh and skin, raw", "Mela", protein=0.25, carbs=11.6, fat=0.25, fiber=1.4,
                sat=0.05, salt=0.0, portions=[["1 mela media", 180]]),
            row("ciqual", "20053", "Eggplant, raw", "Melanzana", protein=1.0, carbs=2.7, fat=0.2, fiber=2.4),
            row("usda_sr", "175142", "Fish, sea bass, mixed species, raw", "Branzino", protein=18.4, carbs=0, fat=2.0,
                sat=0.5, salt=0.17),
            row("ciqual", "18071", "Espresso coffee", "Caffè espresso", protein=0.1, carbs=0, fat=0.2)]  # lang: it


def sample_off():
    return [row("off", "8001234567890", "Biscotti integrali", brand="Marca", barcode="8001234567890", protein=7.5,
                carbs=65, fat=18, fiber=8, sat=2.1, salt=0.5, portions=[["4 biscotti (30 g)", 30]]),
            row("off", "0012345678905", "Mela snack", barcode="0012345678905", protein=1, carbs=80, fat=1),
            row("off", "123", "Internal code", protein=5, carbs=5, fat=5),
            row("off", "20570910", "Bisfrolla con crema alle nocciole e cacao", brand="Confiserie Firenze",  # lang: it
                barcode="20570910", countries="cz", protein=7, carbs=58.6, fat=26.4, sat=9, salt=0.34),
            # the same store code used by two different products: both are kept
            row("off", "0000020570910", "Cioccolato fondente", brand="Altra", barcode="0000020570910", countries="~it",
                protein=8, carbs=40, fat=40)]


def build(tmp, rows, kind="generic", version="2026-10-21", name=None, sources=None):
    sources = sources or ([{"id": "usda_fnd", "version": "2026-04-30"}, {"id": "usda_sr", "version": "2018-04"},
                           {"id": "ciqual", "version": "2025-11-03"}] if kind == "generic" else [{"id": "off", "version": version}])
    out = Path(tmp) / (name or f"{kind}-{version}.tar")
    b.package(rows, kind, version, sources, out)
    return out


def repack(src, dest, manifest=None, xz=None, extra=None, drop=None):
    """Rewrites a package changing the manifest, the compressed database or the members."""
    with tarfile.open(src) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar}
    if manifest is not None:
        members["manifest.json"] = json.dumps(manifest).encode()
    if xz is not None:
        members["catalog.sqlite.xz"] = xz
    members.update(extra or {})
    for name in drop or ():
        members.pop(name)
    with tarfile.open(dest, "w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return dest


def rebuild_db(src, dest, sql):
    """A package whose database was changed by sql, with hashes and sizes recomputed (consistent but invalid)."""
    with tarfile.open(src) as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        raw = lzma.decompress(tar.extractfile("catalog.sqlite.xz").read())
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "c.sqlite"
        p.write_bytes(raw)
        with closing(sqlite3.connect(p)) as con:
            con.executescript(sql)
            con.commit()
        raw = p.read_bytes()
    xz = lzma.compress(raw)
    manifest.update(sqlite_size=len(raw), sqlite_sha256=hashlib.sha256(raw).hexdigest(), xz_size=len(xz),
                    xz_sha256=hashlib.sha256(xz).hexdigest())
    return repack(src, dest, manifest=manifest, xz=xz)


class Conversions(unittest.TestCase):
    def test_usda_carbohydrate_by_difference_loses_the_fibre(self):
        n = {1003: 0.26, 1004: 0.17, 1005: 13.81, 1079: 2.4, 1258: 0.028, 1093: 1.0}
        v = b.usda_values(n)
        self.assertAlmostEqual(v["carbs"], 11.41)
        self.assertEqual(v["fiber"], 2.4)
        self.assertEqual(v["salt"], 0.0)  # 1 mg sodium -> 0.0025 g salt, rounded
        self.assertAlmostEqual(v["kcal"], round(4 * 11.41 + 4 * 0.26 + 9 * 0.17 + 2 * 2.4, 2))  # EU factors

    def test_usda_salt_from_sodium_and_missing_values(self):
        v = b.usda_values({1003: 20.0, 1004: 5.0, 1005: 0.0, 1093: 400.0})
        self.assertEqual(v["salt"], 1.0)
        self.assertIsNone(v["fiber"])
        self.assertIsNone(v["sat"])
        self.assertIsNone(b.usda_values({1003: 20.0, 1005: 1.0}))  # no fat: not usable

    def test_usda_carbohydrate_by_summation_is_kept(self):
        self.assertEqual(b.usda_values({1003: 1.0, 1004: 1.0, 1050: 10.0, 1079: 3.0})["carbs"], 10.0)

    def test_ciqual_contents(self):
        self.assertEqual(b.ciqual_number(" 12,5 "), 12.5)
        self.assertEqual(b.ciqual_number("traces"), 0.0)
        self.assertEqual(b.ciqual_number("< 0,5"), 0.25)
        self.assertIsNone(b.ciqual_number("-"))
        self.assertIsNone(b.ciqual_number(" "))
        self.assertIsNone(b.ciqual_number(None))

    def test_ciqual_values(self):
        c = {"328": "52,9", "25000": "0,25", "31000": "11,6", "40000": "0,25", "34100": "1,4", "10110": "4",
             "34000": "< 0,5", "40302": "traces"}
        v = b.ciqual_values(c)
        self.assertEqual(v["kcal"], 52.9)  # declared energy (regulation 1169/2011) is kept
        self.assertEqual(v["salt"], 0.01)  # from sodium
        self.assertEqual(v["sat"], 0.0)
        self.assertEqual(v["polyols"], 0.25)
        no_carbs = b.ciqual_values({"25000": "2", "31000": "0", "40000": "0,4", "34000": "< 0,5"})
        self.assertEqual(no_carbs["polyols"], 0.0)  # half of "< 0.5" cannot exceed the carbohydrates
        self.assertAlmostEqual(no_carbs["kcal"], 4 * 2 + 9 * 0.4)  # computed when missing
        self.assertIsNone(b.ciqual_values({"25000": "2", "40000": "1"}))

    def test_off_values(self):
        r = {"energy-kcal_100g": "", "energy_100g": "1674", "proteins_100g": "7", "carbohydrates_100g": "60",
             "fat_100g": "20", "sodium_100g": "0.2", "alcohol_100g": "", "fiber_100g": "nan"}
        v = b.off_values(r)
        self.assertEqual(v["kcal"], 400.1)  # kJ / 4.184
        self.assertEqual(v["salt"], 0.5)
        self.assertIsNone(v["fiber"])
        beer = b.off_values({"energy-kcal_100g": "43", "proteins_100g": "0.5", "carbohydrates_100g": "3.5",
                             "fat_100g": "0", "alcohol_100g": "5"})
        self.assertAlmostEqual(beer["alcohol"], 3.95, places=1)  # 5% vol
        self.assertIsNone(b.off_values({"energy-kcal_100g": "100", "proteins_100g": "1", "fat_100g": "1"}))

    def test_off_portion(self):
        self.assertEqual(b.off_portion({"serving_size": "4 biscotti  (30 g)", "serving_quantity": "30"}),
                         [["4 biscotti (30 g)", 30.0]])
        self.assertEqual(b.off_portion({"serving_size": "", "serving_quantity": "30"}), [])
        self.assertEqual(b.off_portion({"serving_size": "1 kg", "serving_quantity": "5000"}), [])


class Values(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(hc.norm("  Caffè   ESPRESSO, già pronto! "), "caffe espresso gia pronto")  # lang: it
        self.assertEqual(hc.norm("Würstel_di-pollo"), "wurstel di pollo")
        self.assertEqual(hc.norm(None), "")

    def test_plausible(self):
        self.assertTrue(hc.plausible(vals(protein=10, carbs=50, fat=10, fiber=5)))
        self.assertFalse(hc.plausible(vals(protein=10, carbs=50, fat=10, kcal=1700)))  # kJ written as kcal
        self.assertFalse(hc.plausible(vals(protein=60, carbs=50, fat=10)))  # more than 100 g
        self.assertFalse(hc.plausible(vals(protein=10, carbs=50, fat=5, sat=6)))
        self.assertFalse(hc.plausible(vals(protein=10, carbs=5, fat=5, polyols=6)))
        self.assertFalse(hc.plausible(vals(protein=-1, carbs=5, fat=5)))
        self.assertTrue(hc.plausible(vals(protein=1, carbs=1, fat=1, kcal=80)))  # unknown fibre allows more energy
        self.assertFalse(hc.plausible({"protein": 1, "carbs": 1, "fat": 1}))

    def test_gtin_key(self):
        # EAN-8, its 13 and 14 digit forms
        self.assertEqual({hc.gtin_key(c) for c in ("20570910", "0000020570910", "00000020570910", " 2057 0910 ")},
                         {"00000020570910"})
        # UPC-A and the same code as EAN-13 and GTIN-14
        self.assertEqual({hc.gtin_key(c) for c in ("012345678905", "0012345678905", "00012345678905")}, {"00012345678905"})
        self.assertEqual(hc.gtin_key("8001234567890"), "08001234567890")
        self.assertEqual(hc.gtin_key("18001234567897"), "18001234567897")  # GTIN-14 with an indicator digit
        for bad in ("1234567", "123456789012345", "abc", "", None, "8001-234"):
            self.assertIsNone(hc.gtin_key(bad), bad)
        self.assertEqual(hc.gtin_display("00000020570910"), "20570910")
        self.assertEqual(hc.gtin_display("08001234567890"), "8001234567890")
        self.assertEqual(hc.gtin_display("18001234567897"), "18001234567897")

    def test_gs1_country(self):
        for code, country in (("8001234567890", "it"), ("3017620422003", "fr"), ("4008400402222", "de"),
                              ("5000159459228", "gb"), ("7613035974685", "ch"), ("5411188112709", "be"),
                              ("9001234567893", "at"), ("80012345", "it"), ("5901234123457", "pl")):
            self.assertEqual(hc.gs1_country(hc.gtin_key(code)), country, code)
        for code in ("0012345678905", "20570910", "2001234567893", "6901234567892", "4601234567893", "0412345678908"):
            self.assertIsNone(hc.gs1_country(hc.gtin_key(code)), code)  # UPC, store codes, China, Russia
        self.assertIsNone(hc.gs1_country(None))


class Package(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state"
        self.cat = self.tmp / "catalog"
        self.pkg = build(self.tmp, sample_generic())

    def tearDown(self):
        self._tmp.cleanup()

    def test_manifest_and_determinism(self):
        m = hc.read_manifest(self.pkg)
        self.assertEqual((m["format"], m["schema"], m["kind"], m["version"], m["rows"]),
                         (hc.FORMAT, 2, "generic", "2026-10-21", 4))
        again = build(self.tmp, list(reversed(sample_generic())), name="again.tar")
        self.assertEqual(self.pkg.read_bytes(), again.read_bytes())
        with tarfile.open(self.pkg) as tar:
            self.assertEqual(tar.getnames(), list(hc.MEMBERS))

    def test_install_and_info(self):
        m = hc.install(self.pkg, self.cat, self.state)
        self.assertEqual(sorted(p.name for p in self.cat.iterdir()), ["generic.json", "generic.sqlite"])
        self.assertFalse(self.state.exists())  # the user's data folder is not even created
        got = hc.info(self.cat)["generic"]
        self.assertEqual(got["sqlite_sha256"], m["sqlite_sha256"])
        self.assertGreater(got["file_size"], 0)
        self.assertEqual(hc.info(self.tmp / "nothing"), {})

    def assert_rejected(self, pkg, key):
        hc.install(self.pkg, self.cat, self.state)
        before = (self.cat / "generic.sqlite").read_bytes()
        with self.assertRaises(hc.CatalogError) as e:
            hc.install(pkg, self.cat, self.state)
        self.assertEqual(e.exception.key, key)
        self.assertEqual((self.cat / "generic.sqlite").read_bytes(), before)  # the previous catalog stays
        self.assertEqual(sorted(p.name for p in self.cat.iterdir()), ["generic.json", "generic.sqlite"])  # no temporary files left

    def test_wrong_xz_hash(self):
        m = hc.read_manifest(self.pkg)
        m["xz_sha256"] = "0" * 64
        self.assert_rejected(repack(self.pkg, self.tmp / "bad.tar", manifest=m), "cat.e.hash")

    def test_wrong_sqlite_hash(self):
        m = hc.read_manifest(self.pkg)
        m["sqlite_sha256"] = "0" * 64
        self.assert_rejected(repack(self.pkg, self.tmp / "bad.tar", manifest=m), "cat.e.hash")

    def test_truncated_package(self):
        bad = self.tmp / "bad.tar"
        with tarfile.open(self.pkg) as tar:
            xz = tar.getmember("catalog.sqlite.xz")
        bad.write_bytes(self.pkg.read_bytes()[:xz.offset_data + xz.size // 2])  # cut inside the database
        with self.assertRaises(hc.CatalogError) as e:
            hc.install(bad, self.cat, self.state)
        self.assertIn(e.exception.key, ("cat.e.archive", "cat.e.size", "cat.e.xz", "cat.e.hash"))
        self.assertFalse((self.cat / "generic.sqlite").exists())

    def test_not_a_tar(self):
        bad = self.tmp / "bad.tar"
        bad.write_bytes(b"not a package")
        self.assert_rejected(bad, "cat.e.archive")

    def test_extra_or_missing_member(self):
        self.assert_rejected(repack(self.pkg, self.tmp / "a.tar", extra={"../evil": b"x"}), "cat.e.member")
        self.assert_rejected(repack(self.pkg, self.tmp / "b.tar", drop=["catalog.sqlite.xz"]), "cat.e.member")

    def test_wrong_size(self):
        m = hc.read_manifest(self.pkg)
        m["sqlite_size"] -= 1
        self.assert_rejected(repack(self.pkg, self.tmp / "bad.tar", manifest=m), "cat.e.size")

    def test_corrupt_xz(self):
        with tarfile.open(self.pkg) as tar:
            xz = bytearray(tar.extractfile("catalog.sqlite.xz").read())
        xz[len(xz) // 2] ^= 0xFF
        m = hc.read_manifest(self.pkg)
        m["xz_sha256"] = hashlib.sha256(xz).hexdigest()
        self.assert_rejected(repack(self.pkg, self.tmp / "bad.tar", manifest=m, xz=bytes(xz)), "cat.e.xz")

    def test_other_schema_or_kind(self):
        m = hc.read_manifest(self.pkg)
        self.assert_rejected(repack(self.pkg, self.tmp / "a.tar", manifest={**m, "schema": 1}), "cat.e.schema")
        self.assert_rejected(repack(self.pkg, self.tmp / "b.tar", manifest={**m, "kind": "x"}), "cat.e.kind")
        self.assert_rejected(repack(self.pkg, self.tmp / "c.tar", manifest={**m, "format": "x"}), "cat.e.manifest")

    def test_kind_not_allowed(self):
        off = build(self.tmp, sample_off(), kind="off")
        with self.assertRaises(hc.CatalogError) as e:
            hc.install(off, self.cat, self.state, kinds=("generic",))
        self.assertEqual(e.exception.key, "cat.e.kind")

    def test_invalid_values(self):
        self.assert_rejected(rebuild_db(self.pkg, self.tmp / "a.tar", "UPDATE food SET protein = 150 WHERE id = 1"),
                             "cat.e.values")
        self.assert_rejected(rebuild_db(self.pkg, self.tmp / "b.tar", "UPDATE food SET src = 'off' WHERE id = 1"),
                             "cat.e.values")
        self.assert_rejected(rebuild_db(self.pkg, self.tmp / "c.tar", "UPDATE food SET portions = '[[1]]' WHERE id = 1"),
                             "cat.e.values")
        self.assert_rejected(rebuild_db(self.pkg, self.tmp / "d.tar", "UPDATE food SET kcal = 'x' WHERE id = 1"),
                             "cat.e.values")

    def test_wrong_rows_or_tables(self):
        self.assert_rejected(rebuild_db(self.pkg, self.tmp / "a.tar", "DELETE FROM food WHERE id = 1"), "cat.e.rows")
        self.assert_rejected(rebuild_db(self.pkg, self.tmp / "b.tar",
                                        "CREATE TRIGGER t AFTER INSERT ON food BEGIN SELECT 1; END"), "cat.e.db")
        self.assert_rejected(rebuild_db(self.pkg, self.tmp / "c.tar", "UPDATE meta SET value = '2020-01-01' "
                                        "WHERE key = 'version'"), "cat.e.db")

    def test_damaged_database(self):
        """A database that is not SQLite, with the right hashes."""
        raw = b"SQLite format 3\x00" + b"\x00" * 4000
        xz = lzma.compress(raw)
        m = {**hc.read_manifest(self.pkg), "sqlite_size": len(raw), "sqlite_sha256": hashlib.sha256(raw).hexdigest(),
             "xz_size": len(xz), "xz_sha256": hashlib.sha256(xz).hexdigest()}
        self.assert_rejected(repack(self.pkg, self.tmp / "bad.tar", manifest=m, xz=xz), "cat.e.db")

    def test_catalog_folder_must_be_outside_the_data(self):
        for cat in (self.state, self.state / "catalog"):
            with self.assertRaises(hc.CatalogError) as e:
                hc.install(self.pkg, cat, self.state)
            self.assertEqual(e.exception.key, "cat.e.dir")
        with self.assertRaises(hc.CatalogError):
            hc.install(self.pkg, self.tmp, self.state)  # the data folder inside the catalog folder
        self.assertFalse(self.state.exists())

    def test_remove(self):
        hc.install(self.pkg, self.cat, self.state)
        self.assertTrue(hc.remove("generic", self.cat, self.state))
        self.assertFalse(hc.remove("generic", self.cat, self.state))
        self.assertEqual(hc.info(self.cat), {})

    def test_bundled_installs_once_per_version(self):
        self.assertTrue(hc.ensure_bundled(self.pkg, self.cat, self.state))
        mtime = (self.cat / "generic.sqlite").stat().st_mtime_ns
        self.assertFalse(hc.ensure_bundled(self.pkg, self.cat, self.state))
        self.assertEqual((self.cat / "generic.sqlite").stat().st_mtime_ns, mtime)
        newer = build(self.tmp, sample_generic()[:3], version="2026-12-01")
        self.assertTrue(hc.ensure_bundled(newer, self.cat, self.state))
        self.assertEqual(hc.info(self.cat)["generic"]["version"], "2026-12-01")
        self.assertFalse(hc.ensure_bundled(self.tmp / "missing.tar", self.cat, self.state))
        off = build(self.tmp, sample_off(), kind="off")
        with self.assertRaises(hc.CatalogError):
            hc.ensure_bundled(off, self.cat, self.state)  # only the generic catalog ships with the app


class Search(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.cat = tmp / "catalog"
        hc.install(build(tmp, sample_generic()), cls.cat, tmp / "state")
        hc.install(build(tmp, sample_off(), kind="off"), cls.cat, tmp / "state")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def names(self, q, lang="it", cat=None):
        return [x["name"] for x in hc.search(cat or self.cat, q, lang)]

    def test_italian_names_first_then_generic_before_products(self):
        self.assertEqual(self.names("mela"), ["Mela", "Mela snack", "Melanzana"])  # whole word before the start of a word
        self.assertEqual(self.names("apple", "en"), ["Apple, flesh and skin, raw"])
        # the Italian names are searched in every language (one step lower in English); the shown name follows the language
        self.assertEqual(self.names("mela", "en"), ["Apple, flesh and skin, raw", "Mela snack", "Eggplant, raw"])
        self.assertEqual([x["name"] for x in hc.search(self.cat, "branzino", "en")], ["Fish, sea bass, mixed species, raw"])

    def test_accents_prefixes_and_brands(self):
        self.assertEqual(self.names("caffe esp"), ["Caffè espresso"])  # lang: it
        self.assertEqual(self.names("bisc int"), ["Biscotti integrali"])
        self.assertEqual(self.names("marca"), ["Biscotti integrali"])
        self.assertEqual(self.names("x"), [])
        self.assertEqual(self.names("   "), [])

    def test_item(self):
        it = hc.search(self.cat, "biscotti", "it")[0]
        self.assertEqual((it["cat"], it["src"], it["ref"], it["brand"], it["version"]),
                         ("off", "off", "8001234567890", "Marca", "2026-10-21"))
        self.assertEqual(it["portions"], [["4 biscotti (30 g)", 30]])
        self.assertEqual(it["missing"], [])
        sea = hc.get(self.cat, "usda_sr", "175142", "it")
        self.assertEqual(sea["name"], "Branzino")
        self.assertEqual(sea["missing"], ["fiber"])
        self.assertNotIn("fiber", sea["per100"])
        self.assertIsNone(hc.get(self.cat, "usda_sr", "0"))
        self.assertIsNone(hc.get(self.cat, "nope", "1"))

    def test_barcode_in_every_form(self):
        refs = lambda code: sorted(x["ref"] for x in hc.by_barcode(self.cat, code))
        self.assertEqual(refs("8001234567890"), ["8001234567890"])
        self.assertEqual(refs("08001234567890"), ["8001234567890"])           # GTIN-14 with the leading zero
        for upc in ("012345678905", "0012345678905", "00012345678905"):     # UPC-A <-> EAN-13 <-> GTIN-14
            self.assertEqual(refs(upc), ["0012345678905"], upc)
        # EAN-8 written with and without zeros: both products that use the code, nothing merged
        for ean8 in ("20570910", "0000020570910", "00000020570910"):
            self.assertEqual(refs(ean8), ["0000020570910", "20570910"], ean8)
        self.assertEqual(refs("123"), [])
        self.assertEqual(refs("4000000000000"), [])

    def test_barcode_item(self):
        (it,) = [x for x in hc.by_barcode(self.cat, "20570910") if x["ref"] == "20570910"]
        self.assertEqual((it["name"], it["brand"], it["barcode"], it["countries"], it["country_from_gs1"]),
                         ("Bisfrolla con crema alle nocciole e cacao", "Confiserie Firenze", "20570910", ["cz"], False))
        (other,) = [x for x in hc.by_barcode(self.cat, "20570910") if x["ref"] != "20570910"]
        self.assertEqual((other["countries"], other["country_from_gs1"]), (["it"], True))

    def test_search_needs_fts5_in_the_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = rebuild_db(build(tmp, sample_off(), kind="off"), Path(tmp) / "nofts.tar", "DROP TABLE food_fts")
            with self.assertRaises(hc.CatalogError) as e:
                hc.install(pkg, Path(tmp) / "cat", Path(tmp) / "state")
            self.assertEqual(e.exception.key, "cat.e.db")

    def test_files_are_opened_read_only(self):
        with hc._reading(self.cat, "generic") as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM food")


def parquet_row(code, countries=None, names=None, lang="it", brand="Marca", **n):
    """A product as it comes from the Open Food Facts Parquet export."""
    nutr = {"energy-kcal": 400.0, "proteins": 7.0, "carbohydrates": 60.0, "fat": 14.0, **n}
    return {"code": code, "countries_tags": countries or [], "lang": lang, "brands": brand,
            "product_name": [{"lang": k, "text": v} for k, v in (names if names is not None else {"main": "Biscotti"}).items()],
            "nutriments": [{"name": k, "100g": v} for k, v in nutr.items() if v is not None],
            "serving_quantity": "30", "serving_size": "30 g", "last_modified_t": 1700000000}


class OffRecords(unittest.TestCase):
    """Open Food Facts rows: European filter, names, values, merging of the same code."""

    def test_european_countries(self):
        r = b.off_record(parquet_row("20570910", ["en:czech-republic"]))
        self.assertEqual((r["barcode"], r["countries"]), ("00000020570910", ["cz"]))
        r = b.off_record(parquet_row("1234567890128", ["en:france", "en:united-states", "en:germany"]))
        self.assertEqual(r["countries"], ["de", "fr"])
        for tag in ("en:united-kingdom", "en:switzerland", "en:norway", "en:iceland", "en:liechtenstein", "en:malta"):
            self.assertIsNotNone(b.off_record(parquet_row("1234567890128", [tag])), tag)
        self.assertIsNone(b.off_record(parquet_row("1234567890128", ["en:united-states"])))
        self.assertIsNone(b.off_record(parquet_row("1234567890128", ["en:serbia", "en:turkey"])))

    def test_no_country_but_european_gs1_prefix(self):
        self.assertEqual(b.off_record(parquet_row("8001234567890"))["countries"], ["~it"])
        self.assertEqual(b.off_record(parquet_row("4008400402222"))["countries"], ["~de"])
        for code in ("0012345678905", "6901234567892", "20570910", "123"):
            self.assertIsNone(b.off_record(parquet_row(code)), code)  # UPC, China, store code without country

    def test_names_italian_then_english_then_original(self):
        r = b.off_record(parquet_row("8001234567890", names={"main": "Kekse", "de": "Kekse", "en": "Biscuits",
                                                              "it": "Biscotti"}, lang="de"))
        self.assertEqual((r["name_it"], r["name"]), ("Biscotti", "Biscuits"))
        self.assertEqual(r["aliases"], ["Kekse"])  # every other name is searched too
        r = b.off_record(parquet_row("8001234567890", names={"main": "Kekse", "de": "Kekse"}, lang="de"))
        self.assertEqual((r["name_it"], r["name"]), (None, "Kekse"))
        self.assertIsNone(b.off_record(parquet_row("8001234567890", names={})))

    def test_values_and_plausibility(self):
        r = b.off_record(parquet_row("8001234567890", fiber=0.0, salt=0.0))
        self.assertEqual((r["values"]["fiber"], r["values"]["salt"]), (0.0, 0.0))  # 0 is a value, not a missing one
        self.assertIsNone(b.off_record(parquet_row("8001234567890", **{"energy-kcal": 1700.0})))  # kJ as kcal
        self.assertIsNone(b.off_record(parquet_row("8001234567890", fat=None)))
        self.assertEqual(b.off_record(parquet_row("8001234567890"))["portions"], [["30 g", 30.0]])

    def rec(self, code, name, brand="Marca", **n):
        return b.off_record(parquet_row(code, ["en:italy"], {"main": name}, brand=brand, **n))

    def test_same_code_same_product_is_merged(self):
        a = self.rec("0012345678905", "Biscotti al cacao", fiber=3.0)
        c = self.rec("012345678905", "Biscotti cacao")
        (m,) = b.merge_same_code([a, c])
        self.assertEqual(m["ref"], "0012345678905")  # the most complete entry
        self.assertIn("Biscotti cacao", m["aliases"])  # the other name stays searchable

    def test_same_code_listed_twice(self):
        a, c = self.rec("8001234567890", "Biscotti", fiber=2.0), self.rec("8001234567890", "Altro prodotto", brand="X")
        self.assertEqual([r["ref"] for r in b.merge_same_code([a, c])], ["8001234567890"])

    def test_same_code_different_products_are_kept(self):
        a = self.rec("20570910", "Bisfrolla con crema alle nocciole", brand="Confiserie Firenze")  # lang: it
        c = self.rec("0000020570910", "Detersivo liquido", brand="Altra", **{"energy-kcal": 50.0, "carbohydrates": 5.0,
                                                                          "fat": 1.0, "proteins": 1.0})
        self.assertEqual(len(b.merge_same_code([a, c])), 2)
        d = self.rec("0000020570910", "Cioccolato", brand="", **{"energy-kcal": 550.0, "fat": 35.0, "proteins": 8.0,
                                                                "carbohydrates": 50.0})
        self.assertEqual(len(b.merge_same_code([a, d])), 2)  # different names and values

    def test_order_italian_names_first(self):
        rows = [self.rec("8001234567890", "Kekse"), self.rec("8001234567891", "Biscotti")]
        rows[1]["name_it"] = "Biscotti"
        self.assertEqual([r["ref"] for r in sorted(rows, key=b.off_order)], ["8001234567891", "8001234567890"])


class CommandLine(unittest.TestCase):
    """helio_catalog.py on the server: install, info and remove, with the same folder rules as the app."""

    def test_install_info_remove(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            pkg = build(tmp, sample_off(), kind="off")
            run = lambda *a: subprocess.run([sys.executable, str(APP_DIR / "helio_catalog.py"), "--state", f"{tmp}/state",
                                             "--catalog", f"{tmp}/state-catalog", *a], capture_output=True, text=True)
            r = run("install", str(pkg))
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "installed off 2026-10-21: 5 foods"))
            self.assertEqual(set(json.loads(run("info").stdout)), {"off"})
            self.assertEqual(run("remove").stdout.strip(), "removed")
            self.assertFalse(Path(f"{tmp}/state").exists())
            bad = subprocess.run([sys.executable, str(APP_DIR / "helio_catalog.py"), "--state", f"{tmp}/state",
                                  "--catalog", f"{tmp}/state/cat", "install", str(pkg)], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("cat.e.dir", bad.stderr)


class ShippedCatalog(unittest.TestCase):
    """The generic catalog in catalog/generic.tar and its Italian names."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cat = Path(cls._tmp.name) / "catalog"
        cls.manifest = hc.install(GENERIC, cls.cat, Path(cls._tmp.name) / "state")
        cls.aliases = b.load_aliases(ALIASES)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_manifest(self):
        m = self.manifest
        self.assertEqual(m["kind"], "generic")
        self.assertEqual({s["id"] for s in m["sources"]}, {"usda_fnd", "usda_sr", "ciqual"})
        self.assertGreater(m["rows"], 10000)
        self.assertLess(GENERIC.stat().st_size, 2 * 1024 * 1024)

    def test_about_300_common_foods(self):
        self.assertGreaterEqual(len(self.aliases), 300)
        names = [a["name_it"].lower() for a in self.aliases.values()]
        self.assertEqual(len(names), len(set(names)))

    def test_every_alias_is_in_the_catalog_with_its_name(self):
        for (src, ref), a in self.aliases.items():
            it = hc.get(self.cat, src, ref, "it")
            self.assertIsNotNone(it, f"{src} {ref}")
            self.assertEqual(it["name"], a["name_it"])

    def test_every_italian_name_finds_its_food_first(self):
        for (src, ref), a in self.aliases.items():
            first = hc.search(self.cat, a["name_it"], "it", 3)[0]
            self.assertEqual((first["src"], first["ref"]), (src, ref), a["name_it"])

    def test_aliases_find_their_food(self):
        for (src, ref), a in self.aliases.items():
            for other in a["aliases"]:
                found = [(x["src"], x["ref"]) for x in hc.search(self.cat, other, "it", 5)]
                self.assertIn((src, ref), found, f"{other} -> {a['name_it']}")

    def test_italian_portions(self):
        apple = hc.search(self.cat, "mela", "it", 1)[0]
        self.assertEqual(apple["portions"][0], ["1 mela media", 180.0])  # lang: it

    def test_load_aliases_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.tsv"
            p.write_text("ciqual\t1\tA\t\t\nciqual\t1\tB\t\t\n")
            with self.assertRaises(ValueError):
                b.load_aliases(p)


if __name__ == "__main__":
    unittest.main()
