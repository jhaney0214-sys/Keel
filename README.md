# Keel

**ALM, FP&A, liquidity, profitability and pricing for credit unions and banks, from one projection, so they always agree.**

An institution's rate-risk model, budget, liquidity plan, profitability
system and pricing sheets usually live in different places, and when they
are asked the same question, such as next year's NII, they give different
answers. Keel runs one monthly projection of every position and reads all
of them from it:

- **Interest-rate risk:** NII by scenario for years 1 and 2; NEV under
  parallel shocks of ±100 to ±300bp and under curve-shape scenarios; the
  **NCUA NEV Supervisory Test** as NCUA runs it (below); the repricing
  gap; basis risk (each index, and share rates, moved against the curve);
  and a prepayment model with refinancing incentive, seasoning and burnout.
- **The plan:** a five-year income statement and balance sheet, under the
  base case and under every scenario, and under credit scenarios (moderate
  and severe recessions) with a CECL remaining-life estimate of the
  allowance set against the one on the books.
- **Liquidity:** a first-year survival horizon under a share-runoff stress,
  the plan's own funding need, the contractual gap, ratios, and which tier of
  12 CFR 741.12 applies; graded stress scenarios (severe, systemic, a run on
  uninsured balances); FHLB and discount-window capacity limited by the
  lendable value of pledgeable loans; and deposit concentration (uninsured
  share, largest members) from a member-level file.
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
- **Funds transfer pricing and product profitability:** every position
  funded at a rate matched to its own cash flows; each product's spread,
  fees, servicing cost, expected loss, allocated capital, ROA and RAROC;
  treasury's margin from the rate mismatch; all of it adding back to NII.
- **Account, member and branch profitability:** every loan, certificate and
  member share balance priced as its product is, with a fixed cost per
  account; losing accounts and break-even balances by product; members
  ranked (the whale curve, deciles, relationship type) and rolled up by
  branch.
- **Capital:** credit unions' net worth categories, risk-based capital ratio
  and CCULR for those over $500 million; banks' Tier 1 leverage, CET1, Tier 1
  and total risk-based ratios and CBLR; and net worth with the securities at
  market, today and after +300bp. Risk-weighted assets by product, with each
  product's weight set in the settings.
- **RAROC pricing calculator:** a loan's or deposit's life economics at a
  rate, the rate that earns the hurdle, and the break-even rate.
- **Deposit pricing:** the marginal cost of new money for a rate move on
  each share product (from the deposit study's balance sensitivity), and a
  certificate special's true cost once maturing certificates and share
  balances that move over are counted, against wholesale funding.
- **Investment purchases and swaps:** the realized loss, the yield pickup,
  the month the extra NII earns the loss back in the base case and at
  +/-300bp, and the whole book with and without the trade.
- **New-product spread analysis:** a proposed product's unit economics, its
  own path through the plan, and the whole book with and without it.
- **Budget:** the first plan year by month and by product, and, given an
  actuals file or the general ledger's monthly trial balance, variance
  against it split into volume and rate.
- **Ad hoc reporting:** group, filter and total the book, the core files,
  the budget or profitability; save a query and every report runs it.
- **Credit unions and banks:** `institution: bank` switches the words
  (EVE, deposits, equity), drops NCUA's tests and applies income tax.
- **Rate forecasts:** the plan and budget can run on today's curve held,
  on the curve's implied forwards, or on a management rate forecast, with
  every shock on top of it; NEV stays on today's curve.
- **Driver-based budget:** monthly new-loan volumes or target balances,
  offering rates for new business and share products, and non-interest
  income and expense by line with start months and growth.
- **Key-assumption tests:** deposit betas, decay, prepayment speeds and new
  loan spreads each moved on its own, with the effect on NII, NEV and every
  rate-risk limit.
- **Deposit study:** share betas (with their lag, and separately for
  rising and falling rates), balance sensitivity to rate spreads, core
  balances and decay, estimated from the institution's own history and set
  against the assumptions in use; the assumption tests run the model on the
  study's values too.
- **Run history and back-testing:** every run is saved; the next one shows
  the trend, every assumption changed since, and last quarter's forecast
  against what actually happened (balances, administered rates, NII).
- **Any credit union from public data:** `keel callreport` builds a
  folder from NCUA's quarterly call report files, tied to the credit union's
  reported totals and income, with peer benchmarks against its NCUA peer
  group.

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

**Bringing your own institution? Read [START-HERE.md](START-HERE.md).**
`python -m keel init "<folder>"` makes a starter folder with every input
file, whose example rows already run and tie, to replace with your own
export one file at a time. On Windows, double-click `windows\New Keel
folder.cmd` to do the same, or drag a folder onto `windows\Keel report.cmd`
or `windows\Keel app.cmd` to run the report or open the app. `pip install .`
gives a `keel` command in place of `python -m keel`; nothing else is
installed, since Keel uses only the standard library.

```bash
python tools/make_samples.py                 # three synthetic credit unions, in about two seconds
python -m keel run examples/mid-cu           # -> examples/mid-cu/report/report.html
python -m keel whatif examples/mid-cu examples/whatifs/fhlb-for-auto-growth.json
python -m keel serve examples/mid-cu         # what-if, pricing, new product, trade, explore: http://127.0.0.1:8750/
python -m keel price examples/mid-cu --product used_auto --amount 22000 --term 60 --rate 7.25
python -m keel newproduct examples/mid-cu examples/proposals/green-auto.json
python -m keel swap examples/mid-cu examples/trades/loss-swap.json
python -m keel special examples/mid-cu examples/specials/13-month-special.json
python -m keel query examples/mid-cu --by product,rate_band --measure "sum balance" --measure "wavg spread balance"
python -m keel run examples/community-bank   # the same, for a bank
python -m keel run examples/backtest-cu      # a quarter on: trend, assumption changes and the back-test
```

| Sample | Size | Shape | Supervisory test |
|---|---|---|---|
| `small-cu` | $85M | consumer lending, very liquid, just over NCUA's $50M line; CSV data, JSON settings | Low |
| `mid-cu` | $560M | close to the system's own mix; **CSV data, Excel settings** | Moderate |
| `large-cu` | $2.4B | mortgage-heavy, certificate-funded, FHLB borrowing; everything in Excel | High |
| `sample-cu` | $560M | a hand-written `positions.csv`, for reading the format | High |
| `community-bank` | $1.2B | a commercial bank: prime-based C&I, CRE balloons, construction, brokered CDs | (a bank: none) |

`mid-cu` budgets on a rate forecast with monthly loan volumes, offering
rates and expense lines, and has its general ledger's trial balance through
September (`trial_balance.csv`, mapped by `gl_map.csv`) for the budget
variance and three saved queries; `backtest-cu` is `sample-cu` a
quarter later, with its June run in `history/`; `large-cu` has two queries; `examples/proposals/` has
two new-product proposals.

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
| `loans.csv` | one per loan: product code, balance, rate, fixed/variable, index, margin, next reset, caps and floors, origination and maturity dates, amortization period for balloons, days delinquent; optionally member_id and branch |
| `certificates.csv` | one per certificate: product, balance, rate, open and maturity dates; optionally member_id and branch |
| `shares.csv` | non-maturity shares by product and balance tier: accounts, balance, rate |
| `investments.csv` | one per security: type, par, book value, coupon, book yield, maturity, next call date, WAM and CPR for MBS and CMOs, AFS or HTM |
| `borrowings.csv` | one per borrowing: lender, balance, rate, maturity |
| `gl.csv` | the trial balance the detail must tie to |
| `member_shares.csv` | optional: one per member and share product: member_id, product code, balance, branch |
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

## Any credit union, from its call report

NCUA publishes every federally insured credit union's quarterly call
report at <https://ncua.gov/analysis/credit-union-corporate-call-report-data/quarterly-data>.
Download a quarter's zip, then:

```bash
python -m keel callreport call-report-data-2026-06.zip --search "valley"
python -m keel callreport call-report-data-2026-06.zip --cu 12345 --run
```

The folder it builds has the credit union's own balances by loan, share,
investment and borrowing line, tied to its reported total assets and
liabilities; loan and share rates calibrated so they earn and cost exactly
the interest and dividends it reported; and Keel's default behaviour,
terms and costs, which the notes say plainly. `peers.json` holds twelve
ratios against its NCUA peer group, and the report shows them. It is a
first look from public data, not a substitute for the credit union's own
files. Keel knows the Treasury curve for each quarter-end from March 2025
to June 2026; for another cycle, pass `--curve` a JSON of `{tenor months:
rate}`. Pass `--prior` the previous quarter's zip to calibrate interest on
the latest quarter and take loan growth from it.

**Validated out of sample.** `keel validate` builds every credit union from
one quarter's report and scores its three-month forecast against the next:

```bash
python -m keel validate call-report-data-2025-03.zip call-report-data-2025-06.zip ... call-report-data-2026-06.zip
```

Over five quarters and 21,866 credit-union quarters, Keel's quarterly NII
has a median error of 3.2% (bias -1.1%) against 3.5% for repeating last
quarter, and is closer for 78% of credit unions over $1 billion. Balances
and net worth change one quarter out are no better than "nothing changes".
METHODOLOGY.md has the full table and what it found.

## Rate forecasts and the budget

`base_case` in the settings is `flat` (today's curve held, the default),
`forward` (implied forwards) or `forecast`, with the Forecast sheet (or
`rate_forecast` in JSON) giving rates by month and tenor. Shocks sit on top
of the base case, and a `rates unchanged` scenario shows the plan without
it. The Drivers sheet sets, by product and month (a number, or YYYY-MM),
a new-business `volume`, a month-end `balance`, or an offering `rate`; the
Noninterest sheet itemizes fee income and operating expense with growth and
a start month. Between and after drivers, products grow at their planned
rate from where the last driver left them.

## Credit stress and CECL

The plan runs under credit scenarios that multiply every loan product's
charge-off rate for a stressed period and phase it back (defaults: x2 for 12
months, x3.5 for 18; or the CreditScenarios sheet: name, multiplier, months,
reversion_months). The report shows losses, two-year net income, the net
worth ratio's low, and the CECL allowance build each forecast forces at
once, next to a remaining-life estimate of the allowance compared with the
booked one (any negative contra-asset position).

## Liquidity scenarios, collateral and concentration

- **Scenarios:** the Stresses sheet (or `liquidity.stresses` in JSON) lists
  scenarios by name with `runoff_multiplier`, `haircut_add` (points),
  `contingent_available` (percent), `months` and `uninsured_runoff`
  (percent). Without it, Keel runs four: as configured, severe, systemic and
  an uninsured run. Each is checked against the survival limit.
- **Collateral:** mark a contingent source `secured` (a column in the
  Contingent sheet) and give pledgeable loan products a `collateral_value`
  (percent lent against). The line then counts at the smaller of its unused
  amount and the collateral left after existing borrowings.
- **Concentration:** put `depositors.csv` (member_id, balance) in the folder
  for the uninsured share (limit `uninsured_shares_max`, default 15%) and the
  largest members' share. Without it, certificates over $250,000 give a floor.

## The deposit study

Put `deposit_history.csv` (month, product, balance, rate, market_rate, one
row per share product per month) in the folder, and optionally
`deposit_accounts.csv` (month, account_id, product, balance). The report's
Deposit study section estimates each product's beta and lag, up and down
betas, runoff per 100bp of spread, core balance and, from the accounts,
decay; flags where the assumptions differ; and lists the values the study
supports in the settings' units. It changes nothing by itself. `mid-cu` has
five years of synthetic history whose true behaviour is set slightly away
from its assumptions, and the study finds the gap in money-market decay.

## Key assumptions, history and back-testing

Every `keel run` on a folder tests each key assumption (`--quick` skips
it) and saves its results to `<folder>/history/<date>.json`. When an
earlier run is there, the report adds the trend, the assumption change log
and a back-test of the earlier forecast against today's book and the actuals
(`actuals.csv` or the trial balance).

## Profitability, capital and pricing

Each product in the settings can carry `servicing_cost`, `fee_yield`,
`origination_cost` (percent of balance, or of the amount for origination)
and `risk_weight` (percent). The settings hold `target_capital` (capital per
dollar of risk-weighted assets, default 10%), `hurdle_rate` (default 12%)
and `tax_rate` (0% for a credit union, 21% for a bank unless set). Without a
risk weight a product counts at 100% (20% for a liquid investment, 0% for
cash and liabilities).

The report's Profitability section shows each product's spread over its
FTP, its costs and RAROC, and how the products' spreads plus treasury's
margin make up net interest income. `keel price` and the Pricing page price
one deal; `keel newproduct` and the New product page analyse a launch.

`keel swap <folder> <trade.json>` and the Trade page analyse an investment
purchase or swap. A trade lists securities to `sell` (by security id, the
whole position unless `share` says less, at Keel's market value unless
`price` gives one in percent of book) and to `buy` (product, `amount` in
dollars or `"proceeds"`, `yield` or `spread`, term, amortization). The page
shows the realized gain or loss and its effect on net worth, the yield
pickup, duration and average life before and after, the earn-back month in
each scenario with the cumulative extra NII, and every measure and limit
with and without the trade. `examples/trades/` has a loss swap and a
Treasury purchase from cash.

## Deposit pricing

The Profitability section's deposit-pricing table gives, for each share
product, the marginal cost of new money for a move of -50 to +100bp: the
extra interest a year (every dollar already there reprices) over the extra
balance it brings, using the deposit study's balance sensitivity where its
fit is usable and the settings' `runoff_per_100bp` otherwise, against
twelve-month wholesale money.

`keel special <folder> <special.json>` prices a certificate special:

```json
{"name": "13-month special", "product": "certificates", "rate": 4.35, "term_months": 13,
 "window_months": 3, "volume": 25000000, "maturing_renewal": 70,
 "sources": {"new_money": 45, "money_market": 30, "regular_shares": 25}}
```

Certificates maturing in the window that would have renewed anyway
(`maturing_renewal` percent) move from the standard renewal rate to the
special; the rest of the volume splits by `sources`. The page shows where
the money comes from, the incremental interest, the marginal cost of the
new money against wholesale of the same term, the new-money share needed to
beat borrowing, and a grid by rate and new-money share.

## Accounts, members and branches

With core files in `data/`, the report's Members section prices every loan
and certificate as its product is priced: the spread over the funds
transfer price of the pool it joined, the capital credit, fees, servicing,
expected loss, and each product's `account_cost` (dollars a year per
account, set on the Products sheet). The accounts add back to their
products, and a reconciliation check says so. By product it shows how many
accounts lose money and the balance at which an average account breaks
even.

Add `member_id` and `branch` columns to `loans.csv` and `certificates.csv`,
and `data/member_shares.csv` (member_id, product_code, balance, optionally
branch) for share balances, and it ranks members: the whale curve, deciles,
borrower-and-saver against saver-only and borrower-only, and the same by
branch (a member's branch is the one holding most of their balance). Every
account and member is queryable: `keel query <folder> --table accounts` or
`--table members`.

## Basis risk and prepayment

Indexes can run on their own curves (the Indexes sheet's `curve` column)
and follow the main curve by a `beta`; a scenario on the Scenarios sheet can
carry a `basis` such as `PRIME:-75, shares:40` (large-cu has one: +200bp
with prime lagging and share rates leading). The Interest-rate risk section
adds a basis-risk table: each index moved ±50bp against the curve and share
rates ±25bp, alone, with the change in NII.

For mortgages and other prepayable loans, set `refi_incentive` on a product
and its speed follows each loan's own rate against what the product lends
at today, instead of the curve's move; `burnout` takes that extra speed
away the longer loans sit in the money, and `seasoning_months` ramps new
loans up. large-cu's mortgages run on it.

## Budget and actuals

The Budget section is the base plan's first year by month. Put
`actuals.csv` (or .xlsx) in the folder, with `month` (YYYY-MM), `line` (a
product, or `fee_income`, `operating_expense`, `credit_losses`,
`income_tax`), `average_balance` and `amount`, and the report adds
year-to-date variance by product, split into volume and rate.

Or skip the hand-built file and hand over the general ledger. Put its
monthly trial balance in the folder as `trial_balance.csv` (`month`,
`account`, `balance`, natural signs: debits positive, credits negative) and
map the accounts once in `gl_map.csv`:

```
account,line,measure
1210,used_auto,balance        month-end balance of a product
4110,used_auto,ytd            its interest, year to date
5300,certificates,ytd         dividends paid
4400,fee_income,activity      the month's activity alone
6000,operating_expense,ytd
```

`measure` is `balance`, `activity` or `ytd`; year-to-date accounts are
differenced month over month and restart in the settings'
`fiscal_year_start` month (default 1, January). Several accounts can map to
one line; accounts the map leaves out are ignored. Include the analysis
date's month, so the first month has an opening balance and a prior
year-to-date: the reconciliation then ties that month's balances to the
positions, product by product. One source of actuals per folder: both a
trial balance and `actuals.csv` is refused.

## Ad hoc reports

```bash
python -m keel query <folder> --table loans --by product_code --measure count --measure "sum current_balance" --where "days_delinquent >= 60" --out delinquent.xlsx
```

Tables: `positions` (with FTP, spread, risk weight, capital, term and rate
bands), `profitability`, `budget`, `projection`, and each core file. Save a
query as JSON in `<folder>/queries/` (or with the Explore page's Save
button) and every report and `results.xlsx` include it.

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
python -m unittest discover -s tests      # 198 tests
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
