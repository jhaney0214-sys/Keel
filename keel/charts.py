"""Charts as inline SVG: no library, no network, readable offline and in print.

Three forms cover what an ALCO packet needs, each chosen for the data's job:

* `diverging_bars`: polarity. NII or NEV change by scenario, up in blue and
  down in red about a zero line, with the policy limit drawn as a dashed line.
* `columns`: magnitude over ordered bands or years (the repricing gap, which
  is also signed, and net income by year).
* `line`: change over time (available liquidity through the stress year).

Colour comes from CSS variables the report defines for light and dark (the
dataviz reference palette's blue, its red as the diverging pole, validated
with its script in both modes). Text is never in a series colour. Every mark
carries a <title>, so hovering shows its exact value, on a hit area taller
than the mark; every chart sits beside a table with the same numbers.
"""

import html
import math

WIDTH = 680


def _esc(text):
    return html.escape(str(text), quote=True)


def diverging_bars(rows, fmt, title, limit=None, limit_label="limit", both_sides=True):
    """Horizontal bars about zero. `rows` are (label, value, tooltip). `limit`
    (a positive number in the values' units) draws a dashed line at -limit and,
    with `both_sides`, at +limit. Each side of zero is only as wide as its
    data (and the limit) needs, so a one-sided limit does not waste half the
    chart on empty space."""
    row_h, top, left, right = 24, 44, 104, 56
    height = top + row_h * len(rows) + 24
    lows = [-v for _, v, _ in rows if v < 0] + ([limit] if limit else [])
    highs = [v for _, v, _ in rows if v > 0] + ([limit] if limit and both_sides else [])
    neg = max(lows + [0.0]) * 1.2
    pos = max(highs + [0.0]) * 1.2
    if neg + pos == 0:
        pos = 1.0
    # a side with data never shrinks below a sixth of the plot, so its labels fit
    neg, pos = (max(neg, (neg + pos) / 6) if neg else 0.0), (max(pos, (neg + pos) / 6) if pos else 0.0)
    plot = WIDTH - left - right
    scale = plot / (neg + pos)
    zero = left + neg * scale
    parts = ['<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="%s">' % (WIDTH, height, _esc(title))]
    parts.append('<text class="c-title" x="0" y="16">%s</text>' % _esc(title))
    ticks = [t for t in _ticks(-neg, pos, 6) if t and -neg <= t <= pos]
    for x in ticks:
        px = zero + x * scale
        parts.append('<line class="c-grid" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (px, top - 4, px, height - 20))
        parts.append('<text class="c-tick" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (px, height - 6, _esc(fmt(x))))
    if limit:
        for sign in ((-1, 1) if both_sides else (-1,)):
            px = zero + sign * limit * scale
            parts.append('<line class="c-limit" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (px, top - 10, px, height - 20))
            parts.append('<text class="c-tick" x="%.1f" y="%d" text-anchor="%s">%s</text>' % (
                px + (4 if sign < 0 else -4), top - 12, "start" if sign < 0 else "end",
                _esc("%s %s" % (limit_label, fmt(sign * limit)))))
    for i, (label, value, tip) in enumerate(rows):
        y = top + i * row_h
        w = abs(value) * scale
        x = zero if value >= 0 else zero - w
        cls = "c-pos" if value >= 0 else "c-neg"
        parts.append('<g class="c-mark"><title>%s</title>' % _esc(tip))
        parts.append('<rect class="c-hit" x="0" y="%d" width="%d" height="%d"/>' % (y, WIDTH, row_h))
        parts.append('<rect class="%s" x="%.1f" y="%d" width="%.1f" height="12" rx="2"/>' % (cls, x, y + 6, max(w, 1)))
        parts.append('<text class="c-label" x="%d" y="%d" text-anchor="end">%s</text>' % (left - 10, y + 16, _esc(label)))
        vx = x + w + 6 if value >= 0 else x - 6
        parts.append('<text class="c-value" x="%.1f" y="%d" text-anchor="%s">%s</text></g>' % (
            vx, y + 16, "start" if value >= 0 else "end", _esc(fmt(value))))
    parts.append('<line class="c-axis" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (zero, top - 4, zero, height - 20))
    parts.append("</svg>")
    return "".join(parts)


def columns(rows, fmt, title, signed=False):
    """Vertical bars. `rows` are (label, value, tooltip). Signed columns hang
    from a zero line in the middle and take the diverging colours."""
    top, bottom, left, right = 28, 40, 64, 12
    height = 240
    plot_h = height - top - bottom
    plot_w = WIDTH - left - right
    values = [v for _, v, _ in rows]
    hi = max(values + [0.0])
    lo = min(values + [0.0])
    if not signed:
        lo = 0.0
    span = (hi - lo) or 1.0
    scale = plot_h / (span * 1.1)
    zero = top + (hi * 1.05) * scale if signed else top + plot_h
    step = plot_w / max(len(rows), 1)
    bar = min(48.0, step * 0.55)
    parts = ['<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="%s">' % (WIDTH, height, _esc(title))]
    parts.append('<text class="c-title" x="0" y="16">%s</text>' % _esc(title))
    for i, (label, value, tip) in enumerate(rows):
        cx = left + step * (i + 0.5)
        h = abs(value) * scale
        y = zero - h if value >= 0 else zero
        cls = ("c-pos" if value >= 0 else "c-neg") if signed else "c-pos"
        parts.append('<g class="c-mark"><title>%s</title>' % _esc(tip))
        parts.append('<rect class="c-hit" x="%.1f" y="%d" width="%.1f" height="%d"/>' % (cx - step / 2, top, step, plot_h))
        parts.append('<rect class="%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2"/>' % (cls, cx - bar / 2, y, bar, max(h, 1)))
        vy = y - 5 if value >= 0 else y + h + 13
        parts.append('<text class="c-value" x="%.1f" y="%.1f" text-anchor="middle">%s</text>' % (cx, vy, _esc(fmt(value))))
        parts.append('<text class="c-label" x="%.1f" y="%d" text-anchor="middle">%s</text></g>' % (cx, height - 18, _esc(label)))
    parts.append('<line class="c-axis" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (left, zero, WIDTH - right, zero))
    parts.append("</svg>")
    return "".join(parts)


def _ticks(lo, hi, count=4):
    """Round axis ticks covering lo..hi, and the axis ends they imply."""
    raw = (hi - lo) / count or 1.0
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first = math.floor(lo / step) * step
    last = math.ceil(hi / step) * step
    n = int(round((last - first) / step))
    return [first + i * step for i in range(n + 1)]


def line(points, fmt, title, x_label="month", reference=None, reference_label="", zero=True):
    """One series over time. `points` are (x, y, tooltip). `reference` draws a
    dashed horizontal line (zero, or a floor) with its label. `zero` keeps
    zero on the axis; a ratio that lives far from zero can drop it."""
    top, bottom, left, right = 28, 36, 72, 16
    height = 230
    plot_h = height - top - bottom
    plot_w = WIDTH - left - right
    ys = [y for _, y, _ in points] + ([reference] if reference is not None else [])
    ticks = _ticks(min(ys + ([0.0] if zero else [])), max(ys))
    lo, hi = ticks[0], ticks[-1]
    span = (hi - lo) or 1.0
    xs = [x for x, _, _ in points]
    x0, x1 = min(xs), max(xs)
    px = lambda x: left + (x - x0) / ((x1 - x0) or 1) * plot_w  # noqa: E731
    py = lambda y: top + (hi - y) / span * plot_h  # noqa: E731
    parts = ['<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="%s">' % (WIDTH, height, _esc(title))]
    parts.append('<text class="c-title" x="0" y="16">%s</text>' % _esc(title))
    for v in ticks:
        parts.append('<line class="c-grid" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (left, py(v), WIDTH - right, py(v)))
        parts.append('<text class="c-tick" x="%d" y="%.1f" text-anchor="end">%s</text>' % (left - 8, py(v) + 4, _esc(fmt(v))))
    if reference is not None:
        parts.append('<line class="c-limit" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (left, py(reference), WIDTH - right, py(reference)))
        parts.append('<text class="c-tick" x="%d" y="%.1f" text-anchor="end">%s</text>' % (WIDTH - right, py(reference) - 5, _esc(reference_label)))
    path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", px(x), py(y)) for i, (x, y, _) in enumerate(points))
    parts.append('<path class="c-line" d="%s"/>' % path)
    step = plot_w / max(len(points) - 1, 1)
    for x, y, tip in points:
        parts.append('<g class="c-mark"><title>%s</title><rect class="c-hit" x="%.1f" y="%d" width="%.1f" height="%d"/>'
                     '<circle class="c-dot" cx="%.1f" cy="%.1f" r="4"/></g>' % (
                         _esc(tip), px(x) - step / 2, top, step, plot_h, px(x), py(y)))
        parts.append('<text class="c-tick" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (px(x), height - 16, _esc(x)))
    parts.append('<text class="c-tick" x="%d" y="%d" text-anchor="middle">%s</text>' % (left + plot_w / 2, height - 2, _esc(x_label)))
    parts.append("</svg>")
    return "".join(parts)
