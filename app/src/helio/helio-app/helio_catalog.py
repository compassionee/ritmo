"""Food catalog: community databases (USDA FoodData Central, ANSES Ciqual, Open Food Facts) for the search.

Each catalog is one SQLite file in its own folder, next to the user's data folder and never inside it:
installing, updating or removing a catalog does not touch the user's foods, recipes, saved meals or
diary, and the export of the user's data does not see it. Each file is opened once, read only (immutable: it is
never changed in place, only replaced), and the connection is kept for every search; a replaced or removed file
is closed.

A catalog travels as a package (.tar) with two members:
  manifest.json      format, schema, kind, version, sources, rows and the SHA-256 of both files
  catalog.sqlite.xz  the database (tables meta and food), compressed with xz
The full-text index (FTS5) is built with the database, on the computer: installing only checks (sizes,
both hashes, structure, rows and values) and replaces the installed file with an atomic rename; if
anything fails, the previous catalog stays.

This module never uses the network, and neither does the rest of the app: packages are installed only from a file.
"""
import functools
import hashlib
import io
import json
import lzma
import os
import re
import secrets
import sqlite3
import tarfile
import threading
import unicodedata
from contextlib import closing, contextmanager
from pathlib import Path

import helio_science as sci

FORMAT = "ritmo-food-catalog"
SCHEMA = 2  # 2: countries column, FTS5 index shipped in the package, barcodes as GTIN-14
KINDS = ("generic", "off")  # generic: USDA + Ciqual, shipped with the app; off: Open Food Facts, imported
SOURCES = {"generic": ("usda_fnd", "usda_sr", "ciqual"), "off": ("off",)}
NUTRIENTS = ("kcal", "protein", "carbs", "fat", "fiber", "sat", "salt", "polyols", "alcohol")
REQUIRED = ("kcal", "protein", "carbs", "fat")
OPTIONAL = ("fiber", "sat", "salt", "polyols", "alcohol")
SHOWN_OPTIONAL = ("fiber", "sat", "salt")  # missing values the analysis reports ("at least X g")
MEMBERS = ("manifest.json", "catalog.sqlite.xz")
# where each source comes from and its licence (names and links only: the texts are in the catalogs)
SOURCE_INFO = {
    "usda_fnd": {"short": "USDA", "title": "USDA FoodData Central, Foundation Foods",
                 "org": "U.S. Department of Agriculture, Agricultural Research Service", "url": "https://fdc.nal.usda.gov/",
                 "license": "CC0 1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/"},
    "usda_sr": {"short": "USDA", "title": "USDA FoodData Central, SR Legacy",
                "org": "U.S. Department of Agriculture, Agricultural Research Service", "url": "https://fdc.nal.usda.gov/",
                "license": "CC0 1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/"},
    "ciqual": {"short": "Ciqual", "title": "ANSES-CIQUAL French food composition table", "org": "Anses",
               "url": "https://ciqual.anses.fr/",
               "license": "Licence Ouverte / Etalab 2.0", "license_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"},
    "off": {"short": "OFF", "title": "Open Food Facts", "org": "Open Food Facts contributors",
            "url": "https://world.openfoodfacts.org/",
            "license": "ODbL 1.0", "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
            "contents_license": "DbCL 1.0", "contents_license_url": "https://opendatacommons.org/licenses/dbcl/1-0/"},
}
MAX_MANIFEST = 64 * 1024
MAX_XZ = 768 * 1024 * 1024
MAX_DB = 2 * 1024 * 1024 * 1024
PORTIONS_MAX = 6
COLUMNS = ("id", "src", "ref", "name", "name_it", "aliases", "brand", "barcode", "countries") + NUTRIENTS + ("portions",)
# European countries of the community catalog (EU 27, Norway, Iceland, Liechtenstein, Switzerland, United
# Kingdom): ISO codes in the countries column; "~xx" when the country comes only from the GS1 prefix
EUROPE = ("at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de", "gr", "hu", "ie", "it", "lv", "lt", "lu",
          "mt", "nl", "pl", "pt", "ro", "sk", "si", "es", "se", "no", "is", "li", "ch", "gb")

SCHEMA_SQL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE food(
  id INTEGER PRIMARY KEY, src TEXT NOT NULL, ref TEXT NOT NULL, name TEXT NOT NULL, name_it TEXT, aliases TEXT,
  brand TEXT, barcode TEXT, countries TEXT, kcal REAL NOT NULL, protein REAL NOT NULL, carbs REAL NOT NULL,
  fat REAL NOT NULL, fiber REAL, sat REAL, salt REAL, polyols REAL, alcohol REAL, portions TEXT, UNIQUE(src, ref));
CREATE INDEX food_barcode ON food(barcode) WHERE barcode IS NOT NULL;
"""
# search index without a copy of the text (contentless): Italian name, main name, other names and brand
FTS_SQL = ("CREATE VIRTUAL TABLE food_fts USING fts5(it, main, other, content='', "
           "tokenize='unicode61 remove_diacritics 2', prefix='2 3')")
FTS_OBJECTS = {("table", "food_fts"), ("table", "food_fts_data"), ("table", "food_fts_idx"),
               ("table", "food_fts_docsize"), ("table", "food_fts_config")}

# rows that break a rule: no catalog with even one of them is installed
INVALID_SQL = """SELECT count(*) FROM food WHERE src NOT IN ({srcs}) OR length(ref) NOT BETWEEN 1 AND 40
  OR length(name) NOT BETWEEN 1 AND 200 OR length(coalesce(name_it, '')) > 200 OR length(coalesce(aliases, '')) > 1000
  OR length(coalesce(brand, '')) > 100 OR length(coalesce(countries, '')) > 120
  OR (barcode IS NOT NULL AND (barcode GLOB '*[^0-9]*' OR length(barcode) != 14))
  OR typeof(kcal) NOT IN ('real', 'integer') OR kcal NOT BETWEEN 0 AND 900
  OR {nutrients}
  OR protein + carbs + fat + coalesce(fiber, 0) + coalesce(alcohol, 0) > 101
  OR coalesce(sat, 0) > fat + 0.1 OR coalesce(polyols, 0) > carbs + 0.1
  OR length(coalesce(portions, '')) > 1000"""


class CatalogError(Exception):
    """Rejected package or failed operation: key of the message (catalog "cat.e.*") and its parameters."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.key, self.params = key, params


# ---------------------------------------------------------------- values and text
_ASCII_WORD = re.compile(r"[a-z0-9]+")


@functools.lru_cache(maxsize=8192)
def _norm(text):
    if text.isascii():  # most names: no accents to remove (fast path, same result)
        return " ".join(_ASCII_WORD.findall(text.lower()))
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text))


def norm(text):
    """Search form of a text: lower case, no accents, only letters and digits separated by one space."""
    return _norm(str(text or ""))


def plausible(v):
    """True if the values per 100 g respect the same rules as the foods entered by hand, plus an energy check:
    the declared kcal must be near the kcal computed with the EU factors (catches kJ written as kcal)."""
    if any(not isinstance(v.get(k), (int, float)) or isinstance(v.get(k), bool) for k in REQUIRED):
        return False
    for k in NUTRIENTS:
        x = v.get(k)
        if x is None:
            continue
        if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or not 0 <= x <= (900 if k == "kcal" else 100):
            return False
    if v["protein"] + v["carbs"] + v["fat"] + (v.get("fiber") or 0) + (v.get("alcohol") or 0) > 101:
        return False
    if (v.get("sat") or 0) > v["fat"] + 0.1 or (v.get("polyols") or 0) > v["carbs"] + 0.1:
        return False
    computed = sci.atwater_kcal(v)
    # unknown fibre: up to 2 kcal per gram of what is not protein, carbohydrate or fat (at most 30 g)
    extra = 2 * min(30.0, max(0.0, 100 - v["protein"] - v["carbs"] - v["fat"])) if v.get("fiber") is None else 0.0
    return 0.75 * computed - 30 <= v["kcal"] <= 1.25 * (computed + extra) + 30


def rounded(v):
    return {k: (None if v.get(k) is None else round(float(v[k]), 2)) for k in NUTRIENTS}


def clean_barcode(code):
    """Digits of a barcode (8 to 14, spaces ignored), otherwise None."""
    code = re.sub(r"\s", "", str(code or ""))
    return code if re.fullmatch(r"\d{8,14}", code) else None


def gtin_key(code):
    """The same product code in every form, as GTIN-14: EAN-8, UPC-A (12), EAN-13 and GTIN-14 with or without
    the leading zeros all give the same 14 digits ("20570910" = "0000020570910" = "00000020570910"). None if
    the code is not 8 to 14 digits."""
    code = clean_barcode(code)
    return code.zfill(14) if code else None


def gtin_display(key):
    """Usual printed form of a GTIN-14 key: EAN-8, EAN-13 or GTIN-14."""
    if key.startswith("000000"):
        return key[6:]
    return key[1:] if key.startswith("0") else key


# GS1 prefixes of the European member organisations (first three digits of an EAN-13 or EAN-8)
GS1_EUROPE = {**{p: "fr" for p in range(300, 380)}, 380: "bg", 383: "si", 385: "hr", **{p: "de" for p in range(400, 441)},
              474: "ee", 475: "lv", 477: "lt", **{p: "gb" for p in range(500, 510)}, 520: "gr", 521: "gr", 529: "cy",
              535: "mt", 539: "ie", **{p: "be" for p in range(540, 550)}, 560: "pt", 569: "is",
              **{p: "dk" for p in range(570, 580)}, 590: "pl", 594: "ro", 599: "hu", **{p: "fi" for p in range(640, 650)},
              **{p: "no" for p in range(700, 710)}, **{p: "se" for p in range(730, 740)},
              **{p: "ch" for p in range(760, 770)}, **{p: "it" for p in range(800, 840)},
              **{p: "es" for p in range(840, 850)}, 858: "sk", 859: "cz", **{p: "nl" for p in range(870, 880)},
              **{p: "at" for p in range(900, 920)}}


def gs1_country(key):
    """European country of the GS1 prefix of a GTIN-14 key, or None (other countries, UPC codes, store codes
    20-29 and 02/04, which have no country)."""
    if not key:
        return None
    digits = key[6:] if key.startswith("000000") else key[1:]
    return GS1_EUROPE.get(int(digits[:3]))


# ---------------------------------------------------------------- package
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _tar_add(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), 0, 0o644
    tar.addfile(info, io.BytesIO(data))


def write_package(db_path, out_path):
    """Packs a catalog database built with SCHEMA_SQL (its meta table gives kind, version and sources).
    Deterministic: the same database gives the same package. Returns the manifest."""
    db_path = Path(db_path)
    with closing(sqlite3.connect(db_path)) as con:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        rows = con.execute("SELECT count(*) FROM food").fetchone()[0]
    raw = db_path.read_bytes()
    # the strongest preset for small catalogs; for large ones preset 6 (much faster to build, same decompression)
    preset = 9 | lzma.PRESET_EXTREME if len(raw) < 64 * 1024 * 1024 else 6
    xz = lzma.compress(raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=preset)
    manifest = {"format": FORMAT, "schema": SCHEMA, "kind": meta["kind"], "version": meta["version"],
                "sources": json.loads(meta["sources"]), "rows": rows,
                "sqlite_size": len(raw), "sqlite_sha256": hashlib.sha256(raw).hexdigest(),
                "xz_size": len(xz), "xz_sha256": hashlib.sha256(xz).hexdigest()}
    out_path = Path(out_path)
    tmp = out_path.with_name(out_path.name + ".tmp")
    with tarfile.open(tmp, "w", format=tarfile.USTAR_FORMAT) as tar:
        _tar_add(tar, "manifest.json", json.dumps(manifest, indent=1, sort_keys=True).encode() + b"\n")
        _tar_add(tar, "catalog.sqlite.xz", xz)
    os.replace(tmp, out_path)
    return manifest


def check_manifest(m):
    """The manifest fields, with their types; raises CatalogError."""
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise CatalogError("cat.e.manifest")
    if m.get("schema") != SCHEMA:
        raise CatalogError("cat.e.schema", schema=m.get("schema"))
    if m.get("kind") not in KINDS:
        raise CatalogError("cat.e.kind")
    if not isinstance(m.get("version"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", m["version"]):
        raise CatalogError("cat.e.manifest")
    for k, hi in (("rows", 20_000_000), ("sqlite_size", MAX_DB), ("xz_size", MAX_XZ)):
        if isinstance(m.get(k), bool) or not isinstance(m.get(k), int) or not 0 < m[k] <= hi:
            raise CatalogError("cat.e.size" if k != "rows" else "cat.e.manifest")
    for k in ("sqlite_sha256", "xz_sha256"):
        if not isinstance(m.get(k), str) or not re.fullmatch(r"[0-9a-f]{64}", m[k]):
            raise CatalogError("cat.e.manifest")
    src = m.get("sources")
    if (not isinstance(src, list) or not src
            or any(not isinstance(s, dict) or s.get("id") not in SOURCES[m["kind"]] for s in src)):
        raise CatalogError("cat.e.manifest")
    return m


def _members(tar):
    """The two members, regular files with the expected names and nothing else."""
    found = {}
    for info in tar:
        if info.name not in MEMBERS or not info.isreg() or info.name in found:
            raise CatalogError("cat.e.member", name=info.name[:60])
        found[info.name] = info
    if set(found) != set(MEMBERS):
        raise CatalogError("cat.e.member", name=", ".join(sorted(set(MEMBERS) - set(found))))
    return found


def read_manifest(package):
    """Manifest of a package (path), checked."""
    try:
        with tarfile.open(package, "r:") as tar:
            info = _members(tar)["manifest.json"]
            if info.size > MAX_MANIFEST:
                raise CatalogError("cat.e.size")
            data = tar.extractfile(info).read()
    except (tarfile.TarError, OSError, EOFError):
        raise CatalogError("cat.e.archive")
    try:
        return check_manifest(json.loads(data))
    except ValueError:
        raise CatalogError("cat.e.manifest")


def _unpack_db(package, manifest, dest):
    """Decompresses the database into dest checking sizes and both hashes."""
    try:
        with tarfile.open(package, "r:") as tar:
            info = _members(tar)["catalog.sqlite.xz"]
            if info.size != manifest["xz_size"]:
                raise CatalogError("cat.e.size")
            src = tar.extractfile(info)
            hx, hd, dec = hashlib.sha256(), hashlib.sha256(), lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
            total = 0
            with open(dest, "wb") as out:
                def write(data):
                    nonlocal total
                    total += len(data)
                    if total > manifest["sqlite_size"]:
                        raise CatalogError("cat.e.size")
                    hd.update(data)
                    out.write(data)
                for block in iter(lambda: src.read(1 << 20), b""):
                    hx.update(block)
                    if dec.eof:
                        raise CatalogError("cat.e.xz")
                    write(dec.decompress(block, max_length=1 << 22))
                    while not dec.eof and not dec.needs_input:
                        write(dec.decompress(b"", max_length=1 << 22))
                out.flush()
                os.fsync(out.fileno())
    except (tarfile.TarError, OSError, EOFError):
        raise CatalogError("cat.e.archive")
    except lzma.LZMAError:
        raise CatalogError("cat.e.xz")
    if hx.hexdigest() != manifest["xz_sha256"]:
        raise CatalogError("cat.e.hash")
    if not dec.eof or dec.unused_data or total != manifest["sqlite_size"]:
        raise CatalogError("cat.e.xz")
    if hd.hexdigest() != manifest["sqlite_sha256"]:
        raise CatalogError("cat.e.hash")


def check_db(path, manifest, full=False):
    """Structure, rows and values of a catalog database; raises CatalogError. The SHA-256 of the whole file
    is checked before: it is the database the builder made and checked with full=True (SQLite integrity and
    FTS5 index), so the phone does not repeat those long checks."""
    try:
        # the FTS5 integrity-check is an INSERT command: the full check (on the computer) opens the file writable
        mode = "rw" if full else "ro"
        with closing(sqlite3.connect(f"file:{Path(path).as_posix()}?mode={mode}", uri=True)) as con:
            if full and con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise CatalogError("cat.e.db")
            objects = {(t, n) for t, n in con.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
            if objects != {("table", "meta"), ("table", "food"), ("index", "food_barcode")} | FTS_OBJECTS:
                raise CatalogError("cat.e.db")
            if tuple(r[1] for r in con.execute("PRAGMA table_info(food)")) != COLUMNS:
                raise CatalogError("cat.e.db")
            meta = dict(con.execute("SELECT key, value FROM meta"))
            if (meta.get("format") != FORMAT or meta.get("schema") != str(SCHEMA) or meta.get("kind") != manifest["kind"]
                    or meta.get("version") != manifest["version"]):
                raise CatalogError("cat.e.db")
            if con.execute("SELECT max(id) FROM food").fetchone()[0] != manifest["rows"] or \
                    con.execute("SELECT count(*) FROM food").fetchone()[0] != manifest["rows"]:
                raise CatalogError("cat.e.rows")
            try:  # FTS5 must be available here, and the index must answer
                con.execute("SELECT rowid FROM food_fts WHERE food_fts MATCH 'a*' LIMIT 1").fetchall()
            except sqlite3.OperationalError:
                raise CatalogError("cat.e.fts")
            if full:
                con.execute("INSERT INTO food_fts(food_fts, rank) VALUES ('integrity-check', 0)")
            srcs = ", ".join(f"'{s}'" for s in SOURCES[manifest["kind"]])
            nutrients = " OR ".join(
                f"({k} IS NOT NULL AND (typeof({k}) NOT IN ('real', 'integer') OR {k} NOT BETWEEN 0 AND 100))"
                for k in NUTRIENTS if k != "kcal")
            if con.execute(INVALID_SQL.format(srcs=srcs, nutrients=nutrients)).fetchone()[0]:
                raise CatalogError("cat.e.values")
            for (p,) in con.execute("SELECT portions FROM food WHERE portions IS NOT NULL"):
                if parse_portions(p) is None:
                    raise CatalogError("cat.e.values")
    except sqlite3.DatabaseError:
        raise CatalogError("cat.e.db")


def build_fts(con):
    """Fills the search index from the food table (on the computer, by the builder and the tests)."""
    con.execute(FTS_SQL)
    con.execute("INSERT INTO food_fts(rowid, it, main, other) SELECT id, coalesce(name_it, ''), name, "
                "trim(coalesce(aliases, '') || ' ' || coalesce(brand, '')) FROM food")
    con.execute("INSERT INTO food_fts(food_fts) VALUES ('optimize')")


def parse_portions(text):
    """[[name, grams], ...] from the JSON of a row, or None if it is not valid."""
    try:
        p = json.loads(text)
    except ValueError:
        return None
    if not isinstance(p, list) or len(p) > PORTIONS_MAX:
        return None
    for x in p:
        if (not isinstance(x, list) or len(x) != 2 or not isinstance(x[0], str) or not 0 < len(x[0]) <= 40
                or isinstance(x[1], bool) or not isinstance(x[1], (int, float)) or not 0.1 <= x[1] <= 2000):
            return None
    return p


# ---------------------------------------------------------------- installation
def check_dirs(catalog_dir, state_dir):
    """The catalog folder must be separate from the user's data folder (neither inside the other)."""
    c, s = Path(catalog_dir).resolve(), Path(state_dir).resolve() if state_dir else None
    if s is not None and (c == s or s in c.parents or c in s.parents):
        raise CatalogError("cat.e.dir")
    return c


def installed_path(catalog_dir, kind):
    return Path(catalog_dir) / f"{kind}.sqlite"


def install(package, catalog_dir, state_dir, kinds=KINDS):
    """Checks a package and installs it as the catalog of its kind. Returns the manifest.

    Only files in catalog_dir are created or replaced; state_dir is only used to refuse a catalog folder
    inside the user's data (or the other way round)."""
    cdir = check_dirs(catalog_dir, state_dir)
    manifest = read_manifest(package)
    if manifest["kind"] not in kinds:
        raise CatalogError("cat.e.kind")
    cdir.mkdir(parents=True, exist_ok=True)
    tmp = cdir / f".install-{secrets.token_hex(6)}.sqlite"
    try:
        _unpack_db(package, manifest, tmp)
        check_db(tmp, manifest)
        installed = cdir / f".installed-{secrets.token_hex(6)}.json"
        installed.write_text(json.dumps(manifest, sort_keys=True))
        # the database stays exactly as built (its hash is the manifest's); the manifest goes next to it
        os.replace(tmp, installed_path(cdir, manifest["kind"]))
        os.replace(installed, cdir / f"{manifest['kind']}.json")
        _fsync_dir(cdir)
        _forget(installed_path(cdir, manifest["kind"]))  # the old file: its connection is closed
    finally:
        for p in (tmp, Path(str(tmp) + "-journal")):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    return manifest


def remove(kind, catalog_dir, state_dir):
    """Deletes an installed catalog (only its file in catalog_dir)."""
    cdir = check_dirs(catalog_dir, state_dir)
    if kind not in KINDS:
        raise CatalogError("cat.e.kind")
    _forget(installed_path(cdir, kind))  # closed first: an open file would keep its space after the removal
    try:
        installed_path(cdir, kind).unlink()
    except FileNotFoundError:
        return False
    try:
        (cdir / f"{kind}.json").unlink()
    except FileNotFoundError:
        pass
    _fsync_dir(cdir)
    return True


def _fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# one read-only connection per installed file, opened on first use and kept: {path: (file identity, connection, lock)}
_conns = {}
_conns_lock = threading.Lock()


def _forget(path):
    """Closes the connection of a file that is being replaced or removed."""
    with _conns_lock:
        old = _conns.pop(str(path), None)
    if old:
        with old[2]:
            old[1].close()


@contextmanager
def _reading(catalog_dir, kind):
    """The catalog's connection (None if not installed), for one query at a time. Opened once, read only and immutable
    (the file is only ever replaced by a rename, never written); a new file (import, update) gets a new one."""
    path = installed_path(catalog_dir, kind)
    if not _installed_manifest(catalog_dir, kind):
        _forget(path)
        yield None
        return
    try:
        st = path.stat()
    except OSError:
        yield None
        return
    ident = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    with _conns_lock:
        have = _conns.get(str(path))
        if have is None or have[0] != ident:
            try:
                con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True, check_same_thread=False)
            except sqlite3.Error:
                con = None
            stale, have = have, (ident, con, threading.Lock())
            if con is None:
                have = None
            else:
                _conns[str(path)] = have
        else:
            stale = None
    if stale:
        with stale[2]:
            stale[1].close()
    if have is None:
        yield None
        return
    with have[2]:
        yield have[1]


def close_all():
    """Closes every catalog connection (end of the tests, folder removed)."""
    with _conns_lock:
        paths = list(_conns)
    for p in paths:
        _forget(p)


def _installed_manifest(catalog_dir, kind):
    """Manifest of an installed catalog of the current schema, or None (missing, damaged, or installed by an
    older version of the app: it has to be imported again)."""
    path = installed_path(catalog_dir, kind)
    try:
        m = json.loads((Path(catalog_dir) / f"{kind}.json").read_text())
        if not isinstance(m, dict) or m.get("schema") != SCHEMA or m.get("kind") != kind or not path.is_file():
            return None
        m["file_size"] = path.stat().st_size
        return m
    except (OSError, ValueError):
        return None


def info(catalog_dir):
    """Installed catalogs: {kind: manifest} (kinds not installed, or of an older schema, are missing)."""
    out = {}
    for kind in KINDS:
        m = _installed_manifest(catalog_dir, kind)
        if m:
            out[kind] = m
    return out


def outdated(catalog_dir):
    """Kinds with a file installed by an older version of the app (other schema): to import again."""
    return [k for k in KINDS if installed_path(catalog_dir, k).is_file() and not _installed_manifest(catalog_dir, k)]


def ensure_bundled(package, catalog_dir, state_dir):
    """Installs the catalog shipped with the app if missing or of another version. Returns True if installed now."""
    if not package or not Path(package).is_file():
        return False
    try:
        m = read_manifest(package)
    except CatalogError:
        return False
    have = info(catalog_dir).get(m["kind"])
    if have and have.get("version") == m["version"] and have.get("sqlite_sha256") == m["sqlite_sha256"]:
        return False
    install(package, catalog_dir, state_dir, kinds=("generic",))
    return True


# ---------------------------------------------------------------- search
def _item(kind, row, lang, version):
    r = dict(zip(COLUMNS, row))
    v = {k: r[k] for k in NUTRIENTS if r[k] is not None}
    name = r["name_it"] if lang == "it" and r["name_it"] else r["name"]
    countries = [c for c in (r["countries"] or "").split(",") if c]
    return {"cat": kind, "src": r["src"], "ref": r["ref"], "name": name, "name_orig": r["name"],
            "name_it": r["name_it"], "aliases": [a.strip() for a in (r["aliases"] or "").split(",") if a.strip()],
            "brand": r["brand"] or None, "barcode": gtin_display(r["barcode"]) if r["barcode"] else None,
            "countries": [c.lstrip("~") for c in countries], "country_from_gs1": any(c.startswith("~") for c in countries),
            "per100": v, "missing": [k for k in SHOWN_OPTIONAL if r[k] is None],
            "portions": parse_portions(r["portions"]) or [] if r["portions"] else [], "version": version}


def _name_score(name, q, tokens):
    """0 same text, 1 starts with the whole words searched, 2 starts with the text, 3 every word searched is a
    whole word, 4 every word is the start of a word, 5 found elsewhere (brand, inside a word)."""
    n = norm(name)
    words = n.split()
    if n == q:
        return 0
    if n.startswith(q + " "):
        return 1
    if n.startswith(q):
        return 2
    if all(t in words for t in tokens):
        return 3
    if all(any(w.startswith(t) for w in words) for t in tokens):
        return 4
    return 5


def _rank(item, q, tokens, lang):
    """Sort key: best match among the names (in Italian, a match on an English name counts one step less:
    "pollo" finds "Petto di pollo" before "Pollock"; the other names count one step less than the shown one),
    then generic foods, then foods with an Italian name, then shorter names."""
    it = lang == "it"
    scores = [_name_score(item["name"], q, tokens),
              _name_score(item["name_orig"], q, tokens) + (1 if it and item["src"] != "off" else 0)]
    if item["name_it"]:
        scores.append(_name_score(item["name_it"], q, tokens) + (0 if it else 1))
    scores += [_name_score(x, q, tokens) + 1 for x in item["aliases"][:4]]
    return (min(scores), 0 if item["cat"] == "generic" else 1, 0 if item["name_it"] or not it else 1,
            len(item["name"]), item["name"])


def _phrase(t):
    return '"' + t.replace('"', "") + '"*'


SEARCH_FIRST = 100   # names starting with the first word (they rank first)
SEARCH_OTHERS = 100  # every word anywhere: the first ones in the file (the builder puts the most complete first)


def search(catalog_dir, q, lang="en", limit=30):
    """Community foods matching q (all words, each as the start of a word): best matches first, then the
    generic foods before the products. Read only, local files only. Two bounded lookups in the FTS5 index
    (names that start with the first word, then any match), so a short or common word stays fast also in
    a catalog of millions of products."""
    q = norm(q)[:80]
    tokens = q.split()[:6]
    if not tokens or len(q) < 2:
        return []
    first = "{it main} : ^" + _phrase(tokens[0]) + "".join(" AND " + _phrase(t) for t in tokens[1:])
    anywhere = " AND ".join(_phrase(t) for t in tokens)
    cols = ", ".join("f." + c for c in COLUMNS)
    found = []
    for kind in KINDS:
        with _reading(catalog_dir, kind) as con:
            if con is None:
                continue
            try:
                version = (con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone() or [None])[0]
                ids = [r[0] for r in con.execute("SELECT rowid FROM food_fts WHERE food_fts MATCH ? LIMIT ?",
                                                 (first, SEARCH_FIRST))]
                seen = set(ids)
                ids += [r[0] for r in con.execute("SELECT rowid FROM food_fts WHERE food_fts MATCH ? LIMIT ?",
                                                  (anywhere, SEARCH_OTHERS + len(ids))) if r[0] not in seen]
                rows = con.execute(f"SELECT {cols} FROM food f WHERE f.id IN ({', '.join('?' * len(ids))})",
                                   ids).fetchall() if ids else []
            except sqlite3.Error:
                continue
        found += [_item(kind, r, lang, version) for r in rows]
    found.sort(key=lambda it: _rank(it, q, tokens, lang))
    return found[:limit]


def get(catalog_dir, src, ref, lang="en"):
    """One community food by source and id, or None."""
    for kind in KINDS:
        if src not in SOURCES[kind]:
            continue
        with _reading(catalog_dir, kind) as con:
            if con is None:
                return None
            try:
                version = (con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone() or [None])[0]
                row = con.execute(f"SELECT {', '.join(COLUMNS)} FROM food WHERE src = ? AND ref = ?",
                                  (src, str(ref))).fetchone()
            except sqlite3.Error:
                return None
        return _item(kind, row, lang, version) if row else None
    return None


def by_barcode(catalog_dir, code, lang="en"):
    """Community products with this barcode, in any of its forms (see gtin_key): all of them when the same code
    belongs to different products."""
    key = gtin_key(code)
    out = []
    if not key:
        return out
    for kind in KINDS:
        with _reading(catalog_dir, kind) as con:
            if con is None:
                continue
            try:
                version = (con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone() or [None])[0]
                rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM food WHERE barcode = ? LIMIT 20", (key,)).fetchall()
            except sqlite3.Error:
                continue
        out += [_item(kind, r, lang, version) for r in rows]
    return out


def main(argv=None):
    """Server command line: install the community catalog from a file, remove it, or show what is installed.
    The folders follow the server's settings: HELIO_STATE (user's data) and HELIO_CATALOG (default: the
    data folder's name + "-catalog", next to it)."""
    import argparse
    state = Path(os.environ.get("HELIO_STATE", "/var/lib/helio-app"))
    ap = argparse.ArgumentParser(description="Food catalog of Helio/Ritmo")
    ap.add_argument("--state", default=str(state))
    ap.add_argument("--catalog", default=os.environ.get("HELIO_CATALOG", str(state.parent / (state.name + "-catalog"))))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="check and install a package (.tar)").add_argument("package")
    sub.add_parser("remove", help="remove the community catalog (Open Food Facts)")
    sub.add_parser("info", help="installed catalogs")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "install":
            m = install(args.package, args.catalog, args.state, kinds=("off",))
            print(f"installed {m['kind']} {m['version']}: {m['rows']} foods")
        elif args.cmd == "remove":
            print("removed" if remove("off", args.catalog, args.state) else "not installed")
        else:
            print(json.dumps(info(args.catalog), indent=1, sort_keys=True))
    except CatalogError as e:
        raise SystemExit(f"error: {e.key} {e.params or ''}")


if __name__ == "__main__":
    main()
