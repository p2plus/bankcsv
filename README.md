# bankcsv

Clean garbage bank CSV exports into boring, consistent CSVs.

Banks export CSVs like they hate you: cp1252, semicolons, decimal commas,
three date formats in one file, and a footer row that pretends to be a
transaction. bankcsv fixes that. It's a single Python file with zero
dependencies, and it never touches the network.

## Install

    pipx install bankcsv

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
| zip/xlsx/image bytes with any file extension | refused loudly — detection is content-based, extensions are decoration |

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
    --decimal , | .      force decimal separator
    --datefmt FMT        force a strptime format for the date column
    --dayfirst           ambiguous slash dates read day-first
    --headers yes|no     force header handling

## Self-check

    bankcsv --self-test                    # runs the test suite, exit 0 = green
    python3 -m unittest test_bankcsv -v    # 61 tests

## License

MIT.
