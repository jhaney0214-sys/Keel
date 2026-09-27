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

    def rate(self, months):
        """Annual rate in percent at a tenor in months."""
        t = self.tenors
        if months <= t[0]:
            return self.rates[0]
        if months >= t[-1]:
            return self.rates[-1]
        i = bisect.bisect_right(t, months)
        w = (months - t[i - 1]) / (t[i] - t[i - 1])
        return self.rates[i - 1] + w * (self.rates[i] - self.rates[i - 1])


class Scenario(object):
    """How far the base curve has moved, in basis points, in each month."""

    def __init__(self, name, shock_bp=0.0, ramp_months=0, floor=0.0):
        self.name = name
        self.shock_bp = float(shock_bp)
        self.ramp_months = int(ramp_months)
        self.floor = floor

    @property
    def parallel(self):
        """Instantaneous and sustained: the kind NEV is measured under."""
        return self.ramp_months == 0

    def shift_bp(self, month):
        if self.ramp_months <= 0:
            return self.shock_bp
        return self.shock_bp * min(1.0, float(month) / self.ramp_months)

    def rate(self, curve, month, tenor):
        """The scenario rate, in percent, at `tenor` as seen in `month`
        (month 0 is the analysis date). Floored, so a down shock cannot push
        a rate below `floor`."""
        value = curve.rate(tenor) + self.shift_bp(month) / 100.0
        return value if self.floor is None else max(self.floor, value)

    def __repr__(self):
        return "Scenario(%r)" % self.name


def standard_scenarios(floor=0.0):
    """Base, and parallel shocks of 100 to 300bp each way."""
    out = [Scenario("base", 0, floor=floor)]
    for bp in (-300, -200, -100, 100, 200, 300):
        out.append(Scenario("%+d" % bp, bp, floor=floor))
    return out
