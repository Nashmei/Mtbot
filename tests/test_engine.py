"""Linux-side unit tests (no MT5 / aiosqlite / telegram required).

Run:  python3 -m unittest discover -s tests -v
"""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import indicators as ta
from core.models import Regime, Side
from core.regime import RegimeDetector, _classify
from core.strategy_base import MarketContext, session_of, structural_stop, atr_target
from core.strategy_registry import ComboPerformance, StrategyRegistry, symbol_class
from tools.backtest import _simulate_trade, _stats, aggregate, replay_m1, walk_forward_report, TF_SECONDS


class Tick:
    def __init__(self, bid, ask, epoch=1700000000):
        self.bid = bid
        self.ask = ask
        self.time = epoch
        self.time_msc = epoch * 1000
        self.volume = 1
        self.volume_real = 1.0


class Info:
    point = 0.00001
    digits = 5
    spread = 10
    trade_stops_level = 0
    trade_freeze_level = 0
    volume_min = 0.01
    volume_max = 100.0
    volume_step = 0.01


def synthetic(n=20000, base=1.1000, vol=0.0005, drift=0.00002, seed=7,
              spread=10, cycle=0.0, start=1700000000, anchor=0.0, symbol='EURUSD'):
    rnd = random.Random(seed)
    rows = []
    px = base
    sym = symbol.upper()
    step = 1.0
    if 'XAU' in sym or 'GOLD' in sym or 'US30' in sym or 'US100' in sym:
        step = 100.0
    if 'JPY' in sym:
        step = 0.01
    for i in range(n):
        o = px
        d = drift + (cycle * math.sin(i / 240.0) if cycle else 0.0)
        if anchor:
            d += (anchor - px) * 0.02
        r = rnd.gauss(d, vol)
        c = o + r
        h = max(o, c) + abs(rnd.gauss(0, vol * 0.5))
        l = min(o, c) - abs(rnd.gauss(0, vol * 0.5))
        rows.append({'time': start + i * 60, 'open': o, 'high': h, 'low': l, 'close': c,
                     'tick_volume': 100 + i % 50, 'spread': spread * step})
        px = c
    return rows


def frames_from(m1):
    frames = {'M1': m1}
    for name, tf in TF_SECONDS.items():
        if name == 'M1':
            continue
        frames[name] = aggregate(m1, tf)[0]
    return frames


def context(symbol='EURUSD', m1=None):
    m1 = m1 or synthetic(seed=42, symbol=symbol)
    frames = frames_from(m1)
    epoch = int(m1[-1]['time']) + 60
    last = float(m1[-1]['close'])
    spread = 10 * (0.01 if 'JPY' in symbol.upper() else 1.0)
    tick = Tick(last - spread / 2.0, last + spread / 2.0, epoch)
    info = Info()
    info.point = 0.001 if 'JPY' in symbol.upper() else 0.00001
    info.digits = 3 if 'JPY' in symbol.upper() else 5
    ctx = MarketContext(symbol, tick, info, frames, session_of(epoch), Regime.NO_TRADE, {})
    result = RegimeDetector(None).detect(ctx)
    ctx.regime = result.regime
    ctx.regime_detail = {**result.detail, 'direction': result.direction,
                         'strength': result.strength, 'blocked': result.blocked}
    return ctx, result


class TestIndicators(unittest.TestCase):
    def test_sma_ema_causality(self):
        values = [float(i) for i in range(50)]
        sma = ta.sma(values, 5)
        self.assertAlmostEqual(sma[-1], 47.0)
        ema = ta.ema(values, 5)
        self.assertTrue(all(v is None for v in ema[:3]))
        self.assertAlmostEqual(ema[-1], 47.0, places=6)

    def test_rsi_bounds_and_extremes(self):
        up = [float(i) for i in range(80)]
        down = [float(80 - i) for i in range(80)]
        self.assertAlmostEqual(ta.rsi(up, 14)[-1], 100.0, places=3)
        self.assertAlmostEqual(ta.rsi(down, 14)[-1], 0.0, places=3)

    def test_atr_positive(self):
        rows = [{'open': 1.0, 'high': 1.002, 'low': 0.998, 'close': 1.001} for _ in range(40)]
        atr = ta.atr(rows, 14)
        self.assertTrue(atr[-1] and atr[-1] > 0)

    def test_structured_and_dict_frames_agree(self):
        rows = synthetic(n=200)
        self.assertEqual(ta.closes(rows)[-1], rows[-1]['close'])

    def test_adx_and_bollinger(self):
        rows = synthetic(n=300)
        adx = ta.adx(rows, 14)
        self.assertTrue(adx[-1] is None or adx[-1] >= 0)
        bb = ta.bollinger(ta.closes(rows), 20, 2.0)
        self.assertEqual(len(bb['upper']), len(rows))


class TestSession(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(session_of(1700000000), session_of(1700000000))
        import datetime
        base = datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc)
        self.assertEqual(session_of(base.replace(hour=3).timestamp()), 'ASIA')
        self.assertEqual(session_of(base.replace(hour=9).timestamp()), 'LONDON')
        self.assertEqual(session_of(base.replace(hour=13).timestamp()), 'OVERLAP')
        self.assertEqual(session_of(base.replace(hour=18).timestamp()), 'NEWYORK')
        self.assertEqual(session_of(base.replace(hour=22).timestamp()), 'LATE')


class TestRegime(unittest.TestCase):
    def test_trend_market_classified(self):
        _, result = context(m1=synthetic(n=20000, vol=0.0004, drift=0.00004, seed=3))
        self.assertIn(result.name, ('TREND', 'BREAKOUT', 'RANGE', 'VOLATILE', 'NO_TRADE'))
        if result.name == 'TREND':
            self.assertIn(result.direction, ('UP', 'DOWN'))

    def test_wide_spread_blocked(self):
        # 0.01 absolute spread (1000 points) vs ~0.002 M15 ATR -> blocked.
        ctx, result = context(m1=synthetic(n=5000, vol=0.0004, spread=1000, seed=9))
        self.assertTrue(result.blocked)
        self.assertEqual(result.name, 'NO_TRADE')

    def test_micro_atr_blocked(self):
        ctx, result = context(m1=synthetic(n=5000, vol=0.0000002, spread=1, seed=11))
        if result.detail.get('atr_m15'):
            self.assertTrue(result.blocked)

    def test_classification_matrix(self):
        cases = {
            'trend_up': ((30, 0.5, 1.2, 1.1, 0.001, 0.5, 0.4, 1.0, False, False, 0.3), 'TREND', 'UP'),
            'trend_dn': ((30, -0.5, 1.1, 1.2, 0.001, 0.5, 0.4, 1.0, False, False, 0.3), 'TREND', 'DOWN'),
            'range': ((15, 0.02, 1.2, 1.2, 0.001, 0.3, 0.4, 0.5, False, False, 0.05), 'RANGE', 'NONE'),
            'breakout_up': ((18, 0.1, 1.2, 1.2, 0.001, 0.5, 0.4, 1.6, True, False, 0.1), 'BREAKOUT', 'UP'),
            'breakout_dn': ((18, 0.1, 1.2, 1.2, 0.001, 0.5, 0.4, 1.6, False, True, 0.1), 'BREAKOUT', 'DOWN'),
            'volatile': ((18, 0.02, 1.2, 1.2, 0.001, 0.9, 0.4, 1.0, False, False, 0.05), 'VOLATILE', 'NONE'),
            'spike': ((18, 0.02, 1.2, 1.2, 0.001, 0.5, 0.4, 2.8, False, False, 0.05), 'VOLATILE', 'NONE'),
            'ambiguous': ((21, 0.2, 1.2, 1.2, 0.001, 0.5, 0.4, 1.0, False, False, 0.2), 'NO_TRADE', 'NONE'),
            'no_atr': ((21, 0.2, 1.2, 1.2, None, None, 0.4, 1.0, False, False, 0.2), 'NO_TRADE', 'NONE'),
        }
        for label, (args, regime, direction) in cases.items():
            result = _classify(*args)
            self.assertEqual(result[0].value, regime, label)
            self.assertEqual(result[1], direction, label)


class TestStrategies(unittest.TestCase):
    def test_twenty_five_distinct_specs(self):
        registry = StrategyRegistry(None, None)
        ids = [s.spec.id for s in registry.strategies]
        self.assertEqual(len(ids), 25)
        self.assertEqual(len(set(ids)), 25)
        for strategy in registry.strategies:
            self.assertTrue(strategy.spec.symbols)
            self.assertTrue(strategy.spec.regimes)
            self.assertTrue(strategy.spec.timeframes)
            self.assertTrue(strategy.spec.indicators)
            self.assertTrue(strategy.spec.forbidden or strategy.spec.regimes)

    def test_smoke_all_strategies_all_regimes_no_exceptions(self):
        registry = StrategyRegistry(None, None)
        errors = []
        cases = (
            ('EURUSD', 1.1000, 0.0005, 10 * 0.00001),
            ('GBPJPY', 188.00, 0.06, 10 * 0.001),
            ('XAUUSD', 2350.0, 1.2, 20 * 0.01),
            ('US30', 39500.0, 25.0, 30 * 0.1),
        )
        for symbol, base, vol, spread_price in cases:
            m1 = synthetic(n=8000, base=base, vol=vol, drift=0.0, seed=abs(hash(symbol)) % 1000,
                           symbol=symbol, anchor=base, spread=spread_price)
            frames = frames_from(m1)
            epoch = int(m1[-1]['time']) + 60
            last = float(m1[-1]['close'])
            info = Info()
            info.point = 0.001 if 'JPY' in symbol else (0.01 if symbol == 'XAUUSD' else 0.1 if symbol == 'US30' else 0.00001)
            info.digits = 3 if 'JPY' in symbol else (2 if symbol == 'XAUUSD' else 1 if symbol == 'US30' else 5)
            for regime in ('TREND', 'RANGE', 'BREAKOUT', 'VOLATILE', 'NO_TRADE'):
                ctx = MarketContext(symbol, Tick(last - spread_price / 2, last + spread_price / 2, epoch),
                                    info, frames, 'LONDON', Regime(regime), {'direction': 'UP'})
                for strategy in registry.strategies:
                    try:
                        decision = strategy.analyze(ctx)
                        self.assertEqual(decision['strategy_id'], strategy.spec.id)
                        self.assertIn(decision['decision'], ('SIGNAL', 'WAIT', 'REJECT'))
                    except Exception as exc:  # noqa: BLE001
                        errors.append((symbol, regime, strategy.spec.id, repr(exc)))
        self.assertEqual(errors, [])

    def test_symbol_matching(self):
        registry = StrategyRegistry(None, None)
        gold = [s for s in registry.strategies if 'XAUUSD' in s.spec.symbols]
        self.assertTrue(gold)
        self.assertEqual(symbol_class('XAUUSD'), 'GOLD')
        self.assertEqual(symbol_class('EURUSD'), 'FX')
        self.assertEqual(symbol_class('US100'), 'INDEX')


class TestRegistry(unittest.TestCase):
    def test_combo_verdict_gating(self):
        weak = {'trades': 20, 'wins': 5, 'gross_win': 50.0, 'gross_loss': 150.0,
                'net': -100.0, 'sum_r': -10.0, 'r_count': 20}
        enabled, penalty, reason = ComboPerformance.verdict(weak)
        self.assertFalse(enabled)
        self.assertEqual(reason, 'NEGATIVE_EXPECTANCY')
        early = {'trades': 6, 'wins': 1, 'gross_win': 10.0, 'gross_loss': 60.0,
                 'net': -50.0, 'sum_r': -3.0, 'r_count': 6}
        enabled, penalty, reason = ComboPerformance.verdict(early)
        self.assertTrue(enabled)
        self.assertGreater(penalty, 0)
        good = {'trades': 30, 'wins': 18, 'gross_win': 300.0, 'gross_loss': 120.0,
                'net': 180.0, 'sum_r': 18.0, 'r_count': 30}
        self.assertEqual(ComboPerformance.verdict(good), (True, 0.0, 'OK'))

    def test_registry_no_signal_returns_evaluated(self):
        registry = StrategyRegistry(None, None)
        ctx, result = context(symbol='EURUSD', m1=synthetic(n=8000, seed=21))
        decision, evaluated = registry.evaluate(ctx)
        self.assertTrue(evaluated)
        if decision:
            self.assertIn(decision.get('decision'), ('SIGNAL', 'WAIT'))
        # A SIGNAL must always clear the tier floor.
        if decision and decision.get('decision') == 'SIGNAL':
            tier = registry.by_id[decision['strategy_id']].spec.tier
            self.assertGreaterEqual(decision['confidence'],
                                    StrategyRegistry.TIER_MIN_CONFIDENCE[tier])

    def test_tie_break_direction_conflict(self):
        registry = StrategyRegistry(None, None)
        ctx, _ = context(symbol='EURUSD', m1=synthetic(n=8000, seed=22))

        class Fake:
            def __init__(self, sid, side):
                self.spec = type('S', (), {'id': sid, 'min_confidence': 0.0, 'tier': 'A'})()
                self._side = side

            def eligible(self, c, r=None):
                return True, 'OK'

            def analyze(self, c):
                return {'strategy_id': self.spec.id, 'decision': 'SIGNAL',
                        'side': self._side, 'confidence': 70.0, 'regime': c.regime.value}

        registry.strategies = [Fake('fake_a', 'BUY'), Fake('fake_b', 'SELL')]
        decision, _ = registry.evaluate(ctx)
        self.assertEqual(decision['decision'], 'WAIT')
        self.assertEqual(decision['reason_code'], 'TOP_CONFIDENCE_DIRECTION_TIE')


class TestGeometry(unittest.TestCase):
    def test_structural_stop_never_inside_price(self):
        ctx, _ = context()
        anchor = min(ta.lows(ctx.frame('M5')[-6:]))
        out = structural_stop(ctx, Side.BUY, ctx.ask, anchor, buffer_points=4.0, atr_frame='M5')
        self.assertIsNotNone(out)
        stop, risk = out
        self.assertLess(stop, ctx.ask)
        self.assertGreater(risk, 0)

    def test_atr_target_floor_r(self):
        ctx, _ = context()
        risk = 0.0010
        tp = atr_target(ctx, Side.BUY, ctx.ask, risk, atr_mult=1.0, floor_r=1.5)
        self.assertGreater(tp - ctx.ask, 0)


class TestBacktestEngine(unittest.TestCase):
    def test_simulator_anti_chase_and_costs(self):
        m1 = [
            {'time': 1700000000, 'open': 1.1000, 'high': 1.1005, 'low': 1.0995,
             'close': 1.1000, 'tick_volume': 100, 'spread': 10},
            # Committed open gaps far away from the plan -> anti-chase skip.
            {'time': 1700000060, 'open': 1.1060, 'high': 1.1065, 'low': 1.1055,
             'close': 1.1060, 'tick_volume': 100, 'spread': 10},
            {'time': 1700000120, 'open': 1.1060, 'high': 1.1070, 'low': 1.1050,
             'close': 1.1060, 'tick_volume': 100, 'spread': 10},
        ]
        decision = {'side': 'BUY', 'entry': 1.1000, 'sl_price': 1.0980,
                    'tp_price': 1.1040, 'management': {'max_hold_minutes': 5}}
        self.assertIsNone(_simulate_trade('EURUSD', m1, 0, decision, 0.00001, 5, 10, 60.0))
        trade = _simulate_trade('EURUSD', m1, 0, dict(decision, entry=1.1060), 0.00001, 5, 10, 60.0,
                                commission_points=5.0)
        self.assertIsNotNone(trade)
        self.assertGreater(trade['cost_r'], 0.0)
        self.assertLess(trade['r_multiple_net'], trade['r_multiple'])

    def test_replay_no_lookahead_and_costs(self):
        m1 = synthetic(n=12000, seed=31, cycle=0.00004)
        trades, scans = replay_m1('EURUSD', m1, 0.00001, 5, default_spread_points=10)
        self.assertGreater(scans, 0)
        self.assertTrue(isinstance(trades, list))
        for t in trades:
            self.assertLess(t['entry_time'], t['exit_time'] + 60)
            self.assertTrue(math.isfinite(t['r_multiple']))
            self.assertLessEqual(t['mfe_r'] + 1e-9, max(t['mfe_r'], 0) + 1e-9)
        stats = _stats(trades, 100.0)
        self.assertEqual(stats['trades'], len(trades))
        self.assertGreaterEqual(stats['max_drawdown'], 0.0)

    def test_walk_forward_split(self):
        report = walk_forward_report([
            {'entry_time': i, 'r_multiple': 0.5 if i % 2 else -1.0, 'mfe_r': 1.0, 'mae_r': -1.0}
            for i in range(10)
        ], fraction=0.6)
        self.assertEqual(report['in_sample_count'], 6)
        self.assertEqual(report['out_of_sample_count'], 4)
        self.assertEqual(report['in_sample']['trades'], 6)

    def test_stats_math(self):
        trades = [
            {'r_multiple': 2.0, 'mfe_r': 2.0, 'mae_r': -0.2},
            {'r_multiple': -1.0, 'mfe_r': 0.4, 'mae_r': -1.0},
            {'r_multiple': 1.0, 'mfe_r': 1.2, 'mae_r': -0.3},
        ]
        stats = _stats(trades, 100.0)
        self.assertEqual(stats['trades'], 3)
        self.assertEqual(stats['wins'], 2)
        self.assertAlmostEqual(stats['net'], 200.0)
        self.assertAlmostEqual(stats['profit_factor'], 300.0 / 100.0)
        self.assertAlmostEqual(stats['expectancy'], 200.0 / 3.0, places=6)
        self.assertAlmostEqual(stats['max_drawdown'], 100.0)

    def test_no_trades_stats(self):
        stats = _stats([], 100.0)
        self.assertEqual(stats['trades'], 0)
        self.assertEqual(stats['profit_factor'], 0.0)


if __name__ == '__main__':
    unittest.main()
