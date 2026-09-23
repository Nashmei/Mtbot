"""Broker-free regression checks for market data and entry safety."""
import asyncio
import sys
import time
import types
import unittest
from unittest.mock import patch

import numpy as np

fake_mt5 = types.ModuleType("MetaTrader5")
for name, value in {
    "ACCOUNT_TRADE_MODE_DEMO": 0,
    "SYMBOL_TRADE_EXECUTION_REQUEST": 0,
    "SYMBOL_TRADE_EXECUTION_INSTANT": 1,
    "SYMBOL_TRADE_EXECUTION_MARKET": 2,
    "ORDER_FILLING_FOK": 0,
    "ORDER_FILLING_IOC": 1,
    "ORDER_FILLING_RETURN": 2,
    "COPY_TICKS_ALL": -1,
    "TRADE_RETCODE_DONE": 10009,
    "TRADE_RETCODE_DONE_PARTIAL": 10010,
}.items():
    setattr(fake_mt5, name, value)
sys.modules["MetaTrader5"] = fake_mt5

fake_config = types.ModuleType("core.config")
fake_config.settings = types.SimpleNamespace(
    default_symbol="EURUSD", rr=3.0, risk_per_trade_pct=0.25,
    max_consecutive_losses=3, spread_sample_size=60,
    max_spread_multiplier=1.8, max_spread_points=0,
    max_tick_age_seconds=15, poll_interval_ms=150,
    daily_loss_limit_pct=2.0,
)
sys.modules["core.config"] = fake_config

from core.analyzer import Analyzer
from core.engine import Engine
from core.mt5_gateway import MT5Gateway
from core.risk import Risk


class FakeDB:
    def __init__(self):
        self.events = []
        self.settings = {}

    async def log(self, event, *args, **kwargs):
        self.events.append((event, kwargs))

    async def get(self, key, default=None):
        return self.settings.get(key, default)

    async def set(self, key, value):
        self.settings[key] = value


class MarketSafetyTests(unittest.TestCase):
    def test_ticks_return_latest_samples(self):
        now = int(time.time())
        dtype = [("time", "i8"), ("bid", "f8"), ("ask", "f8")]
        samples = np.array([(now - 99 + i, 1.0, 1.1) for i in range(100)], dtype=dtype)
        with patch.object(fake_mt5, "copy_ticks_range", return_value=samples, create=True) as get:
            result = MT5Gateway().ticks("EURUSD", 30)
        self.assertEqual(len(result), 30)
        self.assertEqual(result["time"][-1], now)
        self.assertEqual(result["time"][0], now - 29)
        self.assertEqual(get.call_count, 1)

    def test_fill_policy_rejects_unsupported_market_mode(self):
        gateway = MT5Gateway()
        info = types.SimpleNamespace(trade_exemode=2, filling_mode=0)
        self.assertIsNone(gateway.filling_for(info))
        info.filling_mode = 2
        self.assertEqual(gateway.filling_for(info), fake_mt5.ORDER_FILLING_IOC)

    def test_rejected_spreads_do_not_raise_baseline_without_limit(self):
        risk = Risk()
        info = types.SimpleNamespace(name="EURUSD", point=0.0001)
        def tick(points):
            return types.SimpleNamespace(bid=1.0, ask=1.0 + points * info.point)
        risk.spread_ok(tick(2), info)
        for _ in range(12):
            risk.spread_ok(tick(100), info)
        self.assertEqual(risk.spread_relax_level["EURUSD"], 2)
        self.assertAlmostEqual(risk.spreads["EURUSD"][0], 2, places=5)
        self.assertEqual(len(risk.spreads["EURUSD"]), 1)

    def test_gold_signal_regression(self):
        ticks = np.array(
            [(2000 + i * .02 - .05, 2000 + i * .02 + .05) for i in range(300)],
            dtype=[("bid", "f8"), ("ask", "f8")],
        )
        closes = np.linspace(1990, 2000, 200)
        rates = np.array(
            [(v, v + .2, v - .2, v) for v in closes],
            dtype=[("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")],
        )
        _, signal, meta = Analyzer().analyze(
            ticks, .01, rates, symbol="XAUUSD",
            rates_m15=rates, rates_h1=rates, rates_m1=rates,
        )
        self.assertEqual(signal.strategy, "gold_scalp")
        self.assertEqual(signal.side.value, "BUY")
        self.assertEqual(meta["decision"], "gold_scalp")
        self.assertAlmostEqual(signal.confidence, .8759288243371257)

    def test_breakout_uses_latest_closed_m5_bar_for_non_gold(self):
        dtype = [("time", "i8"), ("open", "f8"), ("high", "f8"),
                 ("low", "f8"), ("close", "f8")]
        rates = np.zeros(200, dtype=dtype)
        rates["time"] = np.arange(200) * 300
        rates["open"] = rates["close"] = 99.9
        rates["high"] = 100
        rates["low"] = 99.8
        rates["high"][-1] = 101
        rates["close"][-1] = 100.5
        higher = rates.copy()
        higher["close"] = np.linspace(98, 100, 200)
        higher["open"] = higher["close"] - .1
        higher["high"] = higher["close"] + .2
        higher["low"] = higher["close"] - .2
        ticks = np.zeros(300, dtype=[("bid", "f8"), ("ask", "f8")])
        ticks["bid"] = np.linspace(99.96, 100.015, 300)
        ticks["ask"] = ticks["bid"] + .01
        _, signal, _ = Analyzer().analyze(
            ticks, .01, rates, symbol="EURUSD",
            rates_m15=higher, rates_h1=higher, rates_m1=higher,
        )
        self.assertEqual(signal.strategy, "scalp_breakout")


    def test_gold_wait_keeps_trend_regime(self):
        ticks = np.array(
            [(2000 + i * .001 - .05, 2000 + i * .001 + .05) for i in range(300)],
            dtype=[("bid", "f8"), ("ask", "f8")],
        )
        closes = np.linspace(2010, 2000, 200)
        rates = np.array(
            [(v, v + .2, v - .2, v) for v in closes],
            dtype=[("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")],
        )
        regime, signal, meta = Analyzer().analyze(
            ticks, .01, rates, symbol="XAUUSD",
            rates_m15=rates, rates_h1=rates, rates_m1=rates,
        )
        self.assertIsNone(signal)
        self.assertEqual(meta["market_mode"], "trend")
        self.assertEqual(regime.value, "TREND")

    def test_adaptive_thresholds_are_exposed(self):
        ticks = np.array(
            [(1.1000 + i * .000001, 1.1001 + i * .000001) for i in range(300)],
            dtype=[("bid", "f8"), ("ask", "f8")],
        )
        closes = np.linspace(1.101, 1.100, 200)
        rates = np.array(
            [(v, v + .0002, v - .0002, v) for v in closes],
            dtype=[("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")],
        )
        _, _, meta = Analyzer().analyze(
            ticks, .00001, rates, symbol="EURUSD",
            rates_m15=rates, rates_h1=rates, rates_m1=rates,
        )
        self.assertIn("momentum_min", meta)
        self.assertIn("acceleration_min", meta)
        self.assertGreater(meta["momentum_min"], 0)
        self.assertGreater(meta["acceleration_min"], 0)
        self.assertAlmostEqual(meta["momentum_min"], 1.2)
        self.assertAlmostEqual(meta["acceleration_min"], 0.6)

    def test_gold_uses_dedicated_opportunity_floors(self):
        ticks = np.array(
            [(2000 + i * .001 - .05, 2000 + i * .001 + .05) for i in range(300)],
            dtype=[("bid", "f8"), ("ask", "f8")],
        )
        closes = np.linspace(2010, 2000, 200)
        rates = np.array(
            [(v, v + .2, v - .2, v) for v in closes],
            dtype=[("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")],
        )
        _, _, meta = Analyzer().analyze(
            ticks, .01, rates, symbol="XAUUSD",
            rates_m15=rates, rates_h1=rates, rates_m1=rates,
        )
        self.assertAlmostEqual(meta["momentum_min"], 8.0)
        self.assertAlmostEqual(meta["acceleration_min"], 5.0)


    def test_unknown_broker_position_stops_engine(self):
        class Gateway:
            def account(self):
                return types.SimpleNamespace(trade_mode=0)
            def positions(self):
                return (types.SimpleNamespace(magic=4009, ticket=1234),)
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def check():
            engine = Engine(Gateway(), FakeDB(), notify)
            engine.running = True
            await engine.step()
            self.assertFalse(engine.running)
            self.assertEqual(engine.db.events[0][0], "UNMANAGED_POSITION")
            self.assertIn("غير متتبع", messages[0])
        asyncio.run(check())

    def test_daily_equity_limit_survives_restart(self):
        db = FakeDB()
        alerts = []
        async def notify(text, **kwargs):
            alerts.append(text)
        async def check():
            first = Engine(None, db, notify)
            self.assertTrue(await first._daily_entry_allowed(types.SimpleNamespace(equity=1000)))
            second = Engine(None, db, notify)
            self.assertFalse(await second._daily_entry_allowed(types.SimpleNamespace(equity=970)))
            self.assertFalse(await second._daily_entry_allowed(types.SimpleNamespace(equity=970)))
            self.assertEqual(len(alerts), 1)
            self.assertEqual(len(db.events), 1)
        asyncio.run(check())


    def test_start_rejects_existing_untracked_bot_position(self):
        class Gateway:
            def account(self):
                return types.SimpleNamespace(
                    trade_mode=0, equity=1000, currency="USD"
                )
            def algo_status(self):
                return {
                    "connected": True,
                    "trade_allowed": True,
                    "account_trade_allowed": True,
                    "trade_expert": True,
                }
            def positions(self):
                return (types.SimpleNamespace(magic=4009, ticket=77),)
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def check():
            engine = Engine(Gateway(), FakeDB(), notify)
            started = await engine.start()
            self.assertFalse(started)
            self.assertFalse(engine.running)
            self.assertTrue(any("غير متتبعة" in m for m in messages))
        asyncio.run(check())

    def test_stop_partial_close_keeps_trade_tracked(self):
        position = types.SimpleNamespace(ticket=42, symbol="EURUSD", volume=0.10)
        remaining = types.SimpleNamespace(ticket=42, symbol="EURUSD", volume=0.05)
        class Gateway:
            def __init__(self):
                self.lookups = 0
            def position_by_ticket(self, ticket):
                self.lookups += 1
                return position if self.lookups == 1 else remaining
            def close(self, pos):
                return types.SimpleNamespace(
                    retcode=fake_mt5.TRADE_RETCODE_DONE_PARTIAL,
                    comment="partial",
                )
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def check():
            engine = Engine(Gateway(), FakeDB(), notify)
            trade = types.SimpleNamespace(ticket=42, symbol="EURUSD")
            engine.trades[42] = trade
            engine.running = True
            await engine.stop()
            self.assertFalse(engine.running)
            self.assertIn(42, engine.trades)
            self.assertTrue(any(event == "STOP_EXIT_PARTIAL" for event, _ in engine.db.events))
            self.assertTrue(any("إغلاق جزئي" in m for m in messages))
        asyncio.run(check())

    def test_daily_equity_limit_blocks_exact_threshold(self):
        db = FakeDB()
        async def notify(message, **kwargs):
            pass
        async def check():
            engine = Engine(None, db, notify)
            self.assertTrue(await engine._daily_entry_allowed(types.SimpleNamespace(equity=1000)))
            self.assertFalse(await engine._daily_entry_allowed(types.SimpleNamespace(equity=980)))
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
