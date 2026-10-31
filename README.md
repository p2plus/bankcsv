# bankcsv

[![tests](https://github.com/p2plus/bankcsv/actions/workflows/tests.yml/badge.svg)](https://github.com/p2plus/bankcsv/actions/workflows/tests.yml)

Clean garbage bank CSV exports into boring, consistent CSVs.

Banks export CSVs like they hate you: cp1252, semicolons, decimal commas,
three date formats in one file, and a footer row that pretends to be a
transaction. bankcsv fixes that. It's a single Python file with zero
dependencies, and it never touches the network.

## Install

    pipx install bankcsv            # zero dependencies, CSV only
    pipx install bankcsv[xlsx]      # + real xlsx/xlsm workbooks (openpyxl)

or just keep `bankcsv.py` next to your scripts. Python 3.10+.

## Use

    bankcsv clean statement.csv -o clean.csv   # write file
    bankcsv clean statement.csv                # → stdout, pipe-friendly
    bankcsv probe statement.csv                # show what would be detected
    bankcsv clean statement.csv --preview 5    # first 5 rows, no file writes

Output contract: UTF-8, comma-delimited, ISO 8601 dates, dot decimals,
`date,payee,amount[,balance,…]` columns, junk rows dropped, every other
non-empty column passed through untouched.

## What it handles

| Garbage | Fix |
|---|---|
| cp1252 / UTF-16 / UTF-8(-BOM) | detected from bytes |
| `;` `\t` `\|` `,` | sniffed per file |
| `1.234,56` / `1,234.56` / `(45,00)` / `42,13-` / `1'234,56` / `€ 1.000,00` | normalized floats |
| `29.10.2026`, `10/29/2026`, `29-10-26`, `20261029`, `Oct 5, 2026`, `5. Okt. 2026` | ISO 8601 |
| German/English header soup (`Buchungstag`, `Wertstellung`, `Started Date`, …) | mapped to `date` / `payee` / `amount` / `balance` |
| no headers at all | columns classified by parsing success ratio |
| footers (`Alter Kontostand`), repeated headers, blank rows | dropped |
| xlsx/xlsm workbooks (with `bankcsv[xlsx]`) | rows read directly — see below |
| zip/image bytes with any file extension | refused loudly — detection is content-based, extensions are decoration |

## Excel input

With the optional extra (`pipx install bankcsv[xlsx]`), bankcsv reads
real workbooks. Detection is content-based as always — a workbook named
`statement.dat` works, and a zip that isn't a workbook is still refused.
What you get for the quirks:

- **Sheet choice:** banks put the transactions on the biggest sheet, so
  that's the default. Override with `--sheet NAME` or `--sheet 3`
  (1-based); a wrong name lists what's available.
- **Dates:** Excel stores dates as serial numbers with a format — they
  come back as proper ISO dates. Text dates (`29.10.2026`) inside cells
  still go through the normal date parser.
- **Numbers:** real numeric cells (`-42.13`) and locale text in cells
  (`"2.450,75"`) both normalize; `1000.0` prints as `1000.00`, not
  `1000.0`.
- **Formulas:** read as their cached values (`data_only`). A formula
  that was never computed in Excel comes out empty — honest, not a guess.
- **Padding:** trailing empty rows from the sheet's used range are
  dropped before they pollute the junk count.

Without the extra, pointing bankcsv at a workbook gives you one line:
`install the optional extra: pip install 'bankcsv[xlsx]'` — never a
stacktrace.

When a date or amount can't be parsed, the row is dropped and counted in
the stderr report — silent corruption is worse than visible loss.

    bankcsv: 5 rows in → 4 kept, 1 junk (no date: 1)

## Honest limitations

- `1,234` is ambiguous. One separator + three trailing digits + short
  integer part is treated as thousands grouping. If your data disagrees,
  force it: `--decimal ,` or `--decimal .`
- Ambiguous slash dates (`05/06/2026`) default to US month-first;
  `--dayfirst` flips that.
- Headerless balance columns are not guessed. A fee column and a balance
  column look identical from the outside — guessing would invent
  semantics. Balance is only mapped from a real header.

## Overrides

    --delimiter CHAR     force a delimiter
    --sheet SHEET        xlsx sheet by name or 1-based index
    --decimal , | .      force decimal separator
    --datefmt FMT        force a strptime format for the date column
    --dayfirst           ambiguous slash dates read day-first
    --headers yes|no     force header handling

## Self-check

    bankcsv --self-test                    # runs the test suite, exit 0 = green
    python3 -m unittest test_bankcsv -v    # 78 tests (14 need openpyxl)

## License

MIT.
