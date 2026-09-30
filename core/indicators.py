"""Dependency-free technical indicators used by every strategy.

All functions accept either MT5 structured ``numpy`` rate arrays or plain
sequences of dict rows.  Outputs are plain Python lists so the same code runs
inside the Wine/Windows interpreter that ships MT5 and inside the Linux test
interpreter (which has no third-party packages).

Every helper is causal: the value at index ``i`` only uses data ``<= i``.
That property is what keeps the live engine and the bar-replay backtest
free of look-ahead bias.
"""

from __future__ import annotations

import math


def rows(rates):
    """Normalise MT5 rate arrays (or list-of-dict) into a list of dicts."""
    if rates is None:
        return []
    names = getattr(getattr(rates, 'dtype', None), 'names', None)
    if names:
        return [{name: row[name] for name in names} for row in rates]
    out = []
    for row in rates:
        out.append(dict(row) if not isinstance(row, dict) else row)
    return out


def _f(value, default=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def closes(rows_):
    return [_f(r.get('close')) for r in rows_]


def highs(rows_):
    return [_f(r.get('high')) for r in rows_]


def lows(rows_):
    return [_f(r.get('low')) for r in rows_]


def opens(rows_):
    return [_f(r.get('open')) for r in rows_]


def volumes(rows_):
    out = []
    for r in rows_:
        v = r.get('tick_volume')
        if v in (None, 0):
            v = r.get('real_volume')
        out.append(max(_f(v), 0.0))
    return out


def spread_points(rows_):
    return [_f(r.get('spread')) for r in rows_]


def sma(values, period):
    """Full SMA series, ``None`` until the window is filled."""
    out = [None] * len(values)
    if period <= 0:
        return out
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values, period):
    """Full EMA series seeded with the SMA of the first window."""
    out = [None] * len(values)
    if not values or period <= 0:
        return out
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    for i in range(period, len(values)):
        prev = out[i - 1] if out[i - 1] is not None else seed
        out[i] = alpha * values[i] + (1.0 - alpha) * prev
    return out


def wilder(values, period):
    """Wilder smoothing (used by ATR/ADX/RSI)."""
    out = [None] * len(values)
    if len(values) < period or period <= 0:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, len(values)):
        prev = out[i - 1] if out[i - 1] is not None else seed
        out[i] = prev + (values[i] - prev) / period
    return out


def true_ranges(rows_):
    out = []
    for i, row in enumerate(rows_):
        high = _f(row.get('high'))
        low = _f(row.get('low'))
        if i == 0:
            out.append(high - low)
            continue
        prev_close = _f(rows_[i - 1].get('close'))
        out.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return out


def atr(rows_, period=14):
    return wilder(true_ranges(rows_), period)


def rsi(values, period=14):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains = []
    losses = []
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def adx(rows_, period=14):
    """Wilder ADX (+DI/-DI exposed through ``dmi``)."""
    values = dmi(rows_, period)
    return values['adx']


def dmi(rows_, period=14):
    n = len(rows_)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = _f(rows_[i].get('high')) - _f(rows_[i - 1].get('high'))
        down = _f(rows_[i - 1].get('low')) - _f(rows_[i].get('low'))
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
    tr = wilder(true_ranges(rows_), period)
    pdm = wilder(plus_dm, period)
    mdm = wilder(minus_dm, period)
    plus_di = [None] * n
    minus_di = [None] * n
    dx = [0.0] * n
    for i in range(n):
        if tr[i] in (None, 0) or pdm[i] is None or mdm[i] is None:
            continue
        p = 100.0 * pdm[i] / tr[i]
        m = 100.0 * mdm[i] / tr[i]
        plus_di[i] = p
        minus_di[i] = m
        dx[i] = 100.0 * abs(p - m) / (p + m) if (p + m) else 0.0
    return {'adx': wilder(dx, period), 'plus_di': plus_di, 'minus_di': minus_di}


def bollinger(values, period=20, mult=2.0):
    n = len(values)
    mid = sma(values, period)
    upper = [None] * n
    lower = [None] * n
    width = [None] * n
    for i in range(n):
        if mid[i] is None:
            continue
        window = values[i - period + 1:i + 1]
        mean = mid[i]
        var = sum((v - mean) ** 2 for v in window) / period
        sd = math.sqrt(max(var, 0.0))
        upper[i] = mean + mult * sd
        lower[i] = mean - mult * sd
        width[i] = 2.0 * mult * sd
    return {'mid': mid, 'upper': upper, 'lower': lower, 'sd': [
        (upper[i] - mid[i]) / mult if (upper[i] is not None and mid[i] is not None) else None
        for i in range(n)
    ], 'width': width}


def macd(values, fast=12, slow=26, signal=9):
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    n = len(values)
    line = [None] * n
    for i in range(n):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            line[i] = fast_ema[i] - slow_ema[i]
    filled = [v for v in line if v is not None]
    sig_filled = ema(filled, signal)
    sig = [None] * n
    j = 0
    for i in range(n):
        if line[i] is not None:
            sig[i] = sig_filled[j]
            j += 1
    hist = [None if (line[i] is None or sig[i] is None) else line[i] - sig[i] for i in range(n)]
    return {'line': line, 'signal': sig, 'hist': hist}


def keltner(rows_, period=20, atr_period=20, mult=1.5):
    c = closes(rows_)
    mid = ema(c, period)
    a = atr(rows_, atr_period)
    n = len(c)
    return {
        'mid': mid,
        'upper': [None if (mid[i] is None or a[i] is None) else mid[i] + mult * a[i] for i in range(n)],
        'lower': [None if (mid[i] is None or a[i] is None) else mid[i] - mult * a[i] for i in range(n)],
    }


def stochastic(rows_, k_period=14, d_period=3, smooth=3):
    h = highs(rows_)
    l = lows(rows_)
    c = closes(rows_)
    n = len(c)
    raw = [None] * n
    for i in range(k_period - 1, n):
        hh = max(h[i - k_period + 1:i + 1])
        ll = min(l[i - k_period + 1:i + 1])
        span = hh - ll
        raw[i] = 50.0 if span <= 0 else (c[i] - ll) / span * 100.0
    filled = [v for v in raw if v is not None]
    smoothed = sma(filled, smooth)
    out = [None] * n
    j = 0
    for i in range(n):
        if raw[i] is not None:
            out[i] = smoothed[j]
            j += 1
    filled2 = [v for v in out if v is not None]
    sig = sma(filled2, d_period)
    out_d = [None] * n
    j = 0
    for i in range(n):
        if out[i] is not None:
            out_d[i] = sig[j]
            j += 1
    return {'k': out, 'd': out_d}


def donchian(rows_, period=20):
    h = highs(rows_)
    l = lows(rows_)
    n = len(rows_)
    upper = [None] * n
    lower = [None] * n
    for i in range(period - 1, n):
        upper[i] = max(h[i - period + 1:i + 1])
        lower[i] = min(l[i - period + 1:i + 1])
    return {'upper': upper, 'lower': lower}


def session_vwap(rows_):
    """VWAP that resets at each new UTC day of the supplied bars."""
    n = len(rows_)
    out = [None] * n
    day = None
    num = 0.0
    den = 0.0
    for i, row in enumerate(rows_):
        ts = _f(row.get('time'))
        current = int(ts // 86400)
        if current != day:
            day = current
            num = 0.0
            den = 0.0
        typical = (_f(row.get('high')) + _f(row.get('low')) + _f(row.get('close'))) / 3.0
        vol = max(_f(row.get('tick_volume')) or _f(row.get('real_volume')), 1.0)
        num += typical * vol
        den += vol
        out[i] = num / den if den else typical
    return out


def highest(values, start, end=None):
    end = len(values) if end is None else end
    window = [v for v in values[start:end] if v is not None]
    return max(window) if window else None


def lowest(values, start, end=None):
    end = len(values) if end is None else end
    window = [v for v in values[start:end] if v is not None]
    return min(window) if window else None


def percentile(values, q):
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = max(0.0, min(1.0, q)) * (len(clean) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    return clean[lo] + (clean[hi] - clean[lo]) * (pos - lo)


def swing_points(rows_, left=2, right=2):
    """Confirmed fractal swings. ``right`` bars of confirmation (causal)."""
    h = highs(rows_)
    l = lows(rows_)
    n = len(rows_)
    out = []
    for i in range(left, n - right):
        window_high = h[i - left:i + right + 1]
        window_low = l[i - left:i + right + 1]
        if h[i] == max(window_high):
            out.append({'index': i, 'price': h[i], 'type': 'HIGH'})
        elif l[i] == min(window_low):
            out.append({'index': i, 'price': l[i], 'type': 'LOW'})
    return out


def candle(row):
    o = _f(row.get('open'))
    h = _f(row.get('high'))
    l = _f(row.get('low'))
    c = _f(row.get('close'))
    rng = max(h - l, 1e-12)
    body = abs(c - o)
    return {
        'open': o, 'high': h, 'low': l, 'close': c,
        'range': rng, 'body': body, 'body_ratio': body / rng,
        'upper_wick': h - max(o, c), 'lower_wick': min(o, c) - l,
        'bull': c > o, 'bear': c < o,
        'close_position': (c - l) / rng,
    }


def linreg_slope(values, period):
    window = [v for v in values[-period:] if v is not None]
    n = len(window)
    if n < max(3, period // 2):
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(window) / n
    num = sum((i - mean_x) * (window[i] - mean_y) for i in range(n))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0


def ema_stack(closes_, periods):
    """Return ``[ema_n for n in periods]`` evaluated on the last bar."""
    out = []
    for period in periods:
        series = ema(closes_, period)
        out.append(series[-1] if series else None)
    return out


def pips_to_points(pips, point, pip_size=None):
    pip = pip_size if pip_size else (point * 10.0)
    return pips * pip / point
