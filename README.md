# Keel

**ALM, the plan and liquidity for credit unions, from one projection, so the three always agree.**

A credit union's rate-risk model, budget and liquidity plan usually live in
different places, and when they are asked the same question, such as next
year's NII, they give different answers. Keel runs one monthly projection of
every position and reads all three from it:

- **Interest-rate risk:** NII by scenario for years 1 and 2; NEV under
  parallel shocks of ±100 to ±300bp and under curve-shape scenarios; the
  **NCUA NEV Supervisory Test** as NCUA runs it (below); and the repricing
  gap.
- **The plan:** a five-year income statement and balance sheet, under the
  base case and under every scenario.
- **Liquidity:** a first-year survival horizon under a share-runoff stress,
  the plan's own funding need, the contractual gap, ratios, and which tier of
  12 CFR 741.12 applies.
- **Portfolios:** investments by type with market value, unrealized gain or
  loss, WAL and effective duration; the largest holdings; loans by product;
  the certificate maturity ladder.
- **Reconciliation:** checks run on every report that the three really are
  one model, and that every detail file ties to the general ledger.
- **Policy limits:** the board's limits on NII, NEV, net worth, liquidity
  and funding, each marked within, near or breach (with an icon and a word,
  never colour alone). Limits not set are Keel's defaults, and say so.
- **What-ifs:** change an assumption or the balance sheet and see every
  measure and limit move, side by side with the base.

The report opens with the findings in plain words, then the limits, then the
detail, with a chart above each table that holds its exact numbers. It prints
as a board packet (one section a page), follows the system's light or dark
mode, and has an Excel twin, `results.xlsx`, with every table in raw dollars
so formulas built on it add up to the cent.

**It runs on the credit union's own machine and sends nothing anywhere.**
Standard-library Python 3.8+, no dependencies, no network calls. Every
assumption is printed in the report, and [`METHODOLOGY.md`](METHODOLOGY.md)
is written for the model validator.

## Try it

```bash
python tools/make_samples.py                 # three synthetic credit unions, in about two seconds
python -m keel run examples/mid-cu           # -> examples/mid-cu/report/report.html
python -m keel whatif examples/mid-cu examples/whatifs/fhlb-for-auto-growth.json
python -m keel serve examples/mid-cu         # a what-if page at http://127.0.0.1:8750/
```

| Sample | Size | Shape | Supervisory test |
|---|---|---|---|
| `small-cu` | $85M | consumer lending, very liquid, just over NCUA's $50M line; CSV data, JSON settings | Low |
| `mid-cu` | $560M | close to the system's own mix; **CSV data, Excel settings** | Moderate |
| `large-cu` | $2.4B | mortgage-heavy, certificate-funded, FHLB borrowing; everything in Excel | High |
| `sample-cu` | $560M | a hand-written `positions.csv`, for reading the format | High |

The generated three are written as a core system exports them, calibrated to
NCUA's Quarterly Credit Union Data Summary for 2026Q2. **Every figure is
invented** from a fixed seed; none describes a real institution.

## Inputs

**Every input can be a CSV or an Excel workbook (.xlsx)**: the settings, the
product map, each data file, and `positions`. The usual arrangement, CSV data
with an Excel settings workbook, works as it is, and so does all-CSV or
all-Excel. The samples use all three. Excel files are read with the standard
library, so there is still nothing to install. Dates are read whether Excel
stored them as dates or someone typed them as text. A folder holding both
`loans.csv` and `loans.xlsx` is refused rather than guessed between. An old
`.xls` needs saving as `.xlsx` first.

**From core-system files** (a folder with `data/`):

| File | Rows |
|---|---|
| `loans.csv` | one per loan: product code, balance, rate, fixed/variable, index, margin, next reset, caps and floors, origination and maturity dates, amortization period for balloons, days delinquent |
| `certificates.csv` | one per certificate: product, balance, rate, open and maturity dates |
| `shares.csv` | non-maturity shares by product and balance tier: accounts, balance, rate |
| `investments.csv` | one per security: type, par, book value, coupon, book yield, maturity, next call date, WAM and CPR for MBS and CMOs, AFS or HTM |
| `borrowings.csv` | one per borrowing: lender, balance, rate, maturity |
| `gl.csv` | the trial balance the detail must tie to |
| `product_map.json` or `.xlsx` | core product codes to Keel products, and the GL accounts for cash, fixed assets, the NCUSIF deposit, other assets, the allowance and other liabilities (as a workbook: one sheet per section) |

The importer pools loans and certificates that behave alike (same product,
rate type, index and margin, reset timing, remaining term within 6 months and
rate within 25bp) at their balance-weighted rate. It keeps each security and
borrowing on its own, and writes the pools to `report/positions_imported.csv`.
It ties every detail file to its GL line before modelling anything.

**Or directly:** `positions.csv`, one row per instrument or pool; see
`examples/sample-cu`.

**Both need settings**, as `assumptions.xlsx` or `assumptions.json`. The
workbook has one sheet per kind of setting (Settings, Curve, Indexes,
Products, Scenarios, Contingent, Notes), in the same units as the JSON, and
`python -m keel convert` turns either into the other. A misspelled column or
a value that is not a number is refused with its sheet, product and column.
The settings cover the curve and indexes; each product's
behaviour (prepayment and its rate sensitivity, share decay and beta,
new-business term and spread, NEV discount spread, planned growth,
charge-offs, liquidity haircut and stress runoff, call threshold); income and
expense; extra scenarios, including curve shapes; and the contingent
liquidity sources. The samples' `notes` explain the units.

Inputs that would make the projection wrong rather than merely odd stop the
run with the file and row: an unmapped product code, a term loan without a
term, a balloon shorter than its term, a duplicate id, a product with no
assumptions, a what-if path that does not exist.

## Policy limits

The `Limits` sheet of the settings workbook (or `"limits"` in
`assumptions.json`) holds the board's limits; a blank uses Keel's default.

| Key | Limit | Default |
|---|---|---|
| `nii_decline_300` | Year-one NII decline, worst of ±300bp, at most | 15% |
| `nii_decline_200` | Year-one NII decline, worst of ±200bp, at most | 10% |
| `nev_decline_300` | NEV decline on own assumptions, worst of ±300bp, at most | 40% |
| `nev_ratio_min` | NEV ratio on own assumptions after the worst ±300bp, at least | 6% |
| `net_worth_min` | Net worth ratio, lowest month of the base plan, at least | 7% |
| `liquid_to_shares_min` | Cash and liquid investments to shares, at least | 15% |
| `loans_to_shares_max` | Loans to shares, at most | 95% |
| `borrowings_to_assets_max` | Borrowings to assets, at most | 25% |
| `survival_months_min` | Months liquidity lasts under the stress, at least | 6 |
| `warning_band` | How close counts as near, percent of the limit | 10% |

An unknown key stops the run, so a misspelled limit is never silently the
default.

## What-ifs

A what-if is a small JSON file: `assumptions` changed by dotted path (in
the file's own units), and `actions` on the balance sheet (`add` a position,
`scale` a product). Actions settle through cash as the real transaction would:
a sale is priced at market and realizes its gain or loss, and a borrowing
that names its `draws_on` source uses up that much contingent capacity. See
`examples/whatifs/` for three.

`python -m keel serve <folder>` puts the same thing in the browser: the
levers an ALCO asks about (planned growth, share betas and decay, prepayment
speeds, a new borrowing, a sale), prefilled with today's assumptions, with
base and what-if side by side (every measure and every limit, with the
limits that change status called out) and the JSON to keep. It listens on 127.0.0.1
only, so nothing outside the computer can reach it.

## The NCUA NEV Supervisory Test

Run as NCUA describes it. Non-maturity shares are priced at the standardized
**99.00 in the base case and 95.04 at +300bp**, whatever the credit union's
own assumptions say, and every other position keeps its modelled value. The
two ratings are the post-shock NEV ratio and the **NEV percent change**, on
Letter SL 22-01's thresholds. The report shows the credit union's
own-assumption NEV beside it, because the two can disagree sharply: on
`sample-cu` the own-assumption view says Low and the supervisory test High.

## Tests

```bash
python -m unittest discover -s tests      # 70 tests
```

Most pin an answer known independently of Keel: a textbook mortgage payment,
a bond yielding its discount rate valued at par, a 10bp move from a 10% beta,
NCUA's standardized share prices, SL 22-01's bands and 741.12's tiers. Others
pin behaviour that was once wrong: FHLB stock valued as a perpetuity, an
advance that raised stress liquidity, a sale at book that hid its loss. One
breaks the balance sheet on purpose to show the reconciliation catches it.

## Status

A working prototype, built 2026-09-27 and run only on synthetic data. **It
has not been compared against a production ALM model.** That comparison, on
a real credit union's files, is the next test that matters. What it does not
do yet is at the end of [`METHODOLOGY.md`](METHODOLOGY.md).
