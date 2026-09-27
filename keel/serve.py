"""Keel in the browser, served from this machine to this machine.

    python -m keel serve examples/mid-cu        # then open http://127.0.0.1:8750/

Four pages, each running the same model as the report:

* **What-if**: the levers an ALCO asks about (planned growth, share betas
  and decay, prepayment speeds, a new borrowing, a sale), prefilled with
  today's assumptions; base and what-if side by side, every limit included.
* **Pricing**: the RAROC calculator for a loan or deposit.
* **New product**: spread analysis of a proposed product and its effect on
  the whole book.
* **Explore**: ad hoc queries over the book, the core files, the budget and
  profitability, downloadable as CSV and savable into the report.

The folder's own report, if it has been run, is served at /report/. It
listens on 127.0.0.1 only, so nothing outside this computer can reach it,
and it calls nothing outside it either.
"""

import html
import http.server
import json
import os
import re
import threading
import urllib.parse

from keel import measures, model, newproduct, pricing, query, swap, terms, whatif
from keel.report import STYLE, chip, pct, table

LEVERS = (  # (field, label, which products show it)
    ("growth", "Planned growth, %/yr", lambda p: p.growth or p.charge_off or p.runoff),
    ("beta", "Share beta, %", lambda p: p.beta),
    ("runoff", "Decay or paydown, %/yr", lambda p: p.runoff),
    ("cpr", "Prepayment (CPR), %/yr", lambda p: p.cpr),
)
PAGES = (("/", "What-if"), ("/pricing", "Pricing"), ("/newproduct", "New product"), ("/trade", "Trade"),
         ("/explore", "Explore"))
PRICING_OVERRIDES = (("cpr", "Prepayment (CPR)"), ("runoff", "Decay"), ("charge_off", "Expected loss"),
                     ("servicing_cost", "Servicing cost"), ("fee_yield", "Fee yield"),
                     ("origination_cost", "Origination cost"), ("risk_weight", "Risk weight"))

FORM_CSS = """
input,select,button,textarea{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--rule);
border-radius:4px;padding:.3rem .45rem} input:focus,select:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:1px}
td input{width:5.5rem;text-align:right;font-variant-numeric:tabular-nums}
label{display:inline-flex;flex-direction:column;gap:.2rem;font-size:.82rem;color:var(--ink-2);margin:0 1rem .6rem 0;vertical-align:top}
label input,label select,label textarea{color:var(--ink);font-size:.95rem}
button,.button{background:var(--accent);color:#fff;border:0;font-weight:600;padding:.6rem 1.4rem;margin-top:.6rem;cursor:pointer;
border-radius:4px;text-decoration:none;display:inline-block}
button:hover,.button:hover{filter:brightness(1.1)} button.quiet{background:var(--panel);color:var(--ink)}
fieldset{border:1px solid var(--rule);border-radius:4px;margin:1rem 0;padding:.8rem 1rem .4rem}
legend{font-weight:600;padding:0 .3rem} .message{padding:.6rem .85rem;background:var(--panel);border-radius:4px}
nav a.on{color:var(--ink);font-weight:600;border-bottom:2px solid var(--accent);padding-bottom:.2rem}
.fields{font-size:.8rem;color:var(--ink-2)} .fields code{margin-right:.4rem}
pre{overflow-x:auto;background:var(--panel);padding:.8rem;border-radius:4px;font-size:.82rem;max-width:100%}
"""


def _num(fields, key, default=None):
    value = (fields.get(key) or "").strip().replace(",", "")
    if value == "":
        return default
    try:
        return float(value)
    except ValueError:
        raise model.InputError("%s: %r is not a number" % (key.replace("_", " "), value))


def form_to_spec(fields, assumptions):
    """The what-if a submitted form describes. Only values that differ from
    today's assumptions become changes, so an untouched form is the base."""
    spec = {"name": fields.get("name", "").strip() or "Browser what-if", "assumptions": {}, "actions": []}
    for key, value in fields.items():
        if not key.startswith("p."):
            continue
        _, product, field = key.split(".", 2)
        if value.strip() == "":
            continue
        current = getattr(assumptions.products[product], field) * 100.0
        new = float(value)
        if abs(new - current) > 1e-9:
            spec["assumptions"]["products.%s.%s" % (product, field)] = new
    amount = float(fields.get("borrow_amount") or 0)
    if amount > 0:
        add = {"id": "whatif_borrowing", "name": "New borrowing", "product": "borrowings", "side": "liability",
               "balance": amount, "rate": float(fields.get("borrow_rate") or 0),
               "rate_type": "fixed", "term_months": int(float(fields.get("borrow_term") or 12)),
               "amortization": "bullet"}
        if fields.get("borrow_source"):
            add["draws_on"] = fields["borrow_source"]
        spec["actions"].append({"add": add})
    sell = float(fields.get("sell_percent") or 0)
    if sell > 0 and fields.get("sell_product"):
        spec["actions"].append({"scale": {"product": fields["sell_product"], "factor": 1.0 - sell / 100.0}})
    return spec


def form_to_deal(fields, positions):
    """The deal a pricing form describes."""
    product = fields.get("product", "")
    rate = _num(fields, "rate")
    overrides = {key: (None if _num(fields, key) is None else _num(fields, key) / 100.0)
                 for key, _ in PRICING_OVERRIDES}
    return pricing.Deal(product=product, amount=_num(fields, "amount", 25000.0), term_months=int(_num(fields, "term", 60)),
                        rate=None if rate is None else rate / 100.0, side=pricing.side_of(product, positions),
                        amortization=fields.get("amortization") or None,
                        amort_months=int(_num(fields, "amort_months", 0)),
                        upfront_fee=_num(fields, "upfront_fee", 0.0) / 100.0, **overrides)


def form_to_proposal(fields):
    """The new-product proposal a form describes (in file units, percent)."""
    key = re.sub(r"[^a-z0-9_]+", "_", (fields.get("product") or "").strip().lower()).strip("_")
    if not key:
        raise model.InputError("give the new product a key, such as green_auto")
    proposal = {"name": fields.get("name", "").strip() or key, "product": key,
                "like": fields.get("like") or None, "side": fields.get("side") or "asset",
                "term_months": int(_num(fields, "term_months", 0)),
                "amortization": fields.get("amortization") or "level",
                "launch_balance": _num(fields, "launch_balance", 0.0), "growth": _num(fields, "growth", 0.0),
                "average_size": _num(fields, "average_size", 25000.0), "upfront_fee": _num(fields, "upfront_fee", 0.0),
                "behaviour": {}}
    if not proposal["like"]:
        del proposal["like"]
    if _num(fields, "rate") is not None:
        proposal["rate"] = _num(fields, "rate")
    elif _num(fields, "spread") is not None:
        proposal["spread"] = _num(fields, "spread")
    for key_, _ in PRICING_OVERRIDES:
        value = _num(fields, "b_" + key_)
        if value is not None:
            proposal["behaviour"][key_] = value
    return proposal


def form_to_trade(fields):
    """The trade a form describes: every ticked security sold, at its price
    if one is given, and one purchase if it has a product."""
    spec = {"name": fields.get("name", "").strip() or "Investment trade", "sell": [], "buy": []}
    for key in sorted(fields):
        if key.startswith("sell_") and fields[key]:
            sid = key[len("sell_"):]
            sale = {"id": sid}
            if _num(fields, "price_" + sid) is not None:
                sale["price"] = _num(fields, "price_" + sid)
            spec["sell"].append(sale)
    if fields.get("product"):
        buy = {"name": fields.get("buy_name", "").strip() or "purchase", "product": fields["product"],
               "amount": _num(fields, "amount") if _num(fields, "amount") is not None else "proceeds",
               "term_months": int(_num(fields, "term_months", 60)),
               "amortization": fields.get("amortization") or "bullet"}
        if _num(fields, "yield") is not None:
            buy["yield"] = _num(fields, "yield")
        elif _num(fields, "spread") is not None:
            buy["spread"] = _num(fields, "spread")
        spec["buy"].append(buy)
    return spec


def form_to_query(fields):
    lines = lambda text: [x.strip() for x in (text or "").replace("\r", "").split("\n") if x.strip()]  # noqa: E731
    spec = {"name": fields.get("name", "").strip() or "Ad hoc query", "table": fields.get("table") or "positions",
            "by": [b.strip() for b in (fields.get("by") or "").split(",") if b.strip()],
            "measures": lines(fields.get("measures")) or ["count"], "where": lines(fields.get("where"))}
    if (fields.get("sort") or "").strip():
        spec["sort"] = fields["sort"].strip()
    if _num(fields, "limit") is not None:
        spec["limit"] = int(_num(fields, "limit"))
    return spec


class Server(object):

    def __init__(self, folder):
        from keel.__main__ import load
        self.folder = folder
        self.positions, self.assumptions, self.raw, self.imported = load(folder)
        self.base = whatif.key_measures(self.positions, self.assumptions)
        self.tables = query.Tables(self.positions, self.assumptions, folder, self.imported)
        self.lock = threading.Lock()

    # ---------------------------------------------------------- the shell

    def shell(self, title, lead, body, active):
        a = self.assumptions
        tabs = "".join("<a href='%s'%s>%s</a>" % (href, " class='on'" if href == active else "", text)
                       for href, text in PAGES)
        if os.path.isfile(os.path.join(self.folder, "report", "report.html")):
            tabs += "<a href='/report/report.html'>Full report</a>"
        return terms.translate("""<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'><title>Keel: %s</title>
<style>%s%s</style></head><body><header class='top'><div class='inner'><h1>%s</h1><p class='sub'>%s, as of %s. %s
Served from this computer to this computer; nothing leaves it.</p></div></header>
<nav aria-label='Pages'><div class='inner'>%s</div></nav><main>%s</main></body></html>""" % (
            html.escape(title), STYLE, FORM_CSS, html.escape(title),
            html.escape(os.path.basename(os.path.abspath(self.folder))), html.escape(a.as_of), lead, tabs, body), a)

    @staticmethod
    def message(text):
        return "<p class='message'><strong>%s</strong></p>" % html.escape(text) if text else ""

    # ---------------------------------------------------------- what-if

    def page(self, message=""):
        a = self.assumptions
        present = {p.product for p in self.positions}
        rows = []
        for product in sorted(present):
            spec = a.products[product]
            cells = []
            for field, _, shown in LEVERS:
                if shown(spec):
                    cells.append("<td><input name='p.%s.%s' value='%.2f' size='6' inputmode='decimal'></td>"
                                 % (product, field, 100 * getattr(spec, field)))
                else:
                    cells.append("<td></td>")
            if any("input" in c for c in cells):
                rows.append("<tr><td>%s</td>%s</tr>" % (html.escape(product.replace("_", " ")), "".join(cells)))
        sellable = sorted(p for p in present if a.products[p].liquid)
        sources = "".join("<option>%s</option>" % html.escape(n) for n, _ in a.contingent)
        body = """%s<form method='post' action='/whatif'>
<label>Name <input name='name' size='50' placeholder='What are we asking?'></label>
<fieldset><legend>Behaviour and plan (today's values shown)</legend><div class='wrap'><table><thead><tr>
<th>Product</th>%s</tr></thead><tbody>%s</tbody></table></div></fieldset>
<fieldset><legend>Borrow</legend>
<label>Amount <input name='borrow_amount' size='12' inputmode='decimal' placeholder='50000000'></label>
<label>Rate %% <input name='borrow_rate' size='6' inputmode='decimal' placeholder='4.10'></label>
<label>Term, months <input name='borrow_term' size='5' inputmode='numeric' placeholder='36'></label>
<label>Draws on <select name='borrow_source'><option value=''>(no counted source)</option>%s</select></label>
</fieldset>
<fieldset><legend>Sell investments at market</legend>
<label>Product <select name='sell_product'><option value=''></option>%s</select></label>
<label>Percent <input name='sell_percent' size='5' inputmode='decimal' placeholder='50'></label>
</fieldset>
<button type='submit'>Run</button></form>
<h2 style='margin-top:2.6rem'>Today, before any change</h2>%s""" % (
            self.message(message), "".join("<th>%s</th>" % html.escape(label) for _, label, _ in LEVERS),
            "".join(rows), sources, "".join("<option>%s</option>" % html.escape(p) for p in sellable),
            whatif.tables(self.base))
        return self.shell("What-if", "Change behaviour, the plan, borrowing or the portfolio and see every measure "
                          "and limit move.", body, "/")

    def run(self, fields):
        spec = form_to_spec(fields, self.assumptions)
        if not spec["assumptions"] and not spec["actions"]:
            return self.page("Nothing was changed, so there is nothing to compare.")
        with self.lock:
            changed, changed_assumptions, notes = whatif.apply(self.positions, self.raw, spec)
            after = whatif.key_measures(changed, changed_assumptions)
        moved = [a[1] for b, a in zip(self.base, after) if a[2] == "limit" and a[1].status != b[1].status]
        verdict = ("No limit changes status." if not moved else "Limits that change status: %s." % "; ".join(
            "%s, now %s" % (x.label, whatif.STATUS[x.status][2].lower()) for x in moved))
        body = ("<h2>What changed</h2><ul>%s</ul><p><b>%s</b></p>%s<h2>Keep this what-if</h2><p class='muted'>Save "
                "as a .json file and run it again with <code>python -m keel whatif %s file.json</code>, which also "
                "writes its full report.</p><pre>%s</pre><p><a class='button' href='/'>Another what-if</a></p>" % (
                    "".join("<li>%s</li>" % html.escape(n) for n in notes), html.escape(verdict),
                    whatif.tables(self.base, after), html.escape(self.folder), html.escape(json.dumps(spec, indent=2))))
        return self.shell("What-if: " + spec["name"], "The same model, measures and scenarios as the full report; "
                          "only the changes listed differ.", body, "/")

    # ---------------------------------------------------------- pricing

    def products(self, side=None):
        out = []
        for product in sorted({p.product for p in self.positions}):
            p = next(x for x in self.positions if x.product == product)
            if product == "cash" or (p.rate_type == "none" and p.amortization == "none"):
                continue
            if side is None or p.side == side:
                out.append(product)
        return out

    def pricing_page(self, fields):
        a = self.assumptions
        products = self.products()
        chosen = fields.get("product") or ("new_auto" if "new_auto" in products else products[0])
        spec = a.products.get(chosen)
        result, message = "", ""
        if fields.get("product"):
            try:
                q = pricing.quote(form_to_deal(fields, self.positions), a)
                result = self.quote_html(q)
            except (model.InputError, ValueError) as error:
                message = "That deal could not be priced: %s" % error
        v = lambda key, default="": html.escape(fields.get(key, default))  # noqa: E731
        options = "".join("<option%s>%s</option>" % (" selected" if p == chosen else "", html.escape(p))
                          for p in products)
        amort = "".join("<option value='%s'%s>%s</option>" % (x, " selected" if fields.get("amortization") == x else "",
                                                             x or "the product's") for x in
                        ("", "level", "bullet", "balloon", "nonmaturity"))
        overrides = "".join(
            "<label>%s, %% <input name='%s' size='6' inputmode='decimal' value='%s' placeholder='%s'></label>" % (
                label, key, v(key), "" if spec is None or getattr(spec, key) is None else "%.2f" % (100 * getattr(spec, key))
                if key != "risk_weight" or spec.risk_weight is not None else "rule")
            for key, label in PRICING_OVERRIDES)
        body = """%s<form method='get' action='/pricing'>
<fieldset><legend>The deal</legend>
<label>Product <select name='product'>%s</select></label>
<label>Amount <input name='amount' size='12' inputmode='decimal' value='%s'></label>
<label>Term, months <input name='term' size='5' inputmode='numeric' value='%s'></label>
<label>Rate, %% <input name='rate' size='6' inputmode='decimal' value='%s' placeholder='solve'></label>
<label>Amortization <select name='amortization'>%s</select></label>
<label>Balloon amortization, months <input name='amort_months' size='5' value='%s'></label>
<label>Upfront fee, %% <input name='upfront_fee' size='5' value='%s' placeholder='0'></label>
</fieldset>
<fieldset><legend>Behaviour and costs (blank uses the product's, shown)</legend>%s</fieldset>
<button type='submit'>Price it</button></form>%s
<p class='muted'>Target capital %s of risk-weighted balance, hurdle %s, tax %s: from the settings. Leave the rate
blank to solve for the rate that earns the hurdle.</p>""" % (
            self.message(message), options, v("amount", "25000"), v("term", "60"), v("rate"), amort,
            v("amort_months"), v("upfront_fee"), overrides, result, pct(a.target_capital, 0), pct(a.hurdle_rate, 0),
            pct(a.tax_rate, 0))
        return self.shell("Pricing", "RAROC pricing on the institution's own curve, behaviour and costs.", body,
                          "/pricing")

    def quote_html(self, q):
        e = q["at"]
        rows = [[html.escape(("Rate paid" if e["side"] == "liability" and key == "yield" else
                              "Funds transfer credit" if e["side"] == "liability" and key == "ftp" else name)),
                 pct(e[key])] for name, key, _ in pricing.LINES]
        rows.append(["Allocated capital", pct(e["capital"])])
        head = ""
        if e["raroc"] is not None:
            ok = e["raroc"] >= q["hurdle"]
            head = ("<p style='font-size:1.15rem'>RAROC <b>%s</b> %s at %s. Hurdle rate <b>%s</b>; break-even "
                    "<b>%s</b>. Average life %.1f years; lifetime net income $%s.</p>" % (
                        pct(e["raroc"], 1), chip("within" if ok else "near" if e["raroc"] >= 0 else "breach",
                                                 "Clears the hurdle" if ok else "Below the hurdle"),
                        pct(e["rate"]), "n/a" if q["hurdle_rate"] is None else pct(q["hurdle_rate"]),
                        "n/a" if q["breakeven_rate"] is None else pct(q["breakeven_rate"]), e["average_life"],
                        "{:,.0f}".format(e["lifetime_net"])))
        else:
            ok = e["pre_tax"] >= 0
            head = ("<p style='font-size:1.15rem'>Spread over FTP <b>%s</b> %s at %s. The highest rate that still "
                    "covers its costs is <b>%s</b>. Average life %.1f years.</p>" % (
                        pct(e["spread"]), chip("within" if ok else "breach", "Covers its costs" if ok else "Costs more "
                                               "than it earns"), pct(e["rate"]),
                        "n/a" if q["breakeven_rate"] is None else pct(q["breakeven_rate"]), e["average_life"]))
        band = table(["Rate", "Spread", "Net income", "RAROC"],
                     [[pct(b["rate"]), pct(b["spread"]), pct(b["net"]), "n/a" if b["raroc"] is None else pct(b["raroc"], 1)]
                      for b in q["band"]], numeric_from=0)
        return ("<h2>The quote</h2>%s<div class='charts'><div><h3>Per dollar of average balance, a year</h3>%s</div>"
                "<div><h3>Around this rate</h3>%s</div></div>" % (head, table(["Line", "Annual"], rows), band))

    # ---------------------------------------------------------- new product

    def newproduct_page(self, fields=None, message=""):
        fields = fields or {}
        v = lambda key, default="": html.escape(fields.get(key, default))  # noqa: E731
        likes = "".join("<option%s>%s</option>" % (" selected" if fields.get("like", "new_auto") == p else "",
                                                   html.escape(p)) for p in self.products())
        behaviour = "".join("<label>%s, %% <input name='b_%s' size='6' inputmode='decimal' value='%s' "
                            "placeholder='as like'></label>" % (label, key, v("b_" + key))
                            for key, label in PRICING_OVERRIDES)
        body = """%s<form method='post' action='/newproduct'>
<fieldset><legend>The product</legend>
<label>Name <input name='name' size='36' value='%s' placeholder='72-month green auto loan'></label>
<label>Key <input name='product' size='16' value='%s' placeholder='green_auto'></label>
<label>Behaves like <select name='like'><option value=''>(nothing)</option>%s</select></label>
<label>Side <select name='side'><option%s>asset</option><option%s>liability</option></select></label>
</fieldset>
<fieldset><legend>Terms</legend>
<label>Rate, %% <input name='rate' size='6' value='%s'></label>
<label>or spread to the curve, %% <input name='spread' size='6' value='%s'></label>
<label>Term, months <input name='term_months' size='5' value='%s'></label>
<label>Amortization <select name='amortization'><option>level</option><option>bullet</option><option>balloon</option>
<option>nonmaturity</option></select></label>
<label>Upfront fee, %% <input name='upfront_fee' size='5' value='%s'></label>
</fieldset>
<fieldset><legend>Launch</legend>
<label>Launch balance <input name='launch_balance' size='12' value='%s'></label>
<label>Growth after launch, %%/yr <input name='growth' size='6' value='%s'></label>
<label>Average size <input name='average_size' size='10' value='%s'></label>
</fieldset>
<fieldset><legend>Behaviour and costs (blank copies "behaves like")</legend>%s</fieldset>
<button type='submit'>Analyse</button></form>""" % (
            self.message(message), v("name"), v("product"), likes, "" if fields.get("side") == "liability" else " selected",
            " selected" if fields.get("side") == "liability" else "", v("rate", "5.49"), v("spread"),
            v("term_months", "72"), v("upfront_fee"), v("launch_balance", "10000000"), v("growth", "25"),
            v("average_size", "30000"), behaviour)
        return self.shell("New product", "What a proposed product earns, and what it does to the whole book.", body,
                          "/newproduct")

    def newproduct_run(self, fields):
        try:
            proposal = form_to_proposal(fields)
            with self.lock:
                result = newproduct.analyse(self.positions, self.assumptions, self.raw, proposal)
        except (model.InputError, ValueError, KeyError) as error:
            return self.newproduct_page(fields, "That proposal could not run: %s" % error)
        page = newproduct.page(result, STYLE)
        body = page[page.index("<main>") + 6:page.index("</main>")]
        body += ("<h2>Keep this proposal</h2><p class='muted'>Save as a .json file and run it again with "
                 "<code>python -m keel newproduct %s proposal.json</code>.</p><pre>%s</pre>"
                 "<p><a class='button' href='/newproduct'>Another product</a></p>" % (
                     html.escape(self.folder), html.escape(json.dumps(proposal, indent=2))))
        return self.shell("New product: " + proposal["name"], "Spread analysis on the institution's own curve, "
                          "behaviour and costs, and the whole book run with and without it.", body, "/newproduct")

    # ---------------------------------------------------------- trade

    def securities(self):
        if not hasattr(self, "_securities"):
            liquid = {k for k, spec in self.assumptions.products.items() if spec.liquid}
            self._securities = measures.security_analytics(self.positions, self.assumptions, liquid)
        return self._securities

    def trade_page(self, fields=None, message=""):
        fields = fields or {}
        v = lambda key, default="": html.escape(fields.get(key, default))  # noqa: E731
        rows = []
        for s in sorted(self.securities(), key=lambda s: s["gain"]):
            sid = html.escape(s["id"])
            rows.append("<tr><td><input type='checkbox' name='sell_%s' value='1'%s aria-label='sell %s'></td>"
                        "<td>%s <span class='muted'>%s</span></td><td>%s</td><td class='num'>%s</td>"
                        "<td class='num'>%s</td><td class='num'>%.2f%%</td><td class='num'>%.1f</td>"
                        "<td><input name='price_%s' size='6' inputmode='decimal' value='%s' placeholder='model'></td>"
                        "</tr>" % (sid, " checked" if fields.get("sell_" + s["id"]) else "", sid,
                                   html.escape(s["name"]), sid, html.escape(s["product"].replace("_", " ")),
                                   "{:,.0f}".format(s["book"] / 1000.0), "{:,.0f}".format(s["gain"] / 1000.0),
                                   100 * s["yield"], s["duration"], sid, v("price_" + s["id"])))
        liquid = sorted(k for k, spec in self.assumptions.products.items() if spec.liquid)
        options = "".join("<option%s>%s</option>" % (" selected" if fields.get("product") == p else "", html.escape(p))
                          for p in liquid)
        body = """%s<form method='post' action='/trade'>
<fieldset><legend>The trade</legend><label>Name <input name='name' size='40' value='%s'
placeholder='Sell the 2021 callables, buy MBS'></label></fieldset>
<h2>Sell (deepest loss first, $000)</h2><div class='wrap'><table><thead><tr><th>Sell</th><th>Security</th>
<th>Product</th><th class='num'>Book</th><th class='num'>Gain (loss)</th><th class='num'>Book yield</th>
<th class='num'>Duration</th><th>Price, %% of book</th></tr></thead><tbody>%s</tbody></table></div>
<fieldset><legend>Buy</legend>
<label>Name <input name='buy_name' size='28' value='%s' placeholder='FNMA 30-year 5.5%%'></label>
<label>Product <select name='product'><option value=''>(nothing)</option>%s</select></label>
<label>Amount <input name='amount' size='12' value='%s' placeholder='the proceeds'></label>
<label>Yield, %% <input name='yield' size='6' value='%s'></label>
<label>or spread to the curve, %% <input name='spread' size='6' value='%s'></label>
<label>Term, months <input name='term_months' size='5' value='%s'></label>
<label>Amortization <select name='amortization'><option>bullet</option><option%s>level</option>
<option%s>callable</option></select></label>
</fieldset><button type='submit'>Analyse</button></form>""" % (
            self.message(message), v("name"), "".join(rows), v("buy_name"), options, v("amount"), v("yield"),
            v("spread"), v("term_months", "60"), " selected" if fields.get("amortization") == "level" else "",
            " selected" if fields.get("amortization") == "callable" else "")
        return self.shell("Trade", "An investment purchase or swap: the loss, the pickup and how long it takes to "
                          "earn back.", body, "/trade")

    def trade_run(self, fields):
        try:
            spec = form_to_trade(fields)
            with self.lock:
                result = swap.analyse(self.positions, self.assumptions, spec)
        except (model.InputError, ValueError, KeyError) as error:
            return self.trade_page(fields, "That trade could not run: %s" % error)
        page = swap.page(result, STYLE)
        body = page[page.index("<main>") + 6:page.index("</main>")]
        body += ("<h2>Keep this trade</h2><p class='muted'>Save as a .json file and run it again with "
                 "<code>python -m keel swap %s trade.json</code>.</p><pre>%s</pre>"
                 "<p><a class='button' href='/trade'>Another trade</a></p>" % (
                     html.escape(self.folder), html.escape(json.dumps(spec, indent=2))))
        return self.shell("Trade: " + spec["name"], "An investment purchase or swap on the institution's own "
                          "curve and plan.", body, "/trade")

    # ---------------------------------------------------------- explore

    def explore_page(self, fields, message=""):
        v = lambda key, default="": html.escape(fields.get(key, default))  # noqa: E731
        chosen = fields.get("table") or "positions"
        result = ""
        if fields.get("run"):
            try:
                spec = form_to_query(fields)
                q = query.run(spec, self.tables)
                rows = [[html.escape(query.fmt(c)) for c in row] for row in q["rows"][:500]]
                if q["total"]:
                    rows.append([html.escape(query.fmt(c)) for c in q["total"]])
                qs = urllib.parse.urlencode({k: val for k, val in fields.items() if k != "run"})
                result = ("<h2>%s</h2><p class='muted'>%s of %s rows of %s matched; %d groups%s. "
                          "<a href='/explore.csv?%s'>Download CSV</a></p>%s"
                          "<form method='post' action='/explore/save'>%s<button class='quiet' type='submit'>Save to "
                          "the report's ad hoc section</button></form><pre>%s</pre>" % (
                              html.escape(q["name"]), "{:,}".format(q["matched"]), "{:,}".format(q["of"]),
                              html.escape(q["table"]), len(q["rows"]),
                              " (first 500 shown)" if len(q["rows"]) > 500 else "", html.escape(qs),
                              table(q["columns"], rows, numeric_from=len(spec["by"]) or 1, total_last=bool(q["total"])),
                              "".join("<input type='hidden' name='%s' value='%s'>" % (html.escape(k), html.escape(val))
                                      for k, val in fields.items() if k != "run"),
                              html.escape(json.dumps(spec, indent=2))))
            except (model.InputError, ValueError) as error:
                message = "That query could not run: %s" % error
        options = "".join("<option%s>%s</option>" % (" selected" if n == chosen else "", html.escape(n))
                          for n in self.tables.names())
        try:
            fields_list = self.tables.fields(chosen)
        except (KeyError, model.InputError):
            fields_list = []
        body = """%s<form method='get' action='/explore'><input type='hidden' name='run' value='1'>
<fieldset><legend>Query</legend>
<label>Name <input name='name' size='36' value='%s' placeholder='Loans by rate band'></label>
<label>Table <select name='table' onchange='this.form.run.value="";this.form.submit()'>%s</select></label>
<label>Group by (comma-separated) <input name='by' size='36' value='%s' placeholder='product, rate_band'></label>
<br><label>Measures, one a line <textarea name='measures' rows='4' cols='34' placeholder='count
sum balance
wavg rate balance'>%s</textarea></label>
<label>Filters, one a line <textarea name='where' rows='4' cols='34' placeholder='side = asset
balance &gt; 100000'>%s</textarea></label>
<label>Sort <input name='sort' size='16' value='%s' placeholder='-sum balance'></label>
<label>Limit <input name='limit' size='5' value='%s'></label>
<p class='fields'>Fields in %s: %s</p>
<p class='fields'>Measures: <code>count</code><code>sum F</code><code>avg F</code><code>min F</code><code>max F</code>
<code>wavg F W</code>. Filters: <code>=</code><code>!=</code><code>&gt;</code><code>&gt;=</code><code>&lt;</code>
<code>&lt;=</code><code>in a,b</code><code>contains</code>.</p>
</fieldset><button type='submit'>Run</button></form>%s""" % (
            self.message(message), v("name"), options, v("by"), v("measures", "count\nsum balance"), v("where"),
            v("sort"), v("limit"), html.escape(chosen),
            "".join("<code>%s</code>" % html.escape(f) for f in fields_list), result)
        return self.shell("Explore", "Ad hoc reports over the book, the core files, the budget and profitability.",
                          body, "/explore")

    def explore_csv(self, fields):
        import csv
        import io
        q = query.run(form_to_query(fields), self.tables)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(q["columns"])
        w.writerows(q["rows"])
        if q["total"]:
            w.writerow(q["total"])
        return out.getvalue()

    def explore_save(self, fields):
        spec = form_to_query(fields)
        query.run(spec, self.tables)            # refuse to save a query that does not run
        slug = re.sub(r"[^a-z0-9]+", "-", spec["name"].lower()).strip("-") or "query"
        folder = os.path.join(self.folder, "queries")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, slug + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(spec, handle, indent=2)
        return self.explore_page(dict(fields, run="1"), "Saved as queries/%s.json: the next report run includes it."
                                 % slug)

    def report_file(self, path):
        """A file from the folder's report directory, or None."""
        root = os.path.realpath(os.path.join(self.folder, "report"))
        target = os.path.realpath(os.path.join(root, urllib.parse.unquote(path)))
        if not target.startswith(root + os.sep) or not os.path.isfile(target):
            return None
        return target


TYPES = {".html": "text/html; charset=utf-8", ".csv": "text/csv; charset=utf-8",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def handler_for(server):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, body, status=200, kind="text/html; charset=utf-8", filename=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            if filename:
                self.send_header("Content-Disposition", "attachment; filename=%s" % filename)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _fields(self, text):
            return {k: v[0] for k, v in urllib.parse.parse_qs(text, keep_blank_values=True).items()}

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            fields = self._fields(url.query)
            try:
                if url.path in ("/", "/index.html"):
                    return self._send(server.page())
                if url.path == "/pricing":
                    return self._send(server.pricing_page(fields))
                if url.path == "/newproduct":
                    return self._send(server.newproduct_page())
                if url.path == "/trade":
                    return self._send(server.trade_page())
                if url.path == "/explore":
                    return self._send(server.explore_page(fields))
                if url.path == "/explore.csv":
                    return self._send(server.explore_csv(fields), kind="text/csv; charset=utf-8",
                                      filename="keel-query.csv")
                if url.path.startswith("/report/"):
                    path = server.report_file(url.path[len("/report/"):])
                    if path:
                        with open(path, "rb") as handle:
                            return self._send(handle.read(), kind=TYPES.get(os.path.splitext(path)[1],
                                                                            "application/octet-stream"))
            except (model.InputError, ValueError) as error:
                return self._send(server.shell("Error", "", server.message(str(error)), ""), 400)
            self._send("<p>Not found. <a href='/'>Back</a></p>", 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            fields = self._fields(self.rfile.read(length).decode("utf-8"))
            try:
                if self.path == "/whatif":
                    return self._send(server.run(fields))
                if self.path == "/newproduct":
                    return self._send(server.newproduct_run(fields))
                if self.path == "/trade":
                    return self._send(server.trade_run(fields))
                if self.path == "/explore/save":
                    return self._send(server.explore_save(fields))
            except (model.InputError, ValueError) as error:
                return self._send(server.page("That what-if could not run: %s" % error), 400)
            self._send("<p>Not found.</p>", 404)

        def log_message(self, *args):
            pass

    return Handler


def serve(folder, port=8750):
    server = Server(folder)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler_for(server))
    print("Keel: http://127.0.0.1:%d/  (what-if, pricing, new product, explore; Ctrl+C to stop)" % httpd.server_address[1])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
