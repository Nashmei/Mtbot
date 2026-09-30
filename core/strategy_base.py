"""Shared contract for every machine-executable strategy.

A strategy is a pure function of a :class:`MarketContext` (closed-bar market
data only) that returns a decision dict.  The same entry point is used by the
live engine and by the bar-replay backtester, so backtest and production run
identical logic by construction.

Decision contract
-----------------
Required keys:
  decision      : 'SIGNAL' | 'WAIT' | 'REJECT'
  strategy_id   : registry id
  side          : 'BUY' | 'SELL' | 'NONE'
  confidence    : 0..100
  regime        : 'TREND' | 'RANGE' | 'BREAKOUT' | 'VOLATILE' | 'NO_TRADE'
  reason_code   : short machine token
  reason        : human readable
Optional keys used by execution / management / charting:
  entry, sl_price, tp_price, tp1, tp2
  protection_pct, trailing_trigger_pct, trailing_gap_pct
  management    : per-strategy exit behaviour overrides
  draw          : {levels:[...], zones:[...], indicators:[...]} for charting
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import indicators as ta
from .models import Regime, Side


@dataclass(frozen=True)
class StrategySpec:
    id: str
    name: str
    family: str
    # analysis/context/trigger timeframes, e.g. ('M15','M5','M1')
    timeframes: tuple
    # symbols the strategy is designed for; 'FX' / 'XAUUSD' / '*' wildcards
    symbols: tuple
    # regimes where the strategy is allowed to fire
    regimes: tuple
    # regimes where it must never fire (hard block)
    forbidden: tuple = ()
    indicators: tuple = ()
    tier: str = 'B'
    direction: str = 'BOTH'
    holding: str = 'intraday'
    # A/B strategies are approved combinations. C-tier strategies are
    # candidates that only trade after the registry has live/backtest
    # evidence; they start disabled until ``trades >= TRIAL_MIN_TRADES`` and
    # positive expectancy are observed for that strategy/symbol/regime combo.
    trial: bool = False
    # minimum confidence from the account threshold before entry
    min_confidence: float = 60.0
    notes: str = ''


class MarketContext:
    """Immutable view of one symbol at one decision instant (closed bars)."""

    def __init__(self, symbol, tick, info, frames, session, regime, regime_detail=None):
        self.symbol = symbol
        self.tick = tick
        self.info = info
        self.frames = frames  # {'M1': rows, 'M5': rows, ...} closed bars
        self.session = session
        self.regime = regime
        self.regime_detail = regime_detail or {}
        self.point = float(getattr(info, 'point', 0) or 0) or 1e-5
        self.digits = int(getattr(info, 'digits', 5) or 5)
        self.bid = float(getattr(tick, 'bid', 0) or 0)
        self.ask = float(getattr(tick, 'ask', 0) or 0)
        self.mid = (self.bid + self.ask) / 2.0 if self.bid and self.ask else self.bid or self.ask
        self.spread = max(self.ask - self.bid, 0.0)
        self.spread_points = self.spread / self.point
        self._cache = {}

    # -- data helpers -------------------------------------------------
    def frame(self, name):
        return self.frames.get(name) or []

    def last_closed(self, name):
        rows = self.frame(name)
        return rows[-1] if rows else None

    def series(self, name, key):
        return [ta._f(r.get(key)) for r in self.frame(name)]

    def cached(self, key, producer):
        if key not in self._cache:
            self._cache[key] = producer()
        return self._cache[key]

    def atr(self, name, period=14):
        return self.cached(
            f'atr:{name}:{period}',
            lambda: ta.atr(self.frame(name), period),
        )

    def price_for(self, side):
        return self.ask if side == Side.BUY else self.bid

    @property
    def is_gold(self):
        return 'XAU' in self.symbol.upper() or 'GOLD' in self.symbol.upper()

    @property
    def is_index(self):
        upper = self.symbol.upper()
        return any(tag in upper for tag in ('US30', 'US100', 'NAS', 'SPX', 'GER', 'DAX', 'UK100', 'JP225'))

    def regime_direction(self):
        return str(self.regime_detail.get('direction', 'NONE')).upper()

    def pip(self):
        """Pip size; most FX brokers quote 5 digits so a pip is 10 points."""
        if self.point >= 0.01:
            return self.point
        return self.point * 10.0


def session_of(epoch):
    """Classify an epoch (seconds) into a coarse FX session bucket (UTC)."""
    hour = datetime.fromtimestamp(float(epoch), timezone.utc).hour
    if 0 <= hour < 7:
        return 'ASIA'
    if 7 <= hour < 12:
        return 'LONDON'
    if 12 <= hour < 16:
        return 'OVERLAP'
    if 16 <= hour < 21:
        return 'NEWYORK'
    return 'LATE'


def clamp(value, low, high):
    return max(low, min(high, value))


def confidence_score(base, *bonuses):
    return clamp(base + sum(b for b in bonuses if b is not None), 0.0, 97.0)


# ---------------------------------------------------------------------------
# Geometric helpers shared by every strategy
# ---------------------------------------------------------------------------

def structural_stop(ctx, side, price, anchor, buffer_points=3.0, min_atr_frac=0.10,
                    atr_frame='M1', atr_period=14, max_atr_mult=None,
                    min_atr_risk_frac=0.25):
    """Build a stop beyond a structural anchor, never tighter than noise.

    The stop is *derived from structure*: it is the anchor (swing/zone edge)
    plus a volatility/spread buffer.  It is never widened to force a ratio.
    """
    atr_series = ctx.atr(atr_frame, atr_period)
    atr_value = atr_series[-1] if atr_series and atr_series[-1] else 0.0
    buffer = max(ctx.spread * 1.5, ctx.point * buffer_points, atr_value * min_atr_frac)
    if side == Side.BUY:
        stop = anchor - buffer
        risk = price - stop
    else:
        stop = anchor + buffer
        risk = stop - price
    # A stop tighter than a fraction of ATR is inside market noise and gets
    # hit by ordinary quoting/spread.  Widen it to the noise floor instead of
    # silently accepting an unusable (or cost-dominated) risk distance.
    floor = max(atr_value * min_atr_risk_frac, ctx.spread * 2.0)
    if floor > 0 and risk < floor:
        if side == Side.BUY:
            stop = price - floor
        else:
            stop = price + floor
        risk = floor
    if max_atr_mult and atr_value > 0 and risk > atr_value * max_atr_mult:
        return None
    return stop, risk


def atr_target(ctx, side, price, risk, atr_mult=1.0, floor_r=1.2, atr_frame='M5', atr_period=14):
    atr_series = ctx.atr(atr_frame, atr_period)
    atr_value = atr_series[-1] if atr_series and atr_series[-1] else 0.0
    distance = max(risk * floor_r, atr_value * atr_mult, ctx.spread * 3.0)
    return price + distance if side == Side.BUY else price - distance


def rr_of(price, stop, target):
    risk = abs(price - stop)
    if risk <= 0:
        return 0.0
    return abs(target - price) / risk


def base_decision(spec, ctx, reason_code, reason, regime=None, confidence=0.0):
    return {
        'strategy_id': spec.id,
        'strategy': spec.id,
        'symbol': ctx.symbol,
        'decision': 'WAIT',
        'side': 'NONE',
        'direction': 'NONE',
        'confidence': float(confidence),
        'regime': str(regime or ctx.regime),
        'reason_code': reason_code,
        'reason': reason,
        'model': f'deterministic_{spec.id}',
        'management': dict(DEFAULT_MANAGEMENT),
    }


DEFAULT_MANAGEMENT = {
    'protection_pct': 40.0,
    'trailing_trigger_pct': 70.0,
    'trailing_gap_pct': 8.0,
    'partial_at_tp1': 0.0,
    'tp1_lock_to_breakeven': True,
    'max_hold_minutes': 0,
}


def signal_decision(spec, ctx, side, price, stop, target, confidence, reason,
                    reason_code='SETUP_COMPLETE', tp1=None, tp2=None,
                    management=None, draw=None, regime=None):
    risk = abs(price - stop)
    if risk <= 0:
        return base_decision(spec, ctx, 'INVALID_STOP', 'Structural stop is invalid')
    # Objective geometry validation shared by the live engine and the
    # backtester: the stop must be on the losing side and the target on the
    # winning side, and the stop may not sit inside the spread/noise band.
    if side == Side.BUY and not (stop < price < target):
        return base_decision(spec, ctx, 'INVALID_GEOMETRY',
                             'BUY plan requires stop < entry < target')
    if side == Side.SELL and not (target < price < stop):
        return base_decision(spec, ctx, 'INVALID_GEOMETRY',
                             'SELL plan requires target < entry < stop')
    if risk <= ctx.spread * 2.0:
        return base_decision(spec, ctx, 'STOP_INSIDE_SPREAD',
                             'Structural stop is inside twice the spread')
    digits = ctx.digits
    management = {**DEFAULT_MANAGEMENT, **(management or {})}
    out = base_decision(spec, ctx, reason_code, reason, regime=regime, confidence=confidence)
    out.update({
        'decision': 'SIGNAL',
        'side': side.value,
        'direction': side.value,
        'entry': float(price),
        'sl_price': round(float(stop), digits),
        'stop_loss': round(float(stop), digits),
        'tp_price': round(float(target), digits),
        'take_profit': round(float(target), digits),
        'risk_reward': rr_of(price, stop, target),
        'protection_pct': management['protection_pct'],
        'trailing_trigger_pct': management['trailing_trigger_pct'],
        'trailing_gap_pct': management['trailing_gap_pct'],
        'management': management,
    })
    if tp1:
        out['tp1'] = round(float(tp1), digits)
    if tp2:
        out['tp2'] = round(float(tp2), digits)
    if draw:
        out['draw'] = draw
    return out


def to_signal(decision, spec=None):
    """Convert a SIGNAL decision into the engine's :class:`Signal` model."""
    from .models import Signal
    if decision.get('decision') != 'SIGNAL':
        return None
    side = Side(str(decision['side']).upper())
    entry = float(decision.get('entry') or 0)
    stop = float(decision['sl_price'])
    if entry <= 0:
        entry = stop
    return Signal(
        side=side,
        strategy=decision['strategy_id'],
        confidence=float(decision.get('confidence', 0)) / 100.0,
        sl_points=max(abs(entry - stop), 1e-12),
        reason=str(decision.get('reason', '')),
        sl_price=stop,
        tp_price=float(decision['tp_price']),
        protection_pct=float(decision.get('protection_pct', 40.0)),
        trailing_gap_pct=float(decision.get('trailing_gap_pct', 8.0)),
    )


def regime_enum(name):
    try:
        return Regime(str(name).upper())
    except ValueError:
        return Regime.NO_TRADE


def finite(value, default=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default
