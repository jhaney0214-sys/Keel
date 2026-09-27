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

**NCUA NEV Supervisory Test.** The +300bp scenario is instantaneous, parallel
and sustained. Thresholds are from Letter SL 22-01: a post-shock ratio above
7% is low, 4–7% moderate, below 4% high; sensitivity below 40% is low, 40–65%
moderate, above 65% high. The formula for "sensitivity" was not found in the
NCUA text read, so Keel reports both the decline in the NEV ratio (used for
the rating) and the decline in NEV. **Confirm which your examiner uses.**
SL 22-01 also refers to standardized values for non-maturity shares. Keel
takes decay, beta and discount spread as inputs; enter NCUA's values for the
supervisory test.

## Liquidity

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

## Reconciliation

Four checks run on every report:

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
- Shocks are parallel or linear ramps. No twists, key-rate shocks or
  non-parallel curves yet.
- No option-adjusted valuation: prepayment and decay respond to rate levels
  through the stated sensitivities, not to a rate-path simulation.
- Caps and floors bind on the rate path; their option value is not priced
  separately.
- In the stress, certificates are not renewed during the stress months, and
  no extra runoff is applied to certificates that have not yet matured.
- Fee income is flat, and operating expense grows once a year.
- No CECL. Credit losses are a flat charge-off rate by product.
