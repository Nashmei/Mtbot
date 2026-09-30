"""Deterministic, causal market-regime classification.

Regime is computed from closed bars only (M15 context, H1 structure) and is
the gate that decides *which strategies are allowed to run* on a symbol.

Outputs
-------
``RegimeResult`` carries:
  regime        : NO_TRADE | RANGE | TREND | BREAKOUT | VOLATILE
  direction     : UP | DOWN | NONE
  strength      : 0..1 quality of the classification
  detail        : raw indicator values for logging / charting
  blocked       : True when the market is untradable on purpose
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import indicators as ta
from .models import Regime


@dataclass
class RegimeResult:
    regime: Regime
    direction: str = 'NONE'
    strength: float = 0.0
    blocked: bool = False
    detail: dict = field(default_factory=dict)

    @property
    def name(self):
        return self.regime.value


def _classify(adx_value, slope, ema_fast, ema_slow, atr_v, atr_pct, width_pct,
              bar_range_ratio, breakout_up, breakout_dn, range_slope):
    if atr_v is None or atr_v <= 0:
        return Regime.NO_TRADE, 'NONE', 0.0, 'no_atr'

    trend_up = (adx_value is not None and adx_value >= 22
                and ema_fast is not None and ema_slow is not None
                and ema_fast > ema_slow and slope > 0)
    trend_dn = (adx_value is not None and adx_value >= 22
                and ema_fast is not None and ema_slow is not None
                and ema_fast < ema_slow and slope < 0)

    # Compression followed by an impulsive close outside the prior boundary.
    if breakout_up and width_pct is not None and width_pct <= 0.60:
        return Regime.BREAKOUT, 'UP', 0.6 + min(0.35, (bar_range_ratio - 1.4) * 0.25), 'breakout_up'
    if breakout_dn and width_pct is not None and width_pct <= 0.60:
        return Regime.BREAKOUT, 'DOWN', 0.6 + min(0.35, (bar_range_ratio - 1.4) * 0.25), 'breakout_down'

    # Violent expansion without directional agreement = untradable noise.
    if atr_pct is not None and atr_pct >= 0.85:
        if trend_up:
            return Regime.TREND, 'UP', 0.6, 'high_vol_trend_up'
        if trend_dn:
            return Regime.TREND, 'DOWN', 0.6, 'high_vol_trend_down'
        return Regime.VOLATILE, 'NONE', 0.5, 'volatility_expansion'

    if bar_range_ratio >= 2.5 and not (trend_up or trend_dn):
        return Regime.VOLATILE, 'NONE', 0.4, 'single_bar_spike'

    if trend_up:
        return Regime.TREND, 'UP', min(0.95, 0.5 + (adx_value - 22) / 40.0), 'adx_trend_up'
    if trend_dn:
        return Regime.TREND, 'DOWN', min(0.95, 0.5 + (adx_value - 22) / 40.0), 'adx_trend_down'

    if adx_value is not None and adx_value < 20 and abs(range_slope) <= 0.12:
        return Regime.RANGE, 'NONE', min(0.85, 0.5 + (20 - adx_value) / 30.0), 'flat_adx_range'

    if atr_pct is not None and atr_pct <= 0.35:
        return Regime.RANGE, 'NONE', 0.4, 'low_volatility_range'

    return Regime.NO_TRADE, 'NONE', 0.2, 'ambiguous'


class RegimeDetector:
    """Stateless classifier; safe to share across symbols."""

    def __init__(self, config=None):
        self.config = config

    def detect(self, ctx):
        frame = ctx.frame('M15')
        h1 = ctx.frame('H1')
        if len(frame) < 60 or len(h1) < 60:
            return RegimeResult(Regime.NO_TRADE, 'NONE', 0.0, True, {'reason': 'insufficient_history'})

        c15 = ta.closes(frame)
        adx_series = ta.adx(frame, 14)
        adx_value = adx_series[-1]
        atr_series = ta.atr(frame, 14)
        atr_values = [v for v in atr_series if v is not None]
        atr_v = atr_values[-1] if atr_values else None
        atr_pct = None
        if len(atr_values) >= 40:
            recent = atr_values[-1]
            window = atr_values[-120:]
            below = sum(1 for v in window if v <= recent)
            atr_pct = below / len(window)

        bb = ta.bollinger(c15, 20, 2.0)
        widths = [w for w in bb['width'] if w is not None]
        width_pct = None
        if len(widths) >= 40:
            recent_width = widths[-1]
            window = widths[-120:]
            below = sum(1 for w in window if w <= recent_width)
            width_pct = below / len(window)

        ema_fast = ta.ema(c15, 50)
        ema_slow = ta.ema(c15, 200)
        ema_fast_v = ema_fast[-1]
        ema_slow_v = ema_slow[-1]
        slope = ta.linreg_slope(c15[-30:], 20) or 0.0

        last = frame[-1]
        bar_range = ta._f(last.get('high')) - ta._f(last.get('low'))
        bar_range_ratio = (bar_range / atr_v) if atr_v else 0.0
        prior = frame[-21:-1]
        prior_high = max(ta.highs(prior)) if prior else None
        prior_low = min(ta.lows(prior)) if prior else None
        prior_range = (prior_high - prior_low) if (prior_high is not None and prior_low is not None) else None
        close = ta._f(last.get('close'))
        # A breakout needs BOTH an impulsive expansion bar and a decisive
        # displacement away from the prior box; a one-tick poke is not one.
        min_displacement = max(atr_v * 0.25 if atr_v else 0.0, ctx.point * 2.0)
        breakout_up = bool(prior_high is not None and close > prior_high + min_displacement
                           and bar_range_ratio >= 1.4)
        breakout_dn = bool(prior_low is not None and close < prior_low - min_displacement
                           and bar_range_ratio >= 1.4)

        h1_closes = ta.closes(h1)
        range_slope = 0.0
        if prior_high and prior_low and atr_v:
            range_slope = abs(ema_fast_v - ema_slow_v) / max(atr_v, 1e-12) if (
                ema_fast_v is not None and ema_slow_v is not None) else 0.0

        regime, direction, strength, code = _classify(
            adx_value, slope, ema_fast_v, ema_slow_v, atr_v, atr_pct, width_pct,
            bar_range_ratio, breakout_up, breakout_dn, range_slope,
        )

        detail = {
            'adx_m15': adx_value,
            'atr_m15': atr_v,
            'atr_percentile': atr_pct,
            'bb_width_percentile': width_pct,
            'ema50_m15': ema_fast_v,
            'ema200_m15': ema_slow_v,
            'slope_m15': slope,
            'bar_range_ratio': bar_range_ratio,
            'prior_range_m15': prior_range,
            'regime_code': code,
        }

        # Hard block: dead market or absurd spread relative to movement.
        if atr_v and atr_v > 0 and ctx.spread_points > 0:
            if ctx.spread > atr_v * 0.35:
                return RegimeResult(Regime.NO_TRADE, 'NONE', 0.0, True,
                                    {**detail, 'reason': 'spread_vs_atr'})
        if atr_v is not None and atr_v <= ctx.point * 8:
            return RegimeResult(Regime.NO_TRADE, 'NONE', 0.0, True,
                                {**detail, 'reason': 'micro_atr'})

        return RegimeResult(regime, direction, strength, False, detail)
