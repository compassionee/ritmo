#!/usr/bin/env python3
"""Builds the food catalog packages (see helio_catalog.py) from the published datasets.

  build_food_db.py generic --usda-foundation ZIP --usda-sr ZIP --ciqual DIR --aliases TSV -o generic.tar
  build_food_db.py off --parquet food.parquet -o off.tar        (needs pyarrow, on the computer only)

generic: USDA FoodData Central (Foundation Foods and SR Legacy, CSV zips) and ANSES Ciqual (the XML files
alim_*, compo_*), with the Italian names of catalog/aliases-it.tsv. off: the Open Food Facts Parquet export
(food.parquet, Hugging Face openfoodfacts/product-database), the products sold in Europe (EU 27, Norway,
Iceland, Liechtenstein, Switzerland, United Kingdom) and those without a country but with a European GS1
prefix; the same code written in different forms (GTIN-14 key) is one product, unless the entries are
different products (all kept).

Values per 100 g in the app's terms (EU labels): carbohydrates without fibre, salt (sodium x 2.5), kcal with
the EU factors when the source uses others. Rows that break the app's rules are dropped and counted.
The output is deterministic: the same inputs and --version give the same package.
"""
import argparse
from contextlib import closing
import csv
import io
import json
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import helio_catalog as hc  # noqa: E402
import helio_science as sci  # noqa: E402

USDA = {"protein": 1003, "fat": 1004, "carbs_diff": 1005, "carbs_sum": 1050, "fiber": 1079, "sat": 1258,
        "sodium_mg": 1093, "alcohol": 1018}
CIQUAL = {"kcal": "328", "protein": "25000", "carbs": "31000", "fat": "40000", "fiber": "34100", "sat": "40302",
          "salt": "10004", "sodium_mg": "10110", "polyols": "34000", "alcohol": "60000"}
ETHANOL_DENSITY = 0.789  # Open Food Facts gives alcohol in % vol: grams per 100 g of a drink near 1 g/ml


# ---------------------------------------------------------------- conversions (tested in tests/test_catalog.py)
def usda_values(n):
    """Values per 100 g from the USDA nutrients {nutrient_id: amount}. Carbohydrate "by difference" includes
    fibre: the EU label value is the difference minus fibre. Energy is recomputed with the EU factors (USDA
    uses specific or general Atwater factors). None if protein, fat or carbohydrate is missing."""
    fiber = n.get(USDA["fiber"])
    if n.get(USDA["carbs_diff"]) is not None:
        carbs = max(0.0, n[USDA["carbs_diff"]] - (fiber or 0.0))
    else:
        carbs = n.get(USDA["carbs_sum"])  # by summation: sugars and starch, already without fibre
    if carbs is None or n.get(USDA["protein"]) is None or n.get(USDA["fat"]) is None:
        return None
    v = {"protein": n[USDA["protein"]], "carbs": carbs, "fat": n[USDA["fat"]], "fiber": fiber,
         "sat": n.get(USDA["sat"]), "alcohol": n.get(USDA["alcohol"]),
         "salt": None if n.get(USDA["sodium_mg"]) is None else n[USDA["sodium_mg"]] * 2.5 / 1000, "polyols": None}
    v["kcal"] = sci.atwater_kcal(v)
    return hc.rounded(v)


def ciqual_number(text):
    """A Ciqual content: "12,5" -> 12.5, "traces" -> 0, "< 0,5" -> half the limit, "-" or empty -> None."""
    t = (text or "").strip().replace(",", ".")
    if not t or t == "-":
        return None
    if t.lower() == "traces":
        return 0.0
    if t.startswith("<"):
        try:
            return float(t[1:].strip()) / 2
        except ValueError:
            return None
    try:
        return float(t)
    except ValueError:
        return None


def ciqual_values(c):
    """Values per 100 g from the Ciqual constituents {const_code: text}. Ciqual carbohydrates are already
    without fibre and its energy follows regulation 1169/2011; missing energy is computed the same way."""
    g = {k: ciqual_number(c.get(code)) for k, code in CIQUAL.items()}
    if g["protein"] is None or g["carbs"] is None or g["fat"] is None:
        return None
    if g["salt"] is None and g["sodium_mg"] is not None:
        g["salt"] = g["sodium_mg"] * 2.5 / 1000
    v = {k: g[k] for k in hc.NUTRIENTS}
    # half of a "< limit" can exceed the total it belongs to (polyols "< 0.5" in a food with no carbohydrate)
    if v["polyols"] is not None:
        v["polyols"] = min(v["polyols"], v["carbs"])
    if v["sat"] is not None:
        v["sat"] = min(v["sat"], v["fat"])
    if v["kcal"] is None:
        v["kcal"] = sci.atwater_kcal(v)
    return hc.rounded(v)


def off_number(row, col):
    x = row.get(col)
    if x is None or x == "":
        return None
    try:
        x = float(x)  # text in the CSV, numbers (also 0.0) in the Parquet export
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def off_values(row):
    """Values per 100 g from an Open Food Facts CSV row (energy in kJ if kcal are missing, salt from sodium,
    alcohol from % vol). None if energy, protein, carbohydrate or fat is missing."""
    v = {"kcal": off_number(row, "energy-kcal_100g"), "protein": off_number(row, "proteins_100g"),
         "carbs": off_number(row, "carbohydrates_100g"), "fat": off_number(row, "fat_100g"),
         "fiber": off_number(row, "fiber_100g"), "sat": off_number(row, "saturated-fat_100g"),
         "salt": off_number(row, "salt_100g"), "polyols": off_number(row, "polyols_100g"),
         "alcohol": off_number(row, "alcohol_100g")}
    if v["kcal"] is None and off_number(row, "energy_100g") is not None:
        v["kcal"] = off_number(row, "energy_100g") / 4.184
    if v["salt"] is None and off_number(row, "sodium_100g") is not None:
        v["salt"] = off_number(row, "sodium_100g") * 2.5
    if v["alcohol"] is not None:
        v["alcohol"] *= ETHANOL_DENSITY
    if any(v[k] is None for k in hc.REQUIRED):
        return None
    return hc.rounded(v)


def off_portion(row):
    """[[name, grams]] from serving_size and serving_quantity, or []."""
    g = off_number(row, "serving_quantity")
    name = " ".join((row.get("serving_size") or "").split())[:40]
    if g is None or not 0.1 <= g <= 2000 or not name:
        return []
    return [[name, round(g, 1)]]


# ---------------------------------------------------------------- output
def new_db(path, kind, version, sources):
    con = sqlite3.connect(path)
    con.executescript(hc.SCHEMA_SQL)
    meta = {"format": hc.FORMAT, "schema": str(hc.SCHEMA), "kind": kind, "version": version,
            "sources": json.dumps(sources, sort_keys=True)}
    con.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))
    return con


def insert(con, rows, order=None):
    """rows: dicts with src, ref, name, name_it, aliases, brand, barcode (GTIN-14), countries, values, portions.
    The ids follow order (default: by source and id): the search reads the first rows first. Builds the FTS5
    index and compacts the file."""
    data = []
    for i, r in enumerate(sorted(rows, key=order or (lambda r: (r["src"], r["ref"]))), 1):
        v = r["values"]
        aliases = ", ".join(r.get("aliases") or [])[:1000] or None
        data.append((i, r["src"], r["ref"], r["name"][:200], r.get("name_it"), aliases, r.get("brand") or None,
                     r.get("barcode"), r.get("countries") or None, *[v[k] for k in hc.NUTRIENTS],
                     json.dumps(r["portions"], ensure_ascii=False) if r.get("portions") else None))
        if len(data) >= 20000:
            con.executemany(f"INSERT INTO food VALUES ({', '.join('?' * len(hc.COLUMNS))})", data)
            data = []
    con.executemany(f"INSERT INTO food VALUES ({', '.join('?' * len(hc.COLUMNS))})", data)
    hc.build_fts(con)
    con.commit()
    con.execute("VACUUM")
    con.close()


def package(rows, kind, version, sources, out, order=None):
    """Builds the database, checks it fully (SQLite integrity, FTS5 index, values) and packs it."""
    with tempfile.TemporaryDirectory(dir=Path(out).resolve().parent) as tmp:
        db = Path(tmp) / "catalog.sqlite"
        insert(new_db(db, kind, version, sources), rows, order)
        with closing(sqlite3.connect(db)) as con:
            n = con.execute("SELECT count(*) FROM food").fetchone()[0]
        hc.check_db(db, {"kind": kind, "version": version, "rows": n}, full=True)
        return hc.write_package(db, out)


def zip_csv(zf, name):
    member = next(m for m in zf.namelist() if m.endswith("/" + name) or m == name)
    return csv.DictReader(io.TextIOWrapper(zf.open(member), encoding="utf-8-sig", newline=""))


def usda_rows(path, src, type_file):
    """Foods of one USDA dataset (the fdc_id listed in type_file)."""
    with zipfile.ZipFile(path) as zf:
        ids = {r["fdc_id"] for r in zip_csv(zf, type_file)}
        names = {r["fdc_id"]: r["description"].strip() for r in zip_csv(zf, "food.csv") if r["fdc_id"] in ids}
        wanted = set(USDA.values())
        nut = {}
        for r in zip_csv(zf, "food_nutrient.csv"):
            if r["fdc_id"] in names and r["amount"] and int(r["nutrient_id"]) in wanted:
                nut.setdefault(r["fdc_id"], {})[int(r["nutrient_id"])] = float(r["amount"])
        units = {r["id"]: r["name"] for r in zip_csv(zf, "measure_unit.csv")}
        portions = {}
        for r in zip_csv(zf, "food_portion.csv"):
            if r["fdc_id"] not in names or not r["gram_weight"]:
                continue
            unit = units.get(r["measure_unit_id"], "")
            label = " ".join(x for x in (_amount(r["amount"]), "" if unit == "undetermined" else unit,
                                         r["modifier"] or r["portion_description"]) if x)[:40].strip()
            g = float(r["gram_weight"])
            if label and 0.1 <= g <= 2000:
                portions.setdefault(r["fdc_id"], []).append([label, round(g, 1)])
    out, dropped = [], 0
    for fid, name in names.items():
        v = usda_values(nut.get(fid, {}))
        if v is None or not hc.plausible(v) or not name:
            dropped += 1
            continue
        out.append({"src": src, "ref": fid, "name": name, "values": v, "portions": portions.get(fid, [])[:4]})
    return out, dropped


def _amount(a):
    try:
        x = float(a)
    except ValueError:
        return ""
    return f"{x:g}"


def ciqual_rows(folder):
    folder = Path(folder)
    alim = next(folder.glob("*alim_2*.xml"))
    compo = next(folder.glob("*compo_*.xml"))
    names = {}
    for el in ET.parse(alim).getroot():
        code = (el.findtext("alim_code") or "").strip()
        names[code] = (el.findtext("alim_nom_eng") or el.findtext("alim_nom_fr") or "").strip()
    wanted = set(CIQUAL.values())
    comp = {}
    for _, el in ET.iterparse(compo):
        if el.tag != "COMPO":
            continue
        const = (el.findtext("const_code") or "").strip()
        if const in wanted:
            comp.setdefault((el.findtext("alim_code") or "").strip(), {})[const] = el.findtext("teneur")
        el.clear()
    out, dropped = [], 0
    for code, name in names.items():
        v = ciqual_values(comp.get(code, {}))
        if v is None or not hc.plausible(v) or not name:
            dropped += 1
            continue
        out.append({"src": "ciqual", "ref": code, "name": name, "values": v, "portions": []})
    return out, dropped


def load_aliases(path):
    """catalog/aliases-it.tsv: src, ref, Italian name, other Italian names (comma separated), portions
    ("name=grams;name=grams"). Returns {(src, ref): {...}}."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = (line.split("\t") + ["", "", ""])[:5]
            src, ref, name, aliases, portions = (p.strip() for p in parts)
            if not (src and ref and name):
                raise ValueError(f"{path}:{n}: src, ref and name are required")
            if (src, ref) in out:
                raise ValueError(f"{path}:{n}: {src} {ref} is listed twice")
            pp = []
            for p in filter(None, (x.strip() for x in portions.split(";"))):
                label, g = p.rsplit("=", 1)
                pp.append([label.strip(), float(g)])
            out[(src, ref)] = {"name_it": name, "aliases": [a.strip() for a in aliases.split(",") if a.strip()],
                               "portions": pp}
    return out


def build_generic(args):
    rows, report = [], {}
    for src, path, type_file in (("usda_fnd", args.usda_foundation, "foundation_food.csv"),
                                 ("usda_sr", args.usda_sr, "sr_legacy_food.csv")):
        r, dropped = usda_rows(path, src, type_file)
        rows += r
        report[src] = (len(r), dropped)
    r, dropped = ciqual_rows(args.ciqual)
    rows += r
    report["ciqual"] = (len(r), dropped)
    aliases = load_aliases(args.aliases)
    by_key = {(r["src"], r["ref"]): r for r in rows}
    unknown = sorted(f"{s} {ref}" for s, ref in aliases if (s, ref) not in by_key)
    if unknown:
        raise SystemExit("aliases without a food in the sources: " + ", ".join(unknown))
    for key, a in aliases.items():
        r = by_key[key]
        r["name_it"], r["aliases"] = a["name_it"], a["aliases"]
        r["portions"] = (a["portions"] + r["portions"])[:hc.PORTIONS_MAX]
    sources = [{"id": "usda_fnd", "version": args.usda_foundation_version},
               {"id": "usda_sr", "version": "2018-04"},
               {"id": "ciqual", "version": args.ciqual_version}]
    m = package(rows, "generic", args.version, sources, args.output)
    for k, (n, d) in report.items():
        print(f"{k}: {n} foods, {d} dropped")
    return m


# ---------------------------------------------------------------- Open Food Facts (Parquet)
OFF_COUNTRIES = {"austria": "at", "belgium": "be", "bulgaria": "bg", "croatia": "hr", "cyprus": "cy",
                 "czech-republic": "cz", "denmark": "dk", "estonia": "ee", "finland": "fi", "france": "fr",
                 "germany": "de", "greece": "gr", "hungary": "hu", "ireland": "ie", "italy": "it", "latvia": "lv",
                 "lithuania": "lt", "luxembourg": "lu", "malta": "mt", "netherlands": "nl", "poland": "pl",
                 "portugal": "pt", "romania": "ro", "slovakia": "sk", "slovenia": "si", "spain": "es", "sweden": "se",
                 "norway": "no", "iceland": "is", "liechtenstein": "li", "switzerland": "ch", "united-kingdom": "gb"}
OFF_COLUMNS = ["code", "countries_tags", "product_name", "lang", "brands", "nutriments", "serving_quantity",
               "serving_size", "last_modified_t"]


def off_countries(tags):
    """European ISO codes from countries_tags (en:italy -> it), sorted."""
    return sorted({OFF_COUNTRIES[t.split(":", 1)[1]] for t in tags or [] if t.split(":", 1)[-1] in OFF_COUNTRIES})


def off_names(product_name, main_lang):
    """(Italian name, name for the other languages: English, then the original, then any; every distinct name)."""
    by = {}
    for p in product_name or []:
        text = " ".join((p.get("text") or "").split())[:200]
        if text and p.get("lang"):
            by.setdefault(p["lang"], text)
    original = by.get("main") or by.get(main_lang or "")
    it, en = by.get("it"), by.get("en")
    main = en or original or next(iter(by.values()), None)
    names = list(dict.fromkeys(by.values()))
    return it, main, names


def off_record(r):
    """A product of the Parquet export as a catalog row, or None (not European, no name, values missing or
    not plausible). The row keeps every name, the GTIN-14 key and the European countries."""
    countries = off_countries(r.get("countries_tags"))
    code = (r.get("code") or "").strip()
    key = hc.gtin_key(code)
    if not countries:
        gs1 = hc.gs1_country(key)
        if not gs1:
            return None
        countries = ["~" + gs1]
    it, main, names = off_names(r.get("product_name"), r.get("lang"))
    if not main or not code or len(code) > 40:
        return None
    flat = {f"{n['name']}_100g": n.get("100g") for n in r.get("nutriments") or [] if n.get("name")}
    v = off_values(flat)
    if v is None or not hc.plausible(v):
        return None
    brand = (r.get("brands") or "").split(",")[0].strip()[:100]
    return {"src": "off", "ref": code, "name": main, "name_it": it,
            "aliases": [n for n in names if n not in (main, it)], "brand": brand, "barcode": key,
            "countries": countries, "values": v,
            "portions": off_portion({"serving_size": r.get("serving_size"), "serving_quantity": r.get("serving_quantity")}),
            "modified": r.get("last_modified_t") or 0}


def same_product(a, b):
    """Two products with the same GTIN-14 (written in different forms): the same one if the brands do not
    differ and the names share most words, or the values per 100 g are the same within label rounding."""
    ba, bb = hc.norm(a["brand"]), hc.norm(b["brand"])
    if ba and bb and ba != bb:
        return False
    wa = set(hc.norm(" ".join([a["name"], a["name_it"] or ""] + a["aliases"])).split())
    wb = set(hc.norm(" ".join([b["name"], b["name_it"] or ""] + b["aliases"])).split())
    words = len(wa & wb) / max(1, len(wa | wb))
    va, vb = a["values"], b["values"]
    close = (abs(va["kcal"] - vb["kcal"]) <= max(15, 0.1 * max(va["kcal"], vb["kcal"]))
             and all(abs(va[k] - vb[k]) <= 2 + 0.1 * max(va[k], vb[k]) for k in ("protein", "carbs", "fat")))
    return words >= 0.5 or close


def completeness(r):
    return (sum(r["values"][k] is not None for k in hc.NUTRIENTS) + bool(r["name_it"]) + bool(r["portions"])
            + bool(r["brand"]), r["modified"], r["ref"])


def merge_same_code(group):
    """Products sharing a GTIN-14: the same product once (the most complete entry, with every name and
    country of the others); different products are all kept."""
    by_ref = {}
    for r in sorted(group, key=completeness, reverse=True):
        by_ref.setdefault(r["ref"], r)  # the export can list the same code twice: the most complete entry
    clusters = []
    for r in by_ref.values():
        for c in clusters:
            if same_product(c[0], r):
                c.append(r)
                break
        else:
            clusters.append([r])
    out = []
    for c in clusters:
        best = dict(c[0])
        names = [x for r in c for x in [r["name"], r["name_it"]] + r["aliases"] if x]
        best["aliases"] = [n for n in dict.fromkeys(names) if n not in (best["name"], best["name_it"])]
        countries = {x for r in c for x in r["countries"]}
        real = sorted(x for x in countries if not x.startswith("~"))
        best["countries"] = real or sorted(countries)
        out.append(best)
    return out


def off_order(r):
    """Rows the search reads first: Italian names and products sold in Italy, then the most complete."""
    filled = sum(r["values"][k] is not None for k in hc.NUTRIENTS)
    return (not r["name_it"], "it" not in r["countries"], -filled, -r["modified"], r["ref"])


def off_rows(parquet, report):
    """Every European product, merged by GTIN-14 (staged on disk: millions of rows)."""
    import pyarrow.parquet as pq  # only for building, on the computer
    f = pq.ParquetFile(parquet)
    with tempfile.TemporaryDirectory(dir=Path(parquet).resolve().parent) as tmp:
        stage = sqlite3.connect(Path(tmp) / "stage.sqlite")
        stage.execute("CREATE TABLE r(key TEXT, data TEXT)")
        for i in range(f.num_row_groups):
            batch = []
            for r in f.read_row_group(i, columns=OFF_COLUMNS).to_pylist():
                report["total"] += 1
                rec = off_record(r)
                if rec is None:
                    continue
                report["kept_before_merge"] += 1
                batch.append((rec["barcode"] or "ref:" + rec["ref"], json.dumps(rec, ensure_ascii=False)))
            stage.executemany("INSERT INTO r VALUES (?, ?)", batch)
        stage.commit()
        stage.execute("CREATE INDEX r_key ON r(key)")
        rows, group, last = [], [], None

        def flush():
            if not group:
                return
            if not last.isdigit():
                rows.append(group[0])  # a code that is not a barcode: OFF keeps it once
                return
            merged = merge_same_code(group)
            report["groups_split"] += len(merged) > 1
            rows.extend(merged)

        for key, data in stage.execute("SELECT key, data FROM r ORDER BY key"):
            if key != last:
                flush()
                group = []
            last = key
            group.append(json.loads(data))
        flush()
        stage.close()
    return rows


def build_off(args):
    report = {"total": 0, "kept_before_merge": 0, "groups_split": 0}
    rows = off_rows(args.parquet, report)
    for r in rows:
        r["countries"] = ",".join(r["countries"])
    m = package(rows, "off", args.version, [{"id": "off", "version": args.version, "region": "europe"}], args.output,
                order=lambda r: off_order({**r, "countries": r["countries"].split(",")}))
    print(f"off: {report['total']} products, {report['kept_before_merge']} European and plausible, {len(rows)} after "
          f"merging codes, {report['groups_split']} codes with different products")
    return m


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a food catalog package")
    sub = ap.add_subparsers(dest="kind", required=True)
    g = sub.add_parser("generic", help="USDA FoodData Central and Ciqual")
    g.add_argument("--usda-foundation", required=True)
    g.add_argument("--usda-foundation-version", required=True, help="release date, for example 2026-04-30")
    g.add_argument("--usda-sr", required=True)
    g.add_argument("--ciqual", required=True, help="folder with the Ciqual XML files")
    g.add_argument("--ciqual-version", required=True, help="release date, for example 2025-11-03")
    g.add_argument("--aliases", default=str(Path(__file__).resolve().parents[1] / "catalog" / "aliases-it.tsv"))
    o = sub.add_parser("off", help="Open Food Facts, products sold in Europe")
    o.add_argument("--parquet", required=True, help="food.parquet of the Open Food Facts export")
    for p in (g, o):
        p.add_argument("--version", default=date.today().isoformat(), help="catalog version (YYYY-MM-DD)")
        p.add_argument("-o", "--output", required=True)
    args = ap.parse_args(argv)
    m = build_generic(args) if args.kind == "generic" else build_off(args)
    manifest = Path(args.output).with_suffix(".manifest.json")
    manifest.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    print(f"{args.output}: {m['rows']} rows, xz {m['xz_size']} bytes, sqlite {m['sqlite_size']} bytes")
    print(f"sha256 xz {m['xz_sha256']}")


if __name__ == "__main__":
    main()
