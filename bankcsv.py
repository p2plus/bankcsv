#!/usr/bin/env python3
"""bankcsv — clean garbage bank/client CSV exports into boring, consistent CSVs.

Banks export CSVs like they hate you: cp1252, semicolons, decimal commas,
three date formats in one file, and a footer that pretends to be a row.
This fixes that. Pure stdlib. No network. No config files.

Output contract: UTF-8, comma-delimited, ISO 8601 dates, dot decimals,
columns named date / payee / amount [/ balance], junk rows dropped,
every other non-empty column passed through untouched.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import unittest
from datetime import datetime

VERSION = "0.1.0"
CANONICAL = ("date", "payee", "amount", "balance")


class BankcsvError(Exception):
    """User-facing failure (bad file, no mapping found, ...)."""


# --------------------------------------------------------------- encoding

def detect_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


# --------------------------------------------------------------- delimiter

_DELIMS = (",", ";", "\t", "|")


def sniff_delimiter(text: str, sample: int = 20) -> str:
    lines = [ln for ln in text.splitlines()[:sample] if ln.strip()]
    if not lines:
        return ","
    best, best_score = ",", -1.0
    for d in _DELIMS:
        counts = [ln.count(d) for ln in lines]
        modal = max(set(counts), key=counts.count)
        if modal == 0:
            continue
        score = modal * (counts.count(modal) / len(lines))
        if score > best_score:
            best, best_score = d, score
    return best


# --------------------------------------------------------------- amounts

_CURRENCY_RE = re.compile(
    r"(?i)(?<=\d)\s*(?:eur|euro|usd|chf|gbp)\b"
    r"|\b(?:eur|euro|usd|chf|gbp)\s*(?=\d)"
    r"|[€$£¥]")
_TICK_RE = re.compile(r"(?<=\d)'")
_NUM_RE = re.compile(r"[\d.,]+")


def parse_amount(raw, decimal=None):
    """Return float or None.

    Handles: currency glyphs and words (before or after the number, with or
    without a space), thin/no-break spaces, trailing minus ("42,13-"),
    accounting parens ("(42.13)"), Swiss ticks ("1'234,56"), thousands
    grouping in either locale ("1.234,56" / "1,234.56").
    Ambiguous "1,234" / "1.234" (one separator, 3 trailing digits, short
    integer part) is treated as thousands grouping — override with
    --decimal if your data disagrees.
    """
    if raw is None:
        return None
    t = str(raw).strip()
    t = _CURRENCY_RE.sub("", t)
    t = t.replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    t = _TICK_RE.sub("", t)
    if not t:
        return None
    neg = False
    if t.endswith("-"):
        neg, t = True, t[:-1]
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t and t[0] in "+-":
        neg = neg or t[0] == "-"
        t = t[1:]
    if not t or not _NUM_RE.fullmatch(t):
        return None

    def finish(v):
        return -v if neg else v

    if decimal:  # explicit user override: strip the other separator
        other = "," if decimal == "." else "."
        try:
            return finish(float(t.replace(other, "").replace(decimal, ".")))
        except ValueError:
            return None
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        other = "." if dec == "," else ","
        intpart, _, frac = t.rpartition(dec)
        try:
            return finish(float(intpart.replace(other, "") + "." + frac.replace(other, "")))
        except ValueError:
            return None
    if t.count(",") > 1 or t.count(".") > 1:  # 1,234,567 → grouping
        return finish(float(t.replace(",", "").replace(".", "")))
    sep = "," if "," in t else "."
    if sep not in t:
        return finish(float(t))
    intpart, _, frac = t.partition(sep)
    if len(frac) == 3 and 1 <= len(intpart) <= 3:  # 1.234 → thousands
        return finish(float(intpart + frac))
    return finish(float(intpart + "." + (frac or "0")))


# --------------------------------------------------------------- dates

_MONTHS = {}
for _i, _names in enumerate([
    ("jan", "januar", "january"), ("feb", "februar", "february"),
    ("mar", "mär", "mrz", "märz", "march"), ("apr", "april"),
    ("mai", "may"), ("jun", "juni", "june"), ("jul", "juli", "july"),
    ("aug", "august"), ("sep", "sept", "september"),
    ("okt", "oktober", "oct", "october"), ("nov", "november"),
    ("dez", "dezember", "dec", "december"),
]):
    for _n in _names:
        _MONTHS[_n] = _i + 1

_DAY_FIRST_NAME_RE = re.compile(r"^(\d{1,2})\.?\s+([A-Za-zÄÖÜäöü]+)\.?,?\s+(\d{4})$")
_NAME_FIRST_RE = re.compile(r"^([A-Za-zÄÖÜäöü]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$")


def parse_date(raw, fmt=None, dayfirst=False):
    """Return ISO 8601 date string or None.

    Numeric formats are auto-detected. Dot dates are day-first (German
    convention), slash dates are month-first unless the first number rules
    it out; `dayfirst=True` flips the ambiguous-slash default. `fmt` is an
    explicit strptime format that overrides everything.
    """
    if raw is None:
        return None
    t = " ".join(raw.split()).strip()
    if not t or len(t) > 25:
        return None
    if fmt:
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            return None
    for f in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(t, f).date().isoformat()
        except ValueError:
            pass
    if t.isdigit():
        if len(t) == 8:
            try:
                return datetime.strptime(t, "%Y%m%d").date().isoformat()
            except ValueError:
                return None
        return None
    if "." in t:
        for f in ("%d.%m.%Y", "%d.%m.%y", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
            try:
                return datetime.strptime(t, f).date().isoformat()
            except ValueError:
                pass
    for sep in ("/", "-"):
        if sep not in t:
            continue
        head = t.split()[0]
        parts = head.split(sep)
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            a, b, y = parts
            if len(a) == 4:  # already covered by ISO above
                return None
            if int(a) > 12 and int(b) <= 12:
                first_is_day = True
            elif int(b) > 12 and int(a) <= 12:
                first_is_day = False
            else:
                first_is_day = dayfirst  # ambiguous: flag decides
            order = ("%d", "%m") if first_is_day else ("%m", "%d")
            for f in (order[0] + sep + order[1] + sep + "%Y",
                      order[0] + sep + order[1] + sep + "%y"):
                try:
                    return datetime.strptime(t, f).date().isoformat()
                except ValueError:
                    pass
            return None
        return None
    m = _DAY_FIRST_NAME_RE.match(t)
    if m and m.group(2).lower() in _MONTHS:
        mo = _MONTHS[m.group(2).lower()]
        return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(1)):02d}"
    m = _NAME_FIRST_RE.match(t)
    if m and m.group(1).lower() in _MONTHS:
        mo = _MONTHS[m.group(1).lower()]
        return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}"
    return None


# --------------------------------------------------------------- column mapping

SYNONYMS = (
    ("date", ("date", "datum", "buchungstag", "buchungsdatum", "valuta",
              "valutadatum", "wertstellung", "belegdatum", "booking date",
              "transaction date", "posting date", "started date",
              "completed date")),
    ("amount", ("amount", "betrag", "umsatz", "summe", "gebühr", "gebuehr",
                "fee", "transaktion", "value")),
    ("balance", ("balance", "saldo", "kontostand", "restbetrag")),
    ("payee", ("verwendungszweck", "description", "beschreibung", "payee",
               "purpose", "memo", "details", "empfänger", "empfaenger",
               "begünstigter", "beguenstigter", "merchant", "creditor",
               "debtor", "name", "partner", "counterparty", "buchungstext")),
)


def _tokens(header):
    return set(re.findall(r"[a-zäöü]+", header.lower()))


def classify_header_row(row):
    """Map canonical columns to indices for one header row, or None.

    If several columns match the same canonical role, the one matching the
    highest-priority synonym wins (e.g. Verwendungszweck over Buchungstext).
    """
    candidates = {}
    for i, cell in enumerate(row):
        toks = _tokens(cell)
        if not toks:
            continue
        for canon, syns in SYNONYMS:
            for j, s in enumerate(syns):
                if toks & _tokens(s):
                    candidates.setdefault(canon, []).append((j, i))
                    break
            else:
                continue
            break
    mapping = {canon: min(cands)[1] for canon, cands in candidates.items()}
    if "date" in mapping and "amount" in mapping:
        return mapping
    return None


def classify_content(rows, dayfirst=False, scan=200):
    """Map columns by parsing success ratios; requires a date and amount col."""
    sample = rows[:scan]
    ncols = max(len(r) for r in sample)
    stats = []
    for c in range(ncols):
        vals = [r[c] for r in sample if c < len(r) and r[c].strip()]
        if not vals:
            stats.append((0.0, 0.0, 0.0))
            continue
        dr = sum(1 for v in vals if parse_date(v, dayfirst=dayfirst)) / len(vals)
        amounts = [parse_amount(v) for v in vals]
        ar = sum(1 for a in amounts if a is not None) / len(vals)
        avglen = sum(len(v) for v in vals) / len(vals)
        stats.append((dr, ar, avglen))
    datecol = max(range(ncols), key=lambda c: stats[c][0])
    if stats[datecol][0] < 0.6:
        return None
    rest = [c for c in range(ncols) if c != datecol]
    if not rest:
        return None
    amountcol = max(rest, key=lambda c: stats[c][1])
    if stats[amountcol][1] < 0.6:
        return None
    rest = [c for c in rest if c != amountcol]
    # No balance guessing in headerless mode: an all-positive numeric column
    # could be a fee or second amount column — we don't invent semantics.
    balancecol = None
    payeecol = None
    for c in sorted(rest, key=lambda c: stats[c][2], reverse=True):
        if stats[c][0] < 0.5 and stats[c][1] < 0.5 and stats[c][2] >= 8:
            payeecol = c
            break
    m = {"date": datecol, "amount": amountcol}
    if payeecol is not None:
        m["payee"] = payeecol
    if balancecol is not None:
        m["balance"] = balancecol
    return m


# --------------------------------------------------------------- cleaning

def _norm_cell(c):
    return " ".join(str(c).split()).lower()


def load_rows(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise BankcsvError(str(e)) from e
    if not raw.strip():
        raise BankcsvError("file is empty")
    enc = detect_encoding(raw)
    if enc != "utf-16" and b"\x00" in raw[:8192]:
        raise BankcsvError("input looks like a binary file (zip, Excel, "
                           "image?) — bankcsv converts CSV text only")
    text = raw.decode(enc)
    delim = sniff_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    meta = {"encoding": enc, "delimiter": delim}
    return rows, meta


def resolve_mapping(rows, headered=None, dayfirst=False):
    """Return (mapping, header_row, first_data_index)."""
    if headered is not False:
        for i, r in enumerate(rows[:5]):
            m = classify_header_row(r)
            if m:
                return m, r, i + 1
    if headered is not True:
        m = classify_content(rows, dayfirst=dayfirst)
        if m:
            return m, None, 0
    raise BankcsvError(
        "could not locate date+amount columns — run `bankcsv probe FILE`, "
        "then point me at them with a real header row or --datefmt/--decimal")


def clean_rows(rows, decimal=None, dayfirst=False, datefmt=None,
               headered=None, delimiter=None, encoding=None):
    """Return (out_header, out_rows, report). See module docstring."""
    mapping, header, start = resolve_mapping(rows, headered=headered,
                                             dayfirst=dayfirst)
    ncols = max(len(r) for r in rows) if rows else 0
    mapped_idx = set(mapping.values())
    extra_idx = [c for c in range(ncols) if c not in mapped_idx]
    if header:
        extra_names = [header[c].strip() if c < len(header) else f"col{c + 1}"
                       for c in extra_idx]
    else:
        extra_names = [f"col{c + 1}" for c in extra_idx]

    header_norm = [_norm_cell(c) for c in header] if header else None
    out_rows, junk = [], {"blank": 0, "repeated header": 0,
                          "no date": 0, "no amount": 0}
    extra_used = [False] * len(extra_idx)

    for r in rows[start:]:
        cells = list(r) + [""] * (ncols - len(r))
        if not any(str(c).strip() for c in r):
            junk["blank"] += 1
            continue
        if header_norm and [_norm_cell(c) for c in cells[:len(header_norm)]] == header_norm:
            junk["repeated header"] += 1
            continue

        def get(canon):
            i = mapping.get(canon)
            return cells[i] if i is not None else ""

        d = parse_date(get("date"), fmt=datefmt, dayfirst=dayfirst)
        if d is None:
            junk["no date"] += 1
            continue
        a = parse_amount(get("amount"), decimal=decimal)
        if a is None:
            junk["no amount"] += 1
            continue
        payee = re.sub(r"\s+", " ", get("payee")).strip()
        bal = parse_amount(get("balance"), decimal=decimal)
        row_out = [d, payee, f"{a:.2f}"]
        if "balance" in mapping:
            row_out.append("" if bal is None else f"{bal:.2f}")
        for k, c in enumerate(extra_idx):
            v = cells[c].strip() if c < len(cells) else ""
            if v:
                extra_used[k] = True
            row_out.append(v)
        out_rows.append(row_out)

    keep_bal = "balance" in mapping
    keep_extra = [k for k in range(len(extra_idx)) if extra_used[k]]
    out_header = ["date", "payee", "amount"] + (["balance"] if keep_bal else [])
    out_header += [extra_names[k] for k in keep_extra]
    out_rows = [[r[i] for i in
                 list(range(3 + (1 if keep_bal else 0)))
                 + [3 + (1 if keep_bal else 0) + k for k in keep_extra]]
                for r in out_rows]

    report = {
        "encoding": encoding, "delimiter": delimiter,
        "rows_in": len(rows) - start, "kept": len(out_rows),
        "junk": {k: v for k, v in junk.items() if v},
        "mapping": {canon: (header[mapping[canon]].strip()
                            if header else f"col{mapping[canon] + 1}")
                    for canon in CANONICAL if canon in mapping},
        "extras": [extra_names[k] for k in keep_extra],
    }
    return out_header, out_rows, report


# --------------------------------------------------------------- report

def format_report(report):
    lines = []
    lines.append(f"bankcsv: encoding={report['encoding']} "
                 f"delimiter={report['delimiter']!r}")
    m = "  ".join(f"{c}←{n!r}" for c, n in report["mapping"].items())
    lines.append(f"bankcsv: mapping: {m}")
    if report["extras"]:
        lines.append("bankcsv: extras kept: " + ", ".join(report["extras"]))
    junk = ", ".join(f"{k}: {v}" for k, v in report["junk"].items()) or "none"
    lines.append(f"bankcsv: {report['rows_in']} rows in → {report['kept']} kept, "
                 f"{report['rows_in'] - report['kept']} junk ({junk})")
    return "\n".join(lines)


# --------------------------------------------------------------- self test

def run_self_test():
    here = os.path.dirname(os.path.abspath(__file__))
    suite = unittest.defaultTestLoader.discover(here, pattern="test_bankcsv*.py")
    if suite.countTestCases() == 0:
        print("bankcsv: no test module found next to the source — "
              "self-test is a repo checkout feature")
        return 0
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if res.wasSuccessful() else 1


# --------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="bankcsv",
        description="Clean garbage bank/client CSV exports into boring, "
                    "consistent CSVs. Pure stdlib, no network.")
    p.add_argument("--version", action="version", version=f"bankcsv {VERSION}")
    p.add_argument("--self-test", action="store_true",
                   help="run the embedded test suite and exit")
    sub = p.add_subparsers(dest="cmd")

    def add_common(sp):
        sp.add_argument("--decimal", choices=("auto", ",", "."), default="auto",
                        help="decimal separator override (default: sniff)")
        sp.add_argument("--delimiter", default=None, metavar="CHAR",
                        help="field delimiter override (default: sniff)")
        sp.add_argument("--datefmt", default=None, metavar="FMT",
                        help="explicit strptime format for the date column")
        sp.add_argument("--dayfirst", action="store_true",
                        help="treat ambiguous slash dates as day-first")
        sp.add_argument("--headers", choices=("auto", "yes", "no"),
                        default="auto",
                        help="first row is a header (default: auto-detect)")

    pc = sub.add_parser("clean", help="clean a CSV, write result")
    pc.add_argument("file", help="input CSV (any encoding/delimiter)")
    pc.add_argument("-o", "--out", default=None,
                    help="output path (default: stdout)")
    pc.add_argument("--preview", type=int, default=None, metavar="N",
                    help="print only the first N cleaned rows")
    add_common(pc)

    pp = sub.add_parser("probe", help="show what would be detected")
    pp.add_argument("file")
    add_common(pp)

    args = p.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if not args.cmd:
        p.print_help(sys.stderr)
        return 2

    try:
        rows, meta = load_rows(args.file)
    except BankcsvError as e:
        print(f"bankcsv: error: {e}", file=sys.stderr)
        return 1
    if args.delimiter:
        meta["delimiter"] = args.delimiter
        rows = list(csv.reader(io.StringIO(
            "".join(l + "\n" for l in
                    open(args.file, "rb").read().decode(meta["encoding"]).splitlines())),
            delimiter=args.delimiter))
    decimal = None if args.decimal == "auto" else args.decimal
    headered = None if args.headers == "auto" else args.headers == "yes"

    if args.cmd == "probe":
        try:
            mapping, header, _ = resolve_mapping(rows, headered=headered,
                                                 dayfirst=args.dayfirst)
            shown = {c: (header[mapping[c]].strip() if header
                         else f"col{mapping[c] + 1}")
                     for c in CANONICAL if c in mapping}
        except BankcsvError as e:
            shown = f"none ({e})"
        print(f"encoding:  {meta['encoding']}")
        print(f"delimiter: {meta['delimiter']!r}")
        print(f"rows:      {len(rows)} (incl. header, blanks)")
        print(f"mapping:   {shown}")
        return 0

    try:
        out_header, out_rows, report = clean_rows(
            rows, decimal=decimal, dayfirst=args.dayfirst,
            datefmt=args.datefmt, headered=headered,
            delimiter=meta["delimiter"], encoding=meta["encoding"])
    except BankcsvError as e:
        print(f"bankcsv: error: {e}", file=sys.stderr)
        return 1

    print(format_report(report), file=sys.stderr)
    if args.preview is not None:
        out_rows = out_rows[:args.preview]

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(out_header)
    w.writerows(out_rows)
    payload = buf.getvalue()
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8", newline="") as f:
                f.write(payload)
        except OSError as e:
            print(f"bankcsv: error: {e}", file=sys.stderr)
            return 1
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
