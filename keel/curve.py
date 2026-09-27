"""Yield curves and rate scenarios.

A curve is a set of (tenor in months, annual rate in percent) points, read by
linear interpolation and held flat beyond its ends. A scenario moves the whole
curve: an instantaneous, parallel and sustained shock (the NCUA NEV
Supervisory Test is +300bp of this kind), or a ramp that reaches its full
move over a number of months and stays there.
"""

import bisect


class Curve(object):

    def __init__(self, points):
        if not points:
            raise ValueError("a curve needs at least one point")
        pairs = sorted((float(t), float(r)) for t, r in points.items()) \
            if isinstance(points, dict) else sorted((float(t), float(r)) for t, r in points)
        self.tenors = [t for t, _ in pairs]
        self.rates = [r for _, r in pairs]
        # A curve never changes once built, and the projection asks it the
        # same few tenors millions of times: remember each answer.
        self._cache = {}

    def rate(self, months):
        """Annual rate in percent at a tenor in months."""
        cached = self._cache.get(months)
        if cached is not None:
            return cached
        value = self._rate(months)
        self._cache[months] = value
        return value

    def _rate(self, months):
        t = self.tenors
        if months <= t[0]:
            return self.rates[0]
        if months >= t[-1]:
            return self.rates[-1]
        i = bisect.bisect_right(t, months)
        w = (months - t[i - 1]) / (t[i] - t[i - 1])
        return self.rates[i - 1] + w * (self.rates[i] - self.rates[i - 1])


class Scenario(object):
    """How far the base curve has moved, in basis points, in each month.

    Parallel by default. With `shape` ({tenor in months: bp}), the move
    differs along the curve, interpolated between the given tenors and held
    flat beyond them: {"1": 200, "120": 0} is a flattener that lifts the short
    end 200bp and leaves ten years unchanged. `shock_bp` is then ignored."""

    def __init__(self, name, shock_bp=0.0, ramp_months=0, floor=0.0, shape=None, use_path=True, basis=None):
        self.name = name
        # Basis: extra moves, in bp, for named indexes (and "shares", for
        # administered share rates) on top of the curve's own move, reached
        # over the same ramp. {"PRIME": -50} holds prime 50bp under where the
        # curve would take it: the spread between them is the risk.
        self.basis = dict(basis or {})
        self.shock_bp = float(shock_bp)
        self.ramp_months = int(ramp_months)
        self.floor = floor
        self.shape = Curve(shape) if shape else None
        # False for "rates unchanged": the one scenario that ignores the
        # base-case rate path, so the plan can be read against it.
        self.use_path = use_path
        self._shifts = {}               # (month, tenor) -> bp; a scenario never changes once built

    @property
    def instantaneous(self):
        """Applied on the analysis date and held: the kind NEV is measured under."""
        return self.ramp_months == 0

    @property
    def parallel(self):
        return self.instantaneous and self.shape is None

    def shift_bp(self, month, tenor=None):
        """The move at `tenor` (default: the ten-year point for a shaped
        scenario) in `month`."""
        key = (month, tenor)
        cached = self._shifts.get(key)
        if cached is None:
            cached = self._shifts[key] = self._shift_bp(month, tenor)
        return cached

    def _shift_bp(self, month, tenor):
        full = self.shape.rate(120 if tenor is None else tenor) if self.shape else self.shock_bp
        if self.ramp_months <= 0:
            return full
        return full * min(1.0, float(month) / self.ramp_months)

    def basis_bp(self, key, month):
        """The scenario's extra move for an index (or "shares") in `month`."""
        full = self.basis.get(key, 0.0)
        if not full or self.ramp_months <= 0:
            return full
        return full * min(1.0, float(month) / self.ramp_months)

    def rate(self, curve, month, tenor):
        """The scenario rate, in percent, at `tenor` as seen in `month`
        (month 0 is the analysis date). Floored, so a down shock cannot push
        a rate below `floor`."""
        value = curve.rate(tenor) + self.shift_bp(month, tenor) / 100.0
        return value if self.floor is None else max(self.floor, value)

    def __repr__(self):
        return "Scenario(%r)" % self.name


class RatePath(object):
    """Where rates go in the base case, as a move (in basis points) from
    today's curve by month and tenor. Shocks and ramps are applied on top.

    * ``forward``: the curve's own implied forwards. Today's par rates are
      read as annually compounded zero rates, and the rate for `tenor`
      months starting in `month` is the one that links the two:
      (1 + z(m + T))^((m + T)/12) = (1 + z(m))^(m/12) * (1 + f)^(T/12).
    * ``forecast``: a management or economist forecast, given as rates at
      some months and tenors. Each forecast month's moves are interpolated
      across tenors (a forecast of only Fed funds and the ten-year still
      moves the five-year), and between months linearly from today's curve;
      after the last forecast month the curve holds.

    Only the going-concern plan follows the path. NEV and every other
    present value discount on today's curve, because a market value is
    today's price."""

    def __init__(self, kind, curve, rows=None):
        if kind not in ("forward", "forecast"):
            raise ValueError("base case must be flat, forward or forecast, not %r" % kind)
        self.kind = kind
        self.curve = curve
        self.cache = {}
        self.months = []
        if kind == "forecast":
            by_month = {}
            for month, tenor, rate in rows or []:
                by_month.setdefault(int(month), {})[float(tenor)] = 100.0 * (float(rate) - curve.rate(float(tenor)))
            if not by_month:
                raise ValueError("a forecast base case needs at least one forecast rate")
            self.months = sorted(by_month)
            self.moves = {m: Curve(by_month[m]) for m in self.months}

    def _zero(self, months):
        return self.curve.rate(max(months, 1)) / 100.0

    def move_bp(self, month, tenor):
        key = (month, tenor)
        if key in self.cache:
            return self.cache[key]
        if month <= 0:
            value = 0.0
        elif self.kind == "forward":
            m, t = float(month), float(max(tenor, 1))
            grown = (1.0 + self._zero(m + t)) ** ((m + t) / 12.0)
            start = (1.0 + self._zero(m)) ** (m / 12.0)
            forward = (grown / start) ** (12.0 / t) - 1.0
            value = 100.0 * (100.0 * forward - self.curve.rate(tenor))
        else:
            value = self._forecast(month, tenor)
        self.cache[key] = value
        return value

    def _forecast(self, month, tenor):
        months = self.months
        if month >= months[-1]:
            return self.moves[months[-1]].rate(tenor)
        prior_m, prior_v = 0, 0.0
        for m in months:
            v = self.moves[m].rate(tenor)
            if month <= m:
                w = (month - prior_m) / float(m - prior_m)
                return prior_v + w * (v - prior_v)
            prior_m, prior_v = m, v
        return prior_v

    def rate(self, month, tenor):
        return self.curve.rate(tenor) + self.move_bp(month, tenor) / 100.0


def standard_scenarios(floor=0.0):
    """Base, and parallel shocks of 100 to 300bp each way."""
    out = [Scenario("base", 0, floor=floor)]
    for bp in (-300, -200, -100, 100, 200, 300):
        out.append(Scenario("%+d" % bp, bp, floor=floor))
    return out
