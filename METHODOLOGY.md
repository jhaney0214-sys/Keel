# Keel methodology

Written for the person who has to validate the model: what every number is,
how it is computed, and what it assumes. Every statement here can be checked
against the code it names and against the tests in `tests/test_keel.py`.

## One projection, two modes

`keel/engine.py` moves every position forward one month at a time under a
rate scenario. Two modes read the same monthly step:

| Mode | New business | Used for |
|---|---|---|
| **Runoff** | none: today's positions to the end | NEV; the contractual liquidity gap |
| **Going concern** | balances follow the plan | NII; the FP&A statements; the liquidity stress |

Because ALM, the plan and liquidity all read one step function, they cannot
use different prepayment speeds, share rates or balances. The reconciliation
section tests that they did not.

## The monthly step, per position

**Repricing, at the start of the month.**
- *Fixed:* unchanged.
- *Variable:* at each reset (`age` a positive multiple of `reset_months`),
  the rate becomes index + margin, clamped to the position's floor and cap.
  The index is the scenario curve at the index's tenor plus its spread.
- *Administered* (non-maturity shares): rate = opening rate + beta ×
  (scenario short rate − base short rate), floored at the product's
  `rate_floor`. The short rate is the curve at `short_tenor_months`.

**Interest:** balance × annual rate ÷ 12.

**Principal:**
- *Level:* the payment that amortizes the balance over the remaining term at
  the current rate, `B·r / (1 − (1+r)^−n)` with `r` the monthly rate;
  principal is payment − interest. Recomputed each month, so a variable-rate
  loan re-amortizes after a reset. Checked: $100,000, 30 years, 6% pays
  $599.55.
- *Bullet:* the whole balance at maturity.
- *Non-maturity:* decay, balance × SMM(runoff).

**Prepayment** (level only): (balance − scheduled principal) × SMM(CPR),
where SMM(x) = 1 − (1 − x)^(1/12), the monthly rate that compounds to the
annual one. CPR in a scenario = base CPR − `cpr_per_100bp` × (shift ÷ 100bp),
bounded by `cpr_floor` and `cpr_cap`: rates falling 100bp adds
`cpr_per_100bp`. Decay moves the other way: `runoff_per_100bp` is added per
100bp *rise*.

**The prepayment model**, per product, when set. With `refi_incentive`, the
speed follows each loan's own incentive instead of the curve's move: CPR =
`cpr` + `cpr_per_100bp` × (the loan's rate − the product's rate today, in
points), where the product's rate today is the scenario curve at `new_term`
plus `spread`, what the borrower could refinance into. A 3% mortgage with
the market at 6.5% is locked in at the floor whatever the shock; a 7.5%
mortgage speeds up as soon as rates fall. **Burnout:** a loan more than 50bp
in the money counts its months there, and the incentive's extra speed is
multiplied by exp(−`burnout` × months ÷ 12): the borrowers who could
refinance and would have already have. **Seasoning:** with
`seasoning_months`, CPR is scaled by (loan age ÷ `seasoning_months`) until
it reaches full speed; the importer gives each pool its balance-weighted age
from the origination dates, new business starts at zero, and a position
with no age is taken as seasoned.

**Net charge-offs** (assets): the remaining balance × annual charge-off ÷ 12,
written off with no cash received.

## Going concern

For each product, the planned balance in month *t* is its opening balance ×
(1 + growth)^(t/12). After the step, the gap between plan and what remains is
new business:

- *Term products* (`new_term` > 0): a new position of that size, at the
  scenario curve at `new_term` plus the product's `spread` (fixed), or index
  plus the largest existing position's margin (variable).
- *Pooled non-maturity products* (`new_term` = 0 and non-maturity): the pool
  itself grows or shrinks to plan. Money in or out passes through cash.
- *A term product with `new_term` = 0* is not renewed: it runs off, and cash
  or overnight borrowing takes its place. The sample's FHLB advance works this
  way.

**Cash** is the settlement account. Each month:

```
cash += asset principal received − new assets funded
      − liability principal paid + new liability money
      + interest income − interest expense + fee income − operating expense
```

Cash earns the short rate. Below `cash_minimum` the shortfall is borrowed
overnight at the short rate + `overnight_spread`, and repaid when cash allows.
**Equity moves only by net income**, where net income = interest income −
interest expense + fee income − operating expense − credit losses. So assets
= liabilities + equity each month by construction, and the reconciliation
confirms it.

Dividends on shares are treated as paid in cash rather than credited to the
account. The two are identical for income and equity; they differ only in
where the cash sits, which matters little over a month.

## Net economic value

In runoff mode, each position's cash flows (interest + principal, excluding
charge-offs, which are losses) are discounted monthly:

```
PV = Σ CF_k / (1 + y_k/12)^k,   y_k = scenario curve at k months on the analysis date + discount_spread
```

A non-maturity position still open at `nev_max_months` pays its remaining
balance then. Cash and positions with neither a rate nor an amortization
count at book. NEV = PV assets − PV liabilities, and the **NEV ratio** = NEV ÷
PV assets. Both follow NCUA's Examiner's Guide.

A 5% bullet discounted on a flat 5% curve prices at par to the cent (tested).

**No-maturity positions** (FHLB stock, a CUSO stake) count at book. They are
redeemed at par, and discounting a 7% dividend as a perpetuity once valued
FHLB stock at 150% of book.

**NCUA NEV Supervisory Test.** Run as NCUA describes it: every non-maturity
share is priced at **99.00 in the base case and 95.04 at +300bp** (a 1%
benefit, then a further 4% decline), replacing the credit union's own share
assumptions; every other position keeps its modelled value. The +300bp shock
is instantaneous, parallel and sustained. The ratings, from Letter SL 22-01,
are on the post-shock NEV ratio (above 7% low, 4–7% moderate, below 4% high)
and the **NEV percent change** (a decline below 40% is low, 40–65% moderate,
above 65% high). The decline in the ratio itself is shown for reference, not
rated. Sources: NCUA's supervisory framework letter and Examiner's Guide, and
ALM First's description of the standardized prices, read 2026-09-27.

The report also shows NEV on the credit union's own assumptions, under every
instantaneous scenario including curve shapes. The two views can disagree
sharply, and seeing both is the point.

## Liquidity

- **Survival** is the first month *within the first twelve* that available
  liquidity is negative under the stress. Beyond a year, the stress's frozen
  share balances describe a different plan, not a stress. On the large
  sample, a "survival" of month 48 came from plan growth alone.
- **The plan's own funding need** is the peak overnight borrowing in the
  base plan, with the month it occurs.
- **Stress** (going concern, `stress=True`). For `stress_months`, liability
  products take in no new money and lose `stress_runoff` of their opening
  balance, spread evenly across the months, on top of ordinary decay. After
  that they are held where the stress left them. Loans keep funding to plan.
  **Available liquidity** = cash above the minimum + liquid investments ×
  (1 − haircut) + contingent capacity − overnight borrowing drawn. The
  survival horizon is the first month it is negative.
- **Contractual gap:** asset inflows less liability outflows from today's
  positions in runoff mode, monthly and cumulative.
- **12 CFR 741.12 tier** from total assets: under $50M, a policy and a list
  of contingent sources; $50M and up, a contingency funding plan; $250M and
  up, also access to a contingent federal liquidity source.

## Scenarios

Parallel shocks of ±100, ±200 and ±300bp, instantaneous and held; ramps
reaching their move over a stated number of months; and **shapes**, a move
given at chosen tenors and interpolated between them (the samples carry a
flattener, a steepener and a short-end +200bp). In a shaped scenario each
rate moves by the shift at its own tenor. Prepayment responds to the ten-year
point, which mortgage rates follow, and share decay to the short rate, which
members compare. Rates are floored at `rate_floor`.

**Indexes and basis.** An index is the main curve at its tenor plus its
spread, or its own curve (the Indexes sheet's `curve` column, "1:7.5,
12:7.25"), and it moves by its `beta` (percent, default 100) times the main
curve's move at its tenor. A scenario can carry a `basis` ("PRIME:-50,
shares:25"): an extra move for a named index, or for every administered
share rate, reached over the scenario's ramp. The report's basis-risk table
(`keel/basis.py`) moves each index that variable positions reset to by
±50bp against the curve, and share rates by ±25bp, one at a time with
everything else on the base plan, and gives the change in year-one and
year-two NII.

## Instruments

Beyond level, bullet and non-maturity positions:
- **Balloon:** amortizes as if over `amort_months`, prepays like a level
  loan, and is due in full at maturity.
- **Callable:** due at maturity. From its first call date it is called in any
  month when its coupon exceeds the scenario rate for its remaining term,
  plus the product's spread, by more than `call_threshold`.
- **ARMs** reset first at `next_reset_months`, then every `reset_months`.

## Reading CSV and Excel

`keel/xlsx.py` reads .xlsx with the standard library. It handles numbers,
text in the shared-strings table or inline, rich-text runs, booleans, and the
cached value of a formula; it ignores styles, charts and comments. Because a
cell's display format decides whether Excel shows a day count as a date,
columns named `*_date` (and the `as_of` setting) are converted from Excel's
1899-12-30 epoch, and dates typed as text (`2026-06-30` or `6/30/2026`) are
accepted too. Checked 2026-09-27 against Microsoft Excel 16 in both
directions. Excel opened every workbook Keel writes, including a 105,100-row
loan file, without repair. A credit union's CSV and settings workbook
re-saved by Excel (shared strings, dates as day counts) gave the same NII and
NEV to the dollar.

## Importing core-system files

`keel/importer.py` reads loans, certificates, tiered shares, securities,
borrowings and the trial balance.
- **Loans** pool on product, rate type, index and margin, next-reset year,
  remaining term within 6 months, rate within 25bp, and balloon or not.
  Revolving loans (no maturity date) pool as non-maturity assets.
- **Certificates** pool on remaining term and rate.
- **Share tiers** each become a position.
- **Securities and borrowings** stay one position each. A security's balance
  is its book value and its rate is its book yield. MBS and CMOs amortize
  over their WAM.
- **GL lines** supply cash, fixed assets, the NCUSIF deposit, other assets,
  other liabilities and the allowance. The allowance is a negative,
  non-earning asset: the one position allowed below zero.

Before modelling, each detail file is tied to its GL line (loans, investments
at book, certificates, non-maturity shares, borrowings), and each tie is a
reconciliation check.

## Portfolio measures

- **Investments:** market value is the base-scenario PV. Unrealized gain or
  loss is market value less book. WAL is the principal-weighted average month
  of the base runoff, in years. Effective duration = (PV(−100bp) −
  PV(+100bp)) ÷ (2 × PV × 1%).
- **Repricing gap:** variable positions count in full at their next reset,
  and everything else by its base-scenario principal flows, in bands from 0–3
  months to over 10 years.

## Policy limits and the report

`keel/results.py` computes every number the report shows, once; the HTML
(`report.py`), its charts (`charts.py`) and the Excel workbook (`export.py`)
only format it. Each limit is measured as follows:

- NII declines: year-one NII in the parallel shock against the base, worst of
  the pair.
- NEV decline and NEV ratio: own assumptions (not the supervisory prices),
  worst of +300 and −300bp.
- Net worth: the lowest month-end ratio of the five-year base plan.
- Liquidity ratios: today's balance sheet.
- Survival: the stress month in which available liquidity first goes
  negative, less one. Liquidity that lasts the measured year has no number
  and passes any limit up to twelve months.

A maximum is *near* when the value is within `warning_band` percent of the
limit below it, and a minimum when within that band above it. The what-if
page measures the same limits the same way from its own runs; a test holds
the two to the same values.

## Funds transfer pricing and profitability

`keel/profitability.py`. Every position is funded at a rate matched to its
own base-scenario runoff (the strip-funding method):

    FTP = sum(P_t * t * r(t)) / sum(P_t * t)

where P_t is the principal (with charge-offs) paid in month t and r(t) the
base curve at t months. A variable-rate position is funded at the curve at
its next reset; cash at the short rate; positions with no rate and no term
are not funded. Non-maturity shares are funded by their own decay flows.

Profitability is a run-rate on today's balances: for an asset, interest at
its rate less FTP is its spread; for a liability, FTP less its rate. Each
product adds `fee_yield` and subtracts `servicing_cost` and, for assets,
`charge_off` as expected loss. Capital is `risk_weight` x `target_capital`
x balance, and the product is credited for that capital at its FTP rate.
Tax is `tax_rate` of pre-tax income. RAROC = net income / capital.
Treasury's margin is what remains of NII, so that

    sum of spreads + sum of capital credits + treasury = run-rate NII

and a reconciliation check holds every report to it. Fees and operating
expense not carried by products are shown as unallocated, so the run-rate
reaches the institution's net income.

**Accounts and members** (`keel/accounts.py`). The importer keeps every
loan and certificate row with the pool it joined, and each row of
`member_shares.csv` with the share tier its balance falls in. A row's P&L is
its product's, at its own balance and rate and its pool's FTP rate:

    contribution = spread + capital credit + fees - servicing - account_cost - expected loss

A pool's rate is its rows' balance-weighted rate and its FTP applies to all
of them, so the loan and certificate rows add up exactly to their products'
interest and FTP; the check allows $1. `account_cost` is a fixed dollar cost
per account and is not in the product view: it allocates part of the
operating expense the products' servicing leaves unallocated, and the report
says how much. A product's break-even balance is its account cost over its
contribution per dollar before that cost. Members sum their rows; the whale
curve is cumulative contribution against the share of members, most
profitable first; a member's branch is the branch of most of their balance.
All of it is a run-rate on today's balances, before unallocated overhead and
before any value a relationship has beyond this year's margin.

## RAROC pricing

`keel/pricing.py` runs one deal through the same monthly step on the base
curve for its whole life and measures every line per dollar of average
balance (balance-years): yield, strip FTP, spread, capital credit, the
upfront fee and origination cost spread over the balance-years, fee yield,
servicing, expected loss (its charge-offs), tax. The hurdle and break-even
rates are found by bisection between -5% and 60%, and the tests hold each
solved rate to its target. A deposit has no capital: its break-even is the
highest rate that still covers its costs.

## New products

`keel/newproduct.py` adds the product to the settings (copying the product
it is `like`, then its own `behaviour`), prices new business at the
proposal's spread to the curve, originates `launch_balance` on the analysis
date through cash, and grows it at `growth`. It reports the pricing
calculator's unit economics, the product's own path through the plan (its
balance and interest read from the projection, FTP at the launch deal's
rate, costs at its own rates, origination spread over the average life),
and the whole book's key measures and limits with and without it. The
institution runs carry the product's interest only, not its servicing cost.

## Deposit pricing

`keel/depositpricing.py`. For a share product with balance B, rate r and
balance sensitivity s (annual runoff per 100bp of market-over-share spread,
as a decimal), a move of d basis points brings B x s x d / 100 of balance in
a year and costs B x d / 10,000 on the balance already there plus the new
balance at r + d, so the marginal cost is r + d + 1 / (100 s), whatever B
is. s is the deposit study's estimate when its R² is 0.2 or better and
positive, else the settings' `runoff_per_100bp`; a product with s of zero
shows no money moving.

A certificate special of volume V at rate s over a window: rollovers
R = min(V, maturing x renewal share), with maturing the certificates of that
product whose remaining term is within the window, repriced from the
standard renewal rate q (the curve at the term plus the product's spread,
what the plan pays on renewal); the rest, V - R, split by `sources`, each
existing product leaving at its balance-weighted rate. Incremental interest
is V x s less what the moved money paid before; the marginal cost is that
over the new money. Wholesale is the curve at the term plus
`wholesale_spread`. With the other sources' mix at rate m, the break-even
new-money share of V - R is [R(s - q) + (V - R)(s - m)] / [(V - R)(w - m)];
above 1 no share of new money makes the special cheaper than borrowing.

## Investment purchases and swaps

`keel/swap.py` settles the trade on the analysis date through cash. A sale's
proceeds are its market value (the position's cash flows discounted on
today's curve plus the product's discount spread, as the portfolio page
values it) or the given price; proceeds less book is realized at once, so
equity moves by exactly that. A purchase is a new fixed-rate position at its
yield, or at the curve at its term plus the given spread. The yield pickup
is income on what is bought, less income given up, plus or minus the cash
difference at the short rate, all on today's yields.

The earn-back runs the book with and without the trade through the plan in
the base case and at +/-300bp: the plan reinvests cash and runoff at each
scenario's rates, so the extra NII includes that reinvestment and a faster
or slower prepayment of what was bought. It is the first month the
cumulative difference in NII reaches the realized loss, or "not within the
plan". The plan books the loss against net worth on the analysis date, not
through year-one net income; the page states year-one net income with it.

## Budget and variance

`keel/budget.py`. The budget is months 1 to 12 of the base run: each
product's month-end balance, its average (the mean of the opening and the
month end), interest and annualized yield. A check holds the products'
interest, less overnight interest, to the plan's NII. Variance runs over the
months the actuals cover. For each product, with budget balance B_b and
interest I_b and actual B_a and I_a over the period:

    volume = (B_a - B_b) * I_b / B_b        rate = (I_a - I_b) - volume

with the sign reversed for liabilities, so each is the effect on NII. A
product the actuals leave out counts as on budget.

**Actuals from the ledger** (`keel/ledger.py`). A monthly trial balance and
an account map give the same actuals. Each mapped account's natural-sign
balance is turned around by its line: a liability's balance, an asset's
interest income and fee income are credits and come out positive. A
year-to-date account's month is its balance less the prior month's, except
in the fiscal year's first month; a month without the prior month's
balance is left out rather than guessed. A product's average balance is
the mean of the prior and current month-ends (the month-end alone when the
prior is missing), the same definition the budget uses, so a ledger that
matches the plan shows no variance. The analysis date's month-end
balances are reconciled to the positions: each product within 0.5% (or $1).

## Rate paths

`keel/curve.py`, `RatePath`. The base case is a move from today's curve by
month and tenor. `forward` reads today's par rates as annually compounded
zero rates z(t) and takes the rate for T months starting in month m from
(1 + z(m + T))^((m + T)/12) = (1 + z(m))^(m/12) (1 + f)^(T/12).
`forecast` takes rates at chosen months and tenors: each forecast month's
moves from today's curve are interpolated across tenors, then linearly
across months from today (month 0, no move); after the last forecast month
the curve holds. A scenario's shock is added to the path. Only the
going-concern projection (NII, plan, budget, liquidity) follows the path;
NEV, FTP and every market value discount on today's curve. A
`rates unchanged` scenario runs the plan without the path.

## Budget drivers

A driver in month t sets, for that month only, the product's new-business
volume (originated at the month's terms) or its month-end balance, and an
offering rate that applies from t until the next rate driver. For new
business the rate is the budgeted rate plus the scenario's own shift at
that month (so a +100bp shock prices new business 100bp higher); for an
administered share product it replaces the book rate and moves by the
product's beta times the scenario's shift. After a volume or balance
driver, the product grows at its planned rate from the balance it reached.
Non-interest lines are annual amounts paid monthly from their start month,
grown each plan year; when present they replace `fee_income` and
`operating_expense`.

## Key-assumption tests

`keel/sensitivity.py`. Deposit betas, deposit decay and prepayment speeds
(base and rate-driven) are each scaled by 1.5 and 0.5 across every product
that has them, and new-business loan spreads moved by 25bp, one family at a
time. Each variant runs year-one NII at base and +/-300bp and NEV at base
and +/-300bp, and its NII decline, NEV decline and NEV ratio are read
against the same limits as the report. Decay changes NEV but not the plan's
NII, because the plan holds share balances to their growth path.

## Regulatory capital

`keel/capital.py`. Credit unions: the net worth ratio (equity / assets) sets
the category at 7, 6, 4 and 2 percent (12 CFR 702.102); over $500 million,
also the risk-based capital ratio, (equity + the allowance up to 1.25% of
risk-weighted assets) / risk-weighted assets, well capitalized at 10%, and
the CCULR at 9% if elected. Banks: Tier 1 leverage (equity / assets, 5%),
CET1 and Tier 1 risk-based (equity / risk-weighted assets, 6.5% and 8%),
total risk-based (with the allowance as above, 10%) and the CBLR (9%, under
$10 billion, if elected). Risk-weighted assets are product balances times
`risk_weight`; there are no deductions, off-balance-sheet items or past-due
and concentration adjustments. The market view adds (market - book) of the
securities to equity and to assets, with market value on today's curve and
after a +300bp instantaneous shock.

## Credit scenarios and CECL

`keel/credit.py`. A credit scenario's factor is its multiplier for its
stressed months, then falls linearly to one over its reversion months; the
monthly charge-off on every asset position is its product's annual
`charge_off` times that factor, divided by twelve. Each scenario runs the
going-concern plan on the base rate path.

The CECL estimate is the remaining-life method: each loan position's runoff
(its amortization, prepayment and maturity) with the monthly charge-off
applied to the balance still outstanding, summed over its life; a maturing
balance repays before that month's loss. Under a scenario the same sum with
the scenario's factor gives the allowance that forecast implies; the
difference from the baseline is the provision a turn in the forecast forces
at once, and the net worth ratio after it is (equity - build) / (assets -
build). The booked allowance is the sum of negative-balance asset positions.
Single-factor and product-level: no vintages, PD/LGD, discounting or
qualitative adjustment.

## Liquidity scenarios, collateral and concentration

`keel/liquidity.py`. A scenario multiplies every share product's
`stress_runoff`, adds points to each liquid product's `haircut`, multiplies
each contingent source's capacity by `contingent_available`, sets the stress
length, and adds `uninsured_runoff` times the estimated uninsured balance,
spread over share balances in proportion, as extra runoff. Each runs through
the same stressed going-concern projection as the base stress, and its
survival month is read against `survival_months_min`.

Secured sources are capped at the lendable value of pledgeable loans
(balance times `collateral_value`) less existing borrowings; secured sources
draw on that headroom in the order listed, so two lines never pledge the
same loans. Securities are not collateral here, since they already count as
liquid after their haircut. The cap applies to the base stress too.

Uninsured balances are the sum over members of the balance above $250,000.
Ownership categories are not in the file, so this is an upper bound; the
certificate-only fallback is a lower bound, and the report names which it is.

## The deposit study

`keel/deposits.py`. For each share product with at least 13 months of
history:

- **Beta and lag:** ordinary least squares of the share rate on the market
  rate lagged L months, L = 0 to 6 (at least 12 observations); the lag with
  the highest R² is reported.
- **Up and down betas:** the change in the share rate from the market's
  trough to its peak (each shifted by the lag), over the market's change;
  and from the peak to the end of the history likewise.
- **Runoff per 100bp:** monthly log balance growth regressed on the spread
  of the market rate over the share rate, in percentage points; minus the
  slope, times 12. Recommended only when R² is at least 0.2.
- **Core balance:** the lowest trailing-12-month balance over the average.
- **Decay:** for the accounts with a balance in the first month, the share of
  their combined balance still held in month t, fitted through the origin as
  ln R(t) = (t/12) ln(1 - d). New accounts are excluded, which is why
  aggregate balances cannot give decay.

A flag is raised when an estimate differs from the assumption by 10 points
of beta, 3 points of decay, or 2 points of runoff per 100bp (with R² of at
least 0.2). A beta is recommended only with R² of at least 0.5. On synthetic
history with known behaviour the estimates came within a point or two of
the truth for money market (beta 53% against 55% up; decay 27% against
28%); the regular-share lag was found at 6 months against a true 3, because
a floored rate barely moves, which is also why its recommendation should be
read with its R².

## Run history and back-testing

`keel/history.py`. Each run saves its headline measures, limits,
assumptions, book and the base plan's first 24 months (balance and interest
by product, NII, net income, the short rate it assumed). The next run
compares: its assumptions field by field with the last run's; and, k months
on, the last run's month-k balances and implied rates (interest over the
month's average balance) with today's book, and its monthly NII with the
months `actuals.csv` reports. It also shows how far the short rate moved
from the path the last run assumed, which separates a rate surprise from a
behavioural one.

## Call report loading

`keel/callreport.py`. Balances come from the 5300 accounts listed in the
module; total assets tie as cash and other deposits (AS0009) + investment
securities (AS0013) + other investments (AS0017) + loans (025B) - the
allowance (AS0048, or 719) + land and buildings (007) + other fixed assets
(008) + the NCUSIF deposit (794) + foreclosed assets (798A) + other assets
(AS0036), with any remainder in other assets. On the June 2026 cycle that
identity is exact for 94% of credit unions, and after the remainder every
one of the 4,299 ties to within a cent. The call report's loan rates are the
most common rate by type. The fixed-rate lines are scaled together to the
reported loan interest, net of what the variable-rate lines earn at their
reported rates: a variable loan already earns today's index plus its
margin, so the gap is the seasoned fixed book (when the fixed lines cannot
carry it, every line is scaled). NEV values what the call report values
the same way: securities, carried at fair value (held to maturity at cost,
with fair value reported beside it), take one discount spread across the
book so together they are worth that fair value today, and each
variable-rate loan line is discounted to par; every other position takes
its product's default spread. Scaling every line, and discounting
securities on a book already marked to market, had valued HELOCs and ARMs
5-10% under par and taken securities losses twice, together up to about 4%
of assets. Share rates are not reported, so typical relative rates are
scaled to the reported dividends; charge-off rates are scaled to reported
net charge-offs. Year-to-date income is annualized by days (365 over the
days the cycle covers; the first quarter has 90). Securities and other
investments (deposits in other institutions, FHLB stock, CUSOs) together
fill the call report's investment maturity bands. Because the plan pays the
short rate on cash, cash and investments are calibrated together to the
investment income reported: at a blended yield under the short rate only
that share of the cash earns and the rest is kept as liquid, non-earning
cash; over it, all the cash earns and the securities the remainder. With
`--prior`, the previous quarter's report in the same year, interest income
and expense are the latest quarter's at an annual rate (a year-to-date
average lags when margins move); fees, operating expense and charge-offs
stay year to date, because a single quarter of them is lumpy. With
`--year-ago`, the report four quarters earlier, loan and share growth are
half the credit union's own growth over the year (clamped to -20% and
+30%) and half its NCUA peer group's median. Tested over four quarters of
every credit union against Keel's fixed defaults, the latest quarter
annualized, the credit union's own year alone and the peer median alone,
the blend forecast next quarter's loans, shares and assets best; the
latest quarter annualized was the worst, too noisy and seasonal.

## Validation against NCUA call reports

`keel validate` (`keel/validate.py`) builds every credit union from one
quarter's call report, runs the plan forward three months, and scores it
against the next quarter's report: net interest income for the quarter (the
later year-to-date less the earlier, with Keel's quarter set to the
quarter's actual days), total assets, loans, shares, and the change in net
worth (in basis points of assets). Each is compared with a naive forecast:
the latest quarter's NII and net income repeated and balances unchanged.
`tools/quarterly_scorecard.py` runs it over every quarter from June 2024,
fetching new call reports and Treasury curves as they are published, and
writes **VALIDATION.md**, the current record (aggregate figures only), and
`validation-history.csv`, its headline each quarter.

Through June 2026, over the latest four quarters (17,411 credit-union
quarters, calibrated on the latest quarter with growth from the past year),
Keel's quarterly NII has a median error of 2.9% against 3.1% naive and is
closer for 60% of credit unions (75% of those over $1 billion); it is also
closer on assets, loans and shares, and no better than naive on the change
in net worth. What the record says:

- **Interest expense and loan interest are forecast to within about 1%.**
  Most of the remaining NII error is investment income, which moves with
  rates inside the quarter, and which a forecast made at its start cannot
  know: Keel ran high in a quarter of cuts and low in one where one- and
  two-year rates rose. Neither a forward-curve base case nor pricing new
  loans at the call report's reported rates helped, so neither was kept.
- **Balances beat "nothing changes" once growth comes from the past year**
  and the peer group, not fixed defaults.
- **Net worth change is no better than repeating last quarter**, because it
  is dominated by fees, expenses and provisions, which the plan carries as
  flat annual rates.

The validation found and fixed four calibration faults, each named in the
code where it was fixed: cash earned the short rate on top of an investment
yield already spread over it (the smallest, cash-heavy credit unions' NII
came out 7-9% high); other investments were left out of the earning
portfolio; income was annualized by months, not days; and a year-to-date
average was used where the latest quarter was available. The per-credit-
union rows name real institutions on default behaviour, so they are written
to the git-ignored `private/`.

## Second opinion

`keel compare` (`keel/compare.py`) sets Keel's figures beside another ALM
model's on the same book: opening balances, NII in years one and two, NEV
and the NEV ratio, and their changes, by scenario. Each is within or outside
a tolerance (0.1% on balances, 3% on year-one NII, 5% on year two, 10% on
NEV, 1.5 points on the NEV ratio, 2 to 5 points on changes under shock), and
the differences outside are sorted into kinds with where to look for each:
the opening book first, then the base NII, shocked NII, base NEV and
shocked NEV. It says where two sound models part, not which is right.

## Bank mode

`institution: bank` changes three things. Income tax (`tax_rate`, default
21%) is charged monthly on pre-tax income, so cash and equity both move by
it. NCUA's NEV Supervisory Test and the 12 CFR 741.12 tiers are left out.
And every page is translated as its last step (`keel/terms.py`): NEV to EVE,
shares to deposits, net worth to equity. The measures are the same
computations under either name.

## What-ifs

`keel/whatif.py` applies assumption changes, by dotted path in the file's
units, and balance-sheet actions on the analysis date. It then runs the base
and the changed book through the same measures. Actions settle through cash:
- a new liability adds cash, and a new asset spends it;
- a sale is priced at market (the sold share of the position's base PV), and
  the difference from book is a realized gain or loss that lowers or raises
  net worth on the analysis date;
- a borrowing that names its `draws_on` contingent source reduces that
  source's capacity, so the stress does not count the same funding twice.

The base book is never changed.

## Reconciliation

Four checks run on every report, and one per GL tie when the book came from
core-system files:

1. Assets = liabilities + equity in every month of every scenario, to $1.
2. Year-one NII is the same by two paths: the income statement's lines, and
   the product-by-product interest detail less overnight interest.
3. Liquidity's cash path is the balance sheet's cash line.
4. Every position's runoff principal plus charge-offs equals its balance, so
   NEV discounts all of it.

A test breaks the balance sheet on purpose and confirms the first check
fails, so the checks can't pass vacuously.

## Known simplifications

- A credit union built from its call report has Keel's default behaviour,
  terms and costs, not its own; its rate-risk results are indicative only.
- The key-assumption tests scale each family uniformly; a deposit study
  would give each product its own range.

- FTP uses the base curve with no liquidity premium or optionality charge;
  non-maturity deposits are funded to their decay, not to a management
  tenor. Profitability is a run-rate, not the plan.
- Risk weights are per product, not per exposure: no past-due, collateral
  or concentration adjustments, and the capital ratio is a simplified
  risk-based measure, not a call-report calculation.
- The budget is monthly for one year; later years are annual in the plan.

- One main curve: indexes can have their own curves and betas and basis
  scenarios move them apart, but funding, mortgage and deposit rates are
  still read off the main curve plus each product's spread.
- Curve shapes are interpolated moves at chosen tenors, not a fitted
  key-rate or principal-component model.
- No option-adjusted valuation: prepayment and decay respond to rate levels
  through the stated sensitivities, not to a rate-path simulation.
- Caps and floors bind on the rate path; their option value is not priced
  separately.
- In the stress, certificates are not renewed during the stress months, and
  no extra runoff is applied to certificates that have not yet matured.
- Fee income is flat, and operating expense grows once a year.
- CECL is a remaining-life estimate on product charge-off rates, not a
  vintage, PD/LGD or discounted-cash-flow model.
- Pooling approximates each pool's cash flows with its weighted rate and
  term. Very wide pools would blur amortization, which is why the bands are
  narrow.
- Securities earn their book yield on book value; premium and discount
  amortization is folded into the yield, not modelled separately.
- Delinquent loans still accrue; there is no non-accrual treatment yet.
