# Keel

**ALM, the plan and liquidity for credit unions, from one projection, so the three always agree.**

*Keel is a working name, not yet checked for conflicts.*

A credit union's rate-risk model, budget and liquidity plan usually live in
different places, and when they are asked the same question, such as next
year's NII, they give different answers. Keel runs one monthly projection of
every position and reads all three from it:

- **Interest-rate risk:** NII by scenario for years 1 and 2, and NEV under
  parallel shocks of ±100 to ±300bp, with the **NCUA NEV Supervisory Test**
  (+300bp; SL 22-01 thresholds).
- **The plan:** a five-year income statement and balance sheet, under the
  base case and under every scenario.
- **Liquidity:** the stress survival horizon, the contractual gap, ratios,
  and which tier of 12 CFR 741.12 applies.
- **Reconciliation:** four checks, run on every report, that test the three
  really are one model.

**It runs on the credit union's own machine and sends nothing anywhere.**
Standard-library Python 3.8+, no dependencies to install, no network calls.
The inputs are two plain-text files a validator can read in full, and every
assumption is printed in the report. [`METHODOLOGY.md`](METHODOLOGY.md) is
written for the model validator.

## Try it

```bash
python -m keel run examples/sample-cu --name "Sample Credit Union (synthetic)"
```

This writes `examples/sample-cu/report/report.html` and a monthly CSV. The
sample is a synthetic $560M credit union; every figure in it is invented.

## Inputs

**`positions.csv`**, one row per instrument or pool: `id, name, product,
side, balance, rate, rate_type (fixed | variable | administered | none),
index, margin, reset_months, term_months, amortization (level | bullet |
nonmaturity | none), floor, cap`. Rates are in percent. Cash is the row with
product `cash`.

**`assumptions.json`**: the curve, the indexes, each product's behaviour
(prepayment and its rate sensitivity, share decay and beta, new-business term
and spread, NEV discount spread, planned growth, charge-offs, liquidity
haircut and stress runoff), income and expense, and the contingent liquidity
sources. See the sample, whose `notes` explain the units.

Inputs that would make the projection wrong rather than merely odd stop the
run with the file and line: a term loan without a term, a duplicate id, a
product with no assumptions, a variable rate without an index.

## Tests

```bash
python -m unittest discover -s tests      # 21 tests
```

Most pin an answer known independently of Keel: a textbook mortgage payment,
a bond yielding its discount rate valued at par, a 10bp move from a 10% beta,
SL 22-01's bands, and 741.12's tiers. One breaks the balance sheet on purpose
to show the reconciliation catches it.

## Status

A first slice, built 2026-09-27 and run only on synthetic data. **It has not
been validated against a production ALM model.** That comparison, on a real
credit union's positions, is the next test that matters. What it does not do
yet is listed at the end of [`METHODOLOGY.md`](METHODOLOGY.md).
