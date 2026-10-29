"""Tests for bankcsv. Run: python3 -m unittest test_bankcsv -v"""
import contextlib
import csv
import io
import os
import tempfile
import unittest
from pathlib import Path

import bankcsv as b


class TestEncoding(unittest.TestCase):
    def test_utf8_bom(self):
        self.assertEqual(b.detect_encoding(b"\xef\xbb\xbfdate,x\n"), "utf-8-sig")

    def test_utf16_le(self):
        self.assertEqual(b.detect_encoding("date,x\n".encode("utf-16")), "utf-16")

    def test_plain_utf8(self):
        self.assertEqual(b.detect_encoding("datum;ä".encode("utf-8")), "utf-8")

    def test_cp1252_fallback(self):
        self.assertEqual(b.detect_encoding("Café Müller".encode("cp1252")), "cp1252")


class TestDelimiter(unittest.TestCase):
    def test_semicolon(self):
        self.assertEqual(b.sniff_delimiter('a;b;c\n1;2;3\n'), ";")

    def test_comma(self):
        self.assertEqual(b.sniff_delimiter('a,b,c\n1,2,3\n'), ",")

    def test_tab(self):
        self.assertEqual(b.sniff_delimiter("a\tb\tc\n1\t2\t3\n"), "\t")

    def test_pipe(self):
        self.assertEqual(b.sniff_delimiter("a|b|c\n1|2|3\n"), "|")


class TestAmounts(unittest.TestCase):
    def test_de_decimal(self):
        self.assertEqual(b.parse_amount("42,13"), 42.13)

    def test_de_thousands(self):
        self.assertEqual(b.parse_amount("1.000,00"), 1000.0)

    def test_us(self):
        self.assertEqual(b.parse_amount("1,234.56"), 1234.56)

    def test_negative(self):
        self.assertEqual(b.parse_amount("-42,13"), -42.13)

    def test_trailing_minus(self):
        self.assertEqual(b.parse_amount("42,13-"), -42.13)

    def test_parens(self):
        self.assertEqual(b.parse_amount("(42.13)"), -42.13)

    def test_currency_glyph(self):
        self.assertEqual(b.parse_amount("€ 1.000,00"), 1000.0)

    def test_currency_word(self):
        self.assertEqual(b.parse_amount("12,34 EUR"), 12.34)

    def test_swiss_tick(self):
        self.assertEqual(b.parse_amount("1'234,56"), 1234.56)

    def test_thousands_ambiguous_comma(self):
        self.assertEqual(b.parse_amount("1,234"), 1234.0)

    def test_thousands_ambiguous_dot(self):
        self.assertEqual(b.parse_amount("1.234"), 1234.0)

    def test_short_decimal_dot(self):
        self.assertEqual(b.parse_amount("1234.5"), 1234.5)

    def test_multiple_grouping(self):
        self.assertEqual(b.parse_amount("1,234,567"), 1234567.0)

    def test_plain_int(self):
        self.assertEqual(b.parse_amount("1000"), 1000.0)

    def test_override_decimal(self):
        self.assertEqual(b.parse_amount("1,234", decimal=","), 1.234)

    def test_nbsp(self):
        self.assertEqual(b.parse_amount("1\u00a0234,56"), 1234.56)

    def test_garbage(self):
        self.assertIsNone(b.parse_amount("Saldo"))

    def test_empty(self):
        self.assertIsNone(b.parse_amount(""))

    def test_minus_only(self):
        self.assertIsNone(b.parse_amount("-"))


class TestDates(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(b.parse_date("2026-10-29"), "2026-10-29")

    def test_iso_datetime(self):
        self.assertEqual(b.parse_date("2026-10-29 14:30:00"), "2026-10-29")

    def test_german_dot(self):
        self.assertEqual(b.parse_date("29.10.2026"), "2026-10-29")

    def test_german_dot_short_year(self):
        self.assertEqual(b.parse_date("29.10.26"), "2026-10-29")

    def test_dot_with_time(self):
        self.assertEqual(b.parse_date("29.10.2026 14:30"), "2026-10-29")

    def test_us_slash(self):
        self.assertEqual(b.parse_date("10/29/2026"), "2026-10-29")

    def test_eu_slash_inferred(self):
        self.assertEqual(b.parse_date("29/10/2026"), "2026-10-29")

    def test_ambiguous_slash_defaults_us(self):
        self.assertEqual(b.parse_date("05/06/2026"), "2026-05-06")

    def test_ambiguous_slash_dayfirst(self):
        self.assertEqual(b.parse_date("05/06/2026", dayfirst=True), "2026-06-05")

    def test_dash_eu(self):
        self.assertEqual(b.parse_date("29-10-2026"), "2026-10-29")

    def test_dash_short_year(self):
        self.assertEqual(b.parse_date("29-10-26"), "2026-10-29")

    def test_compact(self):
        self.assertEqual(b.parse_date("20261029"), "2026-10-29")

    def test_month_name_first(self):
        self.assertEqual(b.parse_date("Oct 5, 2026"), "2026-10-05")

    def test_day_month_name(self):
        self.assertEqual(b.parse_date("5 Oct 2026"), "2026-10-05")

    def test_german_month_name(self):
        self.assertEqual(b.parse_date("5. Oktober 2026"), "2026-10-05")

    def test_german_abbrev(self):
        self.assertEqual(b.parse_date("5. Okt. 2026"), "2026-10-05")

    def test_explicit_fmt(self):
        self.assertEqual(b.parse_date("20261029", fmt="%Y%m%d"), "2026-10-29")

    def test_invalid_day_month(self):
        self.assertIsNone(b.parse_date("32.13.2026"))

    def test_not_a_date(self):
        self.assertIsNone(b.parse_date("Saldo"))

    def test_empty(self):
        self.assertIsNone(b.parse_date("   "))


SPARKASSE_TEXT = (
    '"Kontonummer";"Buchungstag";"Valutadatum";"Buchungstext";'
    '"Verwendungszweck";"Empfänger";"Betrag";"Währung";\n'
    'DE02120300000000202051;29.10.2026;29.10.2026;Lastschrift;'
    'REWE SAGT DANKE  1234;REWE Markt GmbH;-42,13;EUR;\n'
    'DE02120300000000202051;28.10.2026;28.10.2026;Gutschrift;'
    'Gehalt Oktober;Arbeitgeber AG;2.450,75;EUR;\n'
    ';;;Alter Kontostand;;;1.000,00;EUR;\n'
    'DE02120300000000202051;27.10.2026;27.10.2026;Kartenzahlung;'
    'Café Müller;Café Müller;-4,50;EUR;\n'
)

REVOLUT_TEXT = (
    "Started Date,Description,Amount,Balance (+/-)\n"
    "2026-10-01,Coffee Shop,-3.40,152.60\n"
    "2026-10-02,Salary,2000.00,2152.60\n"
)


class TestCleanRows(unittest.TestCase):
    def clean(self, text, **kw):
        rows = list(csv.reader(io.StringIO(text), delimiter=";"))
        return b.clean_rows(rows, delimiter=";", encoding="test", **kw)

    def test_sparkasse_full(self):
        header, rows, report = self.clean(SPARKASSE_TEXT)
        self.assertEqual(header[:3], ["date", "payee", "amount"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0], "2026-10-29")
        self.assertEqual(rows[0][1], "REWE SAGT DANKE 1234")
        self.assertEqual(rows[0][2], "-42.13")
        self.assertEqual(rows[1][2], "2450.75")
        self.assertEqual(report["kept"], 3)
        self.assertEqual(report["junk"], {"no date": 1})
        self.assertIn("Buchungstag", report["mapping"]["date"])
        self.assertIn("Betrag", report["mapping"]["amount"])

    def test_repeated_header_dropped(self):
        text = SPARKASSE_TEXT.replace(
            ';;;Alter Kontostand;;;1.000,00;EUR;\n',
            '"Kontonummer";"Buchungstag";"Valutadatum";"Buchungstext";'
            '"Verwendungszweck";"Empfänger";"Betrag";"Währung";\n')
        _, rows, report = self.clean(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(report["junk"], {"repeated header": 1})

    def test_blank_rows_dropped(self):
        text = SPARKASSE_TEXT.replace(
            ';;;Alter Kontostand;;;1.000,00;EUR;\n', ';;;;;;;\n')
        _, rows, report = self.clean(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(report["junk"], {"blank": 1})

    def test_extras_pass_through(self):
        header, rows, _ = self.clean(SPARKASSE_TEXT)
        self.assertIn("Währung", header)
        wcol = header.index("Währung")
        self.assertEqual(rows[0][wcol], "EUR")

    def test_unused_extra_dropped(self):
        text = SPARKASSE_TEXT.replace(
            '"Kontonummer";"Buchungstag"', '"Unbenutzt";"Buchungstag"')
        text = (text
                .replace('DE02120300000000202051;29.10.2026', ';29.10.2026')
                .replace('DE02120300000000202051;28.10.2026', ';28.10.2026')
                .replace('DE02120300000000202051;27.10.2026', ';27.10.2026'))
        header, _, _ = self.clean(text)
        self.assertNotIn("Unbenutzt", header)

    def test_headerless_content_mapping(self):
        text = ("29.10.2026;REWE Markt;-42,13\n"
                "28.10.2026;Gehalt;2450,75\n")
        header, rows, report = self.clean(text)
        self.assertEqual(header[:3], ["date", "payee", "amount"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2], "-42.13")

    def test_headerless_skips_balance_if_two_numeric_cols(self):
        text = ("29.10.2026;REWE;-42,13;50,00\n"
                "28.10.2026;Gehalt;2450,75;60,00\n")
        header, rows, _ = self.clean(text)
        self.assertNotIn("balance", header[:4])

    def test_no_mapping_raises(self):
        with self.assertRaises(b.BankcsvError):
            self.clean("foo;bar\nbaz;qux\n")

    def test_decimal_override(self):
        text = "2026-10-01 1,234\n2026-10-02 5,67\n"
        rows = [ln.split(" ") for ln in text.splitlines()]
        header, out, _ = b.clean_rows(rows, decimal=",", delimiter=" ",
                                      encoding="test")
        self.assertEqual(out[0][2], "1.23")


class TestCLI(unittest.TestCase):
    """CLI tests run inside a per-test temp dir. Every path goes through
    _path(), which resolves it and asserts containment — nothing escapes
    the temp workspace."""

    def run_cli(self, argv):
        err = io.StringIO()
        out = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = b.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _path(self, name):
        root = Path(tempfile.mkdtemp()).resolve()
        old = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, old)
        self.addCleanup(lambda p=root: __import__("shutil").rmtree(p, ignore_errors=True))
        p = (root / name).resolve()
        if p != root and root not in p.parents:
            raise ValueError(f"path escapes temp workspace: {p}")
        return p

    def test_clean_to_stdout(self):
        path = self._path("in.csv")
        path.write_bytes(SPARKASSE_TEXT.encode("cp1252"))
        code, out, err = self.run_cli(["clean", str(path)])
        self.assertEqual(code, 0)
        rows = list(csv.reader(io.StringIO(out)))
        self.assertEqual(rows[0][:3], ["date", "payee", "amount"])
        self.assertEqual(len(rows), 4)
        self.assertIn("3 kept", err)

    def test_clean_to_file_and_preview(self):
        path = self._path("in.csv")
        outp = self._path("out.csv")
        path.write_bytes(REVOLUT_TEXT.encode("utf-8"))
        code, out, _ = self.run_cli(["clean", str(path), "-o", str(outp),
                                     "--preview", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        rows = list(csv.reader(outp.read_text(encoding="utf-8").splitlines()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][3], "balance")

    def test_probe(self):
        path = self._path("in.csv")
        path.write_bytes(SPARKASSE_TEXT.encode("cp1252"))
        code, out, _ = self.run_cli(["probe", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("cp1252", out)
        self.assertIn("';'", out)
        self.assertIn("Buchungstag", out)

    def test_extension_blind(self):
        path = self._path("statement.txt")
        path.write_bytes(SPARKASSE_TEXT.encode("cp1252"))
        code, out, _ = self.run_cli(["probe", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("cp1252", out)
        self.assertIn("Buchungstag", out)

    def test_binary_rejected_regardless_of_extension(self):
        path = self._path("in.csv")
        path.write_bytes(b"PK\x03\x04" + bytes(400))
        code, _, err = self.run_cli(["probe", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("binary", err)

    def test_utf16_csv_still_accepted(self):
        path = self._path("in.csv")
        path.write_bytes(REVOLUT_TEXT.encode("utf-16"))
        code, out, _ = self.run_cli(["probe", str(path)])
        self.assertEqual(code, 0)
        self.assertIn("utf-16", out)

    def test_missing_file(self):
        path = self._path("nope.csv")
        code, _, err = self.run_cli(["clean", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("error", err)

    def test_no_cmd(self):
        code, _, _ = self.run_cli([])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
