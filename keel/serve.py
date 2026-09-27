"""A what-if page in the browser, served from this machine to this machine.

    python -m keel serve examples/mid-cu        # then open http://127.0.0.1:8750/

The page lists the levers an ALCO asks about (planned growth, share betas
and decay, prepayment speeds, a new borrowing, a sale), prefilled with
today's assumptions. Submitting it builds the same what-if a JSON file would,
runs it through the same measures, and shows base and what-if side by side,
with the JSON to save if the answer is worth keeping.

It listens on 127.0.0.1 only, so nothing outside this computer can reach it,
and it calls nothing outside it either. The credit union's files stay where
they are; each run reads them fresh from memory.
"""

import html
import http.server
import json
import os
import threading
import urllib.parse

from keel import model, whatif
from keel.report import STYLE

LEVERS = (  # (field, label, which products show it)
    ("growth", "Planned growth, %/yr", lambda p: p.growth or p.charge_off or p.runoff),
    ("beta", "Share beta, %", lambda p: p.beta),
    ("runoff", "Decay or paydown, %/yr", lambda p: p.runoff),
    ("cpr", "Prepayment (CPR), %/yr", lambda p: p.cpr),
)


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


class Server(object):

    def __init__(self, folder):
        from keel.__main__ import load
        self.folder = folder
        self.positions, self.assumptions, self.raw, _ = load(folder)
        self.base = whatif.key_measures(self.positions, self.assumptions)
        self.lock = threading.Lock()

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
        base_rows = "".join("<tr><td>%s</td><td class='num'>%s</td></tr>" % (html.escape(label), whatif._fmt(v, kind))
                            for label, v, kind in self.base)
        return """<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'><title>Keel what-if</title>
<style>%s input,select,button{font:inherit;padding:.25rem .4rem;} button{margin-top:1rem;padding:.5rem 1rem;}
fieldset{border:1px solid var(--rule);margin:1rem 0;padding:.75rem 1rem;} legend{font-weight:600;}</style></head>
<body><main><h1>What-if</h1><p class='muted'>%s, as of %s. Served from this computer to this computer;
nothing leaves it. Dollars in thousands.</p>%s
<form method='post' action='/whatif'>
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
<h2>Base</h2><div class='wrap'><table><tbody>%s</tbody></table></div>
</main></body></html>""" % (
            STYLE, html.escape(os.path.basename(os.path.abspath(self.folder))), html.escape(a.as_of),
            "<p><strong>%s</strong></p>" % html.escape(message) if message else "",
            "".join("<th>%s</th>" % html.escape(label) for _, label, _ in LEVERS), "".join(rows), sources,
            "".join("<option>%s</option>" % html.escape(p) for p in sellable), base_rows)

    def run(self, fields):
        spec = form_to_spec(fields, self.assumptions)
        if not spec["assumptions"] and not spec["actions"]:
            return self.page("Nothing was changed, so there is nothing to compare.")
        with self.lock:
            changed, changed_assumptions, notes = whatif.apply(self.positions, self.raw, spec)
            after = whatif.key_measures(changed, changed_assumptions)
        page = whatif.comparison(spec["name"], notes, self.base, after)
        saved = ("<h2>Keep this what-if</h2><p class='muted'>Save as a .json file and run it again with "
                 "<code>python -m keel whatif %s file.json</code>, which also writes its full report.</p>"
                 "<pre>%s</pre><p><a href='/'>Another what-if</a></p>" % (
                     html.escape(self.folder), html.escape(json.dumps(spec, indent=2))))
        page = page.replace("<p class='muted'>The what-if's own full report is <a href='report.html'>report.html</a> "
                            "in this folder.</p>", "")
        return page.replace("</main>", saved + "</main>")


def handler_for(server):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, body, status=200):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(server.page())
            else:
                self._send("<p>Not found. <a href='/'>Back</a></p>", 404)

        def do_POST(self):
            if self.path != "/whatif":
                return self._send("<p>Not found.</p>", 404)
            length = int(self.headers.get("Content-Length") or 0)
            fields = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"),
                                                                keep_blank_values=True).items()}
            try:
                self._send(server.run(fields))
            except (model.InputError, ValueError) as error:
                self._send(server.page("That what-if could not run: %s" % error), 400)

        def log_message(self, *args):
            pass

    return Handler


def serve(folder, port=8750):
    server = Server(folder)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler_for(server))
    print("Keel what-if page: http://127.0.0.1:%d/  (Ctrl+C to stop)" % httpd.server_address[1])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
