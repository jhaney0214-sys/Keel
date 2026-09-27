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

## Instruments

Beyond level, bullet and non-maturity positions:
- **Balloon:** amortizes as if over `amort_months`, prepays like a level
  loan, and is due in full at maturity.
- **Callable:** due at maturity. From its first call date it is called in any
  month when its coupon exceeds the scenario rate for its remaining term,
  plus the product's spread, by more than `call_threshold`.
- **ARMs** reset first at `next_reset_months`, then every `reset_months`.

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

- One curve drives everything: no separate funding, mortgage or deposit
  curves, and no basis risk between indexes beyond fixed spreads.
- Curve shapes are interpolated moves at chosen tenors, not a fitted
  key-rate or principal-component model.
- No option-adjusted valuation: prepayment and decay respond to rate levels
  through the stated sensitivities, not to a rate-path simulation.
- Caps and floors bind on the rate path; their option value is not priced
  separately.
- In the stress, certificates are not renewed during the stress months, and
  no extra runoff is applied to certificates that have not yet matured.
- Fee income is flat, and operating expense grows once a year.
- No CECL. Credit losses are a flat charge-off rate by product.
- Pooling approximates each pool's cash flows with its weighted rate and
  term. Very wide pools would blur amortization, which is why the bands are
  narrow.
- Securities earn their book yield on book value; premium and discount
  amortization is folded into the yield, not modelled separately.
- Delinquent loans still accrue; there is no non-accrual treatment yet.
