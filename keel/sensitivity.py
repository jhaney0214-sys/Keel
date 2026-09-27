"""Which assumptions the answer depends on: each key behavioural assumption
moved on its own, and the rate-risk measures run again.

Examiners ask how the credit union knows its assumptions are reasonable,
and which ones drive the result. This answers the second question
directly: deposit betas, deposit decay and prepayment speeds are each
scaled by half again and by half, and new-business loan spreads moved
25bp, one at a time; each variant is run through the same NII (+/-300bp)
and NEV (+/-300bp) measures as the report, and its limit statuses are
read against the same limits. An assumption whose plausible range turns a
limit from within to breach is one the credit union has to be able to
support with its own data (a deposit study, prepayment history).
"""

import dataclasses

from keel import engine, measures, model, results

#: (family, field, which products, how each variant changes the value)
FAMILIES = (
    ("Deposit betas", "beta", lambda p, spec: p.side == "liability" and spec.beta > 0,
     (("x1.5", lambda v: min(v * 1.5, 1.0)), ("x0.5", lambda v: v * 0.5))),
    ("Deposit decay", "runoff", lambda p, spec: p.side == "liability" and spec.runoff > 0,
     (("x1.5 (shorter life)", lambda v: min(v * 1.5, 0.95)), ("x0.5 (longer life)", lambda v: v * 0.5))),
    ("Prepayment speeds", "cpr", lambda p, spec: p.side == "asset" and spec.cpr > 0,
     (("x1.5", lambda v: min(v * 1.5, 0.95)), ("x0.5", lambda v: v * 0.5))),
    ("Rate-driven prepayment", "cpr_per_100bp", lambda p, spec: p.side == "asset" and spec.cpr_per_100bp > 0,
     (("x1.5", lambda v: v * 1.5), ("x0.5", lambda v: v * 0.5))),
    ("New-business loan spreads", "spread", lambda p, spec: p.side == "asset" and spec.new_term > 0
     and spec.charge_off > 0, (("-25bp", lambda v: v - 0.0025), ("+25bp", lambda v: v + 0.0025))),
)
KEYS = ("nii_decline_300", "nev_decline_300", "nev_ratio_min")


def measure(positions, a):
    """The rate-risk measures a variant is judged on."""
    by = {s.name: s for s in a.scenarios}
    nii = {}
    for name in ("base", "+300", "-300"):
        run = engine.going_concern(positions, a, by[name], months=12)
        nii[name] = sum(m.nii for m in run)
    nev = {name: measures.nev(positions, a, by[name]) for name in ("base", "+300", "-300")}
    worst_nii = min(nii["+300"] / nii["base"] - 1, nii["-300"] / nii["base"] - 1) if nii["base"] else 0.0
    worst_decline = min(nev[k].nev / nev["base"].nev - 1 for k in ("+300", "-300")) if nev["base"].nev else 0.0
    worst_ratio = min(nev[k].ratio for k in ("+300", "-300"))
    return {"nii_y1": nii["base"], "nii_decline_300": -100 * worst_nii, "nev_decline_300": -100 * worst_decline,
            "nev_ratio_min": 100 * worst_ratio}


def _status(a, values):
    known = {k: (kind, default) for k, kind, default, _ in model.LIMITS}
    out = {}
    for key in KEYS:
        kind, default = known[key]
        out[key] = results.evaluate(key, kind, a.limits.get(key, default), values[key], a.warning_band)
    return out


def run(positions, a, study=None):
    """{"baseline": {...}, "rows": [{family, variant, products, values, status, moves}]}.
    With `study` (deposits.recommended's {product: {field: percent}}), one
    more row runs the model on the deposit study's estimates."""
    from keel import parallel
    variants = []               # (row without values, the changed assumptions)
    if study:
        changed = dict(a.products)
        for name, values in study.items():
            if name in changed:
                changed[name] = dataclasses.replace(changed[name], **{k: v / 100.0 for k, v in values.items()})
        variants.append(({"family": "Deposit study", "variant": "as estimated", "field": "study",
                          "products": sorted(study)}, dataclasses.replace(a, products=changed)))
    for family, field, chosen, options in FAMILIES:
        products = sorted({p.product for p in positions if chosen(p, a.products[p.product])})
        if not products:
            continue
        for label, change in options:
            changed = dict(a.products)
            for name in products:
                spec = changed[name]
                changed[name] = dataclasses.replace(spec, **{field: change(getattr(spec, field))})
            variants.append(({"family": family, "variant": label, "field": field, "products": products},
                             dataclasses.replace(a, products=changed)))
    # Every variant is its own pass over the book: run them side by side.
    measured = parallel.run([("keel.sensitivity.measure", (positions, a), {})]
                            + [("keel.sensitivity.measure", (positions, b), {}) for _, b in variants])
    baseline = measured[0]
    base_status = _status(a, baseline)
    rows = []
    for (row, b), values in zip(variants, measured[1:]):
        status = _status(b, values)
        rows.append(dict(row, values=values, status=status, flips=[k for k in KEYS if status[k] != base_status[k]]))
    return {"baseline": baseline, "status": base_status, "rows": rows}


def driver(result):
    """The family that moves the NEV decline most, and by how many points."""
    best, move = None, 0.0
    for row in result["rows"]:
        d = abs(row["values"]["nev_decline_300"] - result["baseline"]["nev_decline_300"])
        if d > move:
            best, move = row, d
    return best, move
