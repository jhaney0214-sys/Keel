# Start here: running Keel on your own institution

This is for a finance or ALM team bringing its own core-system export. It
takes you from nothing to a report whose every detail file ties to your
general ledger. Keel runs on your computer and sends nothing anywhere.

## 1. What you need

- **Python 3.9 or later**, from python.org. On Windows, tick "Add
  python.exe to PATH" in the installer. Nothing else is installed: Keel
  uses only Python's standard library.
- **This Keel folder**, anywhere on your computer.

Check it works: open a terminal in the Keel folder and run

```bash
python -m keel run examples/sample-cu
```

It writes `examples/sample-cu/report/report.html`. (Or, on Windows, drag
`examples\sample-cu` onto `windows\Keel report.cmd`.)

## 2. Make your folder

```bash
python -m keel init "../Riverbend FCU"
python -m keel run "../Riverbend FCU"
```

Add `--bank` for a bank. On Windows, double-click `windows\New Keel
folder.cmd` instead. The new folder has every input file with its columns
and a few example rows that tie to an example ledger, so the first run
works: its report is of a pretend $1.7 million credit union. Keep your
folder **outside** the Keel folder, since it will hold member-level data.

## 3. Replace the example rows, one file at a time

Run the report after each file. Its **Reconciliation** section lists each
detail file against its ledger account; a file that no longer ties is named
with the difference. Fix that before reading anything else.

**`data/gl.csv`: your trial balance on the analysis date.** Columns
`account, description, balance`, with debits positive and credits
negative, as the trial balance prints them. Every account can be listed;
Keel reads only the ones the product map names.

**`data/product_map.xlsx`: how your codes and accounts map to Keel.** One
sheet per section.

| Sheet | Columns | What goes in it |
|---|---|---|
| loans, certificates, shares, investments, borrowings | `code`, `product` | each product code your core uses, and the Keel product it belongs to (a code of `*` catches the rest; an unmapped code stops the run and names its file and row) |
| gl | `key`, `account` | the ledger accounts for `cash`, `fixed_assets`, `ncusif`, `other_assets`, `allowance`, `other_liabilities`, and the accounts each detail file ties to: `loans`, `investments`, `certificates`, `shares`, `borrowings`. Several accounts are a comma-separated list; `30*` is every account starting 30. |

Keel's product names are yours to choose, but every one must appear on the
Products sheet of `assumptions.xlsx` (step 4).

**The detail files.** Extra columns are ignored, dates are `YYYY-MM-DD`
(Excel dates read too), rates are in percent, and any file can be `.csv`
or `.xlsx`.

| File | One row per | Columns |
|---|---|---|
| `loans.csv` | loan | `loan_id, product_code, rate_type` (F or V), `index, margin, reset_months, next_reset_date, rate_floor, rate_cap` (variable loans), `days_delinquent, origination_date, maturity_date` (blank for lines of credit), `current_balance, rate, amortization_months` (balloons); optional `member_id, branch` |
| `certificates.csv` | certificate | `certificate_id, product_code, open_date, maturity_date, balance, rate`; optional `member_id, branch` |
| `shares.csv` | product and balance tier | `product_code, tier_low, tier_high` (blank for no top), `accounts, balance, rate` |
| `investments.csv` | security | `security_id, type` (mapped on the investments sheet), `description, maturity_date, book_value, book_yield, next_call_date` (callables), `wam_months` (MBS and CMOs) |
| `borrowings.csv` | borrowing | `borrowing_id, lender` (mapped on the borrowings sheet), `balance, rate, maturity_date` |

Loans 90 or more days past due are treated as non-accrual. Loans and
certificates are pooled by product, rate type, term and rate for speed;
the pools are written to `report/positions_imported.csv` so you can see them.

## 4. Set the assumptions

`assumptions.xlsx` starts with Keel's values, which are not yours. In order
of what matters most:

1. **Settings sheet:** `as_of`, your analysis date, and the income lines
   `fee_income` and `operating_expense` (dollars a year).
2. **Curve sheet:** the Treasury (or your funding) curve on that date.
3. **Products sheet:** for non-maturity shares, `beta`, `runoff` and
   `runoff_per_100bp` (they drive NEV more than anything else); for loans,
   `cpr`, `cpr_per_100bp` and `charge_off`; for new business, `new_term`
   and `spread`. The notes on each sheet give the units.
4. **Limits sheet:** your board's policy limits. Limits left blank show as
   Keel's defaults, and the report says so.

If you have them, these optional files add sections to the report:

| File | Adds |
|---|---|
| `deposit_history.csv` (month, product, balance, rate, market_rate) | the deposit study: your own betas and balance sensitivity, against your assumptions |
| `deposit_accounts.csv` (month, account_id, product, balance) | deposit decay from account cohorts |
| `depositors.csv` (member_id, balance) | uninsured deposits and concentration |
| `data/member_shares.csv` (member_id, product_code, balance) | profitability by member and branch |
| `trial_balance.csv` + `gl_map.csv`, or `actuals.csv` | budget variance against actuals |

The README has each one in full.

## 5. Read the report

Open `report/report.html`. It starts with findings in plain language and
the policy limits, then has a section for each area: rate risk, the plan,
liquidity, capital, profitability, the budget, and the reconciliation and
assumptions that produced it all. `report/results.xlsx` has every table.
`python -m keel serve "../Riverbend FCU"` (or `windows\Keel app.cmd`) opens
the what-if, pricing, new-product, trade and explore pages in your browser.

## What to trust, and what not yet

Keel's projection is checked by its own reconciliation on every run, and
its quarterly forecasts have been scored against NCUA call reports for every
credit union over five quarters (METHODOLOGY.md, "Validation against NCUA
call reports"). It has **not** yet been compared side by side with a
production ALM model on the same institution's files. Until it has, run it
beside your current model, and investigate every difference before relying
on either.
