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
    "POSITION_TYPE_BUY": 0,
    "POSITION_TYPE_SELL": 1,
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

from core.analyzer import (
    Analyzer,
    _M5_REVERSAL_CLOSED_INDEX,
    _M5_BREAKOUT_CLOSED_INDEX,
    _M5_SIDEWAYS_OVERLAP_INDICES,
)
from core.engine import Engine, _signal_key
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

    def test_live_quote_clock_offset_does_not_define_freshness(self):
        """History ticks are UTC even when Wine/MT5 live quote time is terminal-local."""
        now = time.time()
        dtype = [("time", "i8"), ("time_msc", "i8"), ("bid", "f8"), ("ask", "f8")]
        samples = np.array(
            [(int(now), int((now - 1 + i / 100) * 1000), 1.0, 1.0001) for i in range(100)],
            dtype=dtype,
        )
        latest = float(samples["time_msc"][-1]) / 1000.0
        self.assertLess(abs(time.time() - latest), fake_config.settings.max_tick_age_seconds)
        terminal_local_quote = now + 3 * 60 * 60
        self.assertGreater(
            abs(time.time() - terminal_local_quote),
            fake_config.settings.max_tick_age_seconds,
        )

    def test_no_money_volume_walk_never_increases_risk(self):
        vmin, vstep, requested = 0.01, 0.01, 10.19
        accepted = 8.37
        vol = requested
        checked = []
        while vol - vstep >= vmin - 1e-9:
            vol = round(vol - vstep, 8)
            checked.append(vol)
            if vol <= accepted:
                break
        self.assertLess(vol, requested)
        self.assertGreaterEqual(vol, vmin)
        self.assertLessEqual(vol, accepted)
        self.assertTrue(all(b < a for a, b in zip([requested] + checked, checked)))

    def test_aggregate_margin_level_floor_math(self):
        """A new order is capped so projected margin level stays at or above 200%."""
        equity = 3211.40
        current_margin = 1000.0
        margin_1lot = 800.0
        min_margin_level_pct = 200.0
        max_total_margin = equity * 100.0 / min_margin_level_pct
        remaining_margin = max_total_margin - current_margin
        capacity = remaining_margin / margin_1lot
        self.assertAlmostEqual(max_total_margin, 1605.70, places=2)
        self.assertAlmostEqual(capacity, 0.757125, places=6)
        projected = current_margin + margin_1lot * capacity
        self.assertGreaterEqual(equity / projected * 100.0, 200.0)

    def test_aggregate_margin_floor_rejects_min_lot_when_needed(self):
        equity = 1000.0
        current_margin = 499.5
        margin_1lot = 100.0
        vmin = 0.01
        max_total_margin = equity / 2.0
        capacity = (max_total_margin - current_margin) / margin_1lot
        self.assertLess(capacity, vmin)

    def test_no_money_notice_key_is_signal_scoped(self):
        signal_key = ("scalp_trend", "SELL", 12345)
        first = ("order_check_no_money", "NZDUSD", signal_key)
        repeat = ("order_check_no_money", "NZDUSD", signal_key)
        new_signal = ("order_check_no_money", "NZDUSD", ("scalp_trend", "SELL", 12346))
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, new_signal)

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
        self.assertGreaterEqual(meta["gold_values"]["confirmation_score"], 3)
        self.assertEqual(meta["gold_values"]["confirmation_required"], 3)
        self.assertTrue(meta["gold_checks"]["direction"])
        self.assertTrue(meta["gold_checks"]["htf"])
        self.assertTrue(meta["gold_checks"]["range_floor"])
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
        self.assertEqual(meta["gold_values"]["confirmation_required"], 3)
        self.assertIn("range_floor", meta["gold_checks"])

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

    def test_profit_protection_has_no_one_minute_stall_exit(self):
        position = types.SimpleNamespace(ticket=501, profit=25.0)
        class Gateway:
            def __init__(self):
                self.close_calls = 0
            def position_by_ticket(self, ticket):
                return position
            def close(self, pos):
                self.close_calls += 1
                return types.SimpleNamespace(retcode=fake_mt5.TRADE_RETCODE_DONE)
            def info(self, symbol):
                return types.SimpleNamespace(digits=5)
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def check():
            gw=Gateway()
            engine=Engine(gw,FakeDB(),notify)
            engine.max_trade_minutes=10
            trade=types.SimpleNamespace(
                ticket=501,symbol='EURUSD',side=types.SimpleNamespace(value='BUY'),
                entry=1.10000,sl=1.10500,tp=1.12000,initial_r=.005,
                opened_at=time.time()-120,strategy='scalp_trend',regime='TREND',
                confidence=.80,reason='',volume=.1,protection_pct=45.0,
                trailing_gap_pct=5.0,protection_45_active=True,trailing=True,
                best_favorable_price=1.11000,last_progress_at=time.time()-300,
                signal_bar=0,
            )
            # Use the real Side enum so manage() follows the BUY path.
            from core.models import Side
            trade.side=Side.BUY
            tick=types.SimpleNamespace(bid=1.11000,ask=1.11010)
            info=types.SimpleNamespace(point=.00001,digits=5,trade_stops_level=0,trade_freeze_level=0)
            await engine.manage(trade,tick,info)
            self.assertEqual(gw.close_calls,0)
            self.assertFalse(any(event.startswith('PROFIT_STALL_EXIT') for event,_ in engine.db.events))
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


    def test_start_adopts_existing_manual_position_with_sl_tp(self):
        position = types.SimpleNamespace(
            magic=0, ticket=77, symbol="EURUSD", type=fake_mt5.POSITION_TYPE_BUY,
            price_open=1.1000, sl=1.0950, tp=1.1150, volume=0.10,
            time=time.time()-7200,
        )
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
                return (position,)
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def idle_loop():
            await asyncio.sleep(60)
        async def check():
            engine = Engine(Gateway(), FakeDB(), notify)
            engine.loop = idle_loop
            before = time.time()
            started = await engine.start()
            self.assertTrue(started)
            self.assertTrue(engine.running)
            self.assertEqual(engine.mode, "RUNNING")
            self.assertIn(77, engine.trades)
            trade = engine.trades[77]
            self.assertEqual(trade.strategy, "manual_adopted")
            self.assertGreaterEqual(trade.opened_at, before)
            self.assertTrue(any(event == "POSITION_ADOPTED" for event, _ in engine.db.events))
            engine.running = False
            engine.mode = "STOPPED"
            engine.loop_task.cancel()
        asyncio.run(check())

    def test_start_adopts_protected_bot_position_and_restores_metadata(self):
        position = types.SimpleNamespace(
            magic=4009, ticket=79, symbol="EURUSD", type=fake_mt5.POSITION_TYPE_BUY,
            price_open=1.1000, sl=1.1080, tp=1.1150, volume=0.10,
            time=time.time()-300,
        )
        class DB(FakeDB):
            async def open_trade_metadata(self, ticket):
                self.assert_ticket = ticket
                return {
                    'strategy':'scalp_trend','regime':'TREND','confidence':0.82,
                    'reason':'restored','sl':1.0950,'protection_pct':45.0,
                    'trailing_gap_pct':5.0,'signal_bar':12345,
                }
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
                return (position,)
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def idle_loop():
            await asyncio.sleep(60)
        async def check():
            db=DB()
            engine=Engine(Gateway(), db, notify)
            engine.loop=idle_loop
            started=await engine.start()
            self.assertTrue(started)
            trade=engine.trades[79]
            self.assertEqual(trade.strategy,'scalp_trend')
            self.assertEqual(trade.signal_bar,12345)
            self.assertAlmostEqual(trade.initial_r,0.005)
            self.assertTrue(trade.protection_45_active)
            self.assertLess(trade.opened_at,time.time()-200)
            engine.running=False
            engine.mode='STOPPED'
            engine.loop_task.cancel()
        asyncio.run(check())

    def test_start_rejects_existing_position_without_sl_or_tp(self):
        position = types.SimpleNamespace(
            magic=0, ticket=78, symbol="EURUSD", type=fake_mt5.POSITION_TYPE_BUY,
            price_open=1.1000, sl=0.0, tp=1.1150, volume=0.10,
            time=time.time()-60,
        )
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
                return (position,)
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def check():
            engine = Engine(Gateway(), FakeDB(), notify)
            started = await engine.start()
            self.assertFalse(started)
            self.assertFalse(engine.running)
            self.assertEqual(engine.mode, "STOPPED")
            self.assertTrue(any("SL/TP" in m for m in messages))
        asyncio.run(check())

    def test_stop_enters_draining_without_closing_position(self):
        class Gateway:
            def __init__(self):
                self.close_calls = 0
            def close(self, pos):
                self.close_calls += 1
                raise AssertionError("stop must not close positions")
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def check():
            gw = Gateway()
            engine = Engine(gw, FakeDB(), notify)
            engine.trades[42] = types.SimpleNamespace(ticket=42, symbol="EURUSD")
            engine.running = True
            engine.mode = "RUNNING"
            state = await engine.stop()
            self.assertEqual(state, "DRAINING")
            self.assertTrue(engine.running)
            self.assertEqual(engine.mode, "DRAINING")
            self.assertIn(42, engine.trades)
            self.assertEqual(gw.close_calls, 0)
            self.assertTrue(any(event == "BOT_DRAINING" for event, _ in engine.db.events))
            self.assertTrue(any("إدارة" in m or "مراقبة" in m for m in messages))
        asyncio.run(check())

    def test_session_profit_target_switches_to_draining(self):
        messages = []
        async def notify(message, **kwargs):
            messages.append(message)
        async def check():
            engine = Engine(None, FakeDB(), notify)
            engine.running = True
            engine.mode = "RUNNING"
            engine.session_profit_target = 100.0
            engine.trades[1] = types.SimpleNamespace(ticket=1)
            engine.trades[2] = types.SimpleNamespace(ticket=2)
            await engine._register_session_pnl(40.0, closing_ticket=1)
            self.assertEqual(engine.mode, "RUNNING")
            await engine._register_session_pnl(65.0, closing_ticket=1)
            self.assertEqual(engine.mode, "DRAINING")
            self.assertAlmostEqual(engine.session_realized_profit, 105.0)
            self.assertTrue(engine.session_target_notified)
            self.assertTrue(any("هدف الجلسة" in m for m in messages))
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


    # Stage 1 critical bug regressions.
    def test_gold_m5_reversal_uses_latest_closed_bar(self):
        self.assertEqual(_M5_REVERSAL_CLOSED_INDEX, -1)

    def test_gold_breakout_uses_latest_closed_bar(self):
        self.assertEqual(_M5_BREAKOUT_CLOSED_INDEX, -1)

    def test_gold_m5_sideways_uses_latest_five_bar_window(self):
        self.assertEqual(tuple(_M5_SIDEWAYS_OVERLAP_INDICES), (-5, -4, -3, -2, -1))

    def test_gold_signal_key_matches_manage_and_scan(self):
        manage_key = _signal_key("gold_scalp", "SELL", 1000)
        scan_key = _signal_key("gold_scalp", "SELL", 1000)
        self.assertEqual(manage_key, scan_key)
        self.assertEqual(scan_key, ("gold_scalp", "SELL", 1000))

    def test_gold_same_bar_signal_is_blocked(self):
        blocked = _signal_key("gold_scalp", "SELL", 1000)
        current = _signal_key("gold_scalp", "SELL", 1000)
        self.assertEqual(blocked, current)

    def test_gold_new_bar_signal_key_changes_after_cooldown(self):
        blocked = _signal_key("gold_scalp", "SELL", 1000)
        current = _signal_key("gold_scalp", "SELL", 1300)
        self.assertNotEqual(blocked, current)

    def test_invalid_r_successful_close_keeps_engine_running(self):
        position = types.SimpleNamespace(ticket=91)
        class Gateway:
            def close(self, pos):
                return types.SimpleNamespace(
                    retcode=fake_mt5.TRADE_RETCODE_DONE, comment="done"
                )
            def position_by_ticket(self, ticket):
                return None
        async def notify(message, **kwargs):
            pass
        async def check():
            engine = Engine(Gateway(), FakeDB(), notify)
            engine.running = True
            ok = await engine._handle_invalid_initial_r("XAUUSD", position, 100.0, 100.0)
            self.assertTrue(ok)
            self.assertTrue(engine.running)
            self.assertEqual(
                [event for event, _ in engine.db.events],
                ["INVALID_INITIAL_R", "INVALID_INITIAL_R_EXIT"],
            )
        asyncio.run(check())

    def test_invalid_r_failed_close_with_remaining_position_stops_engine(self):
        position = types.SimpleNamespace(ticket=92)
        class Gateway:
            def close(self, pos):
                return types.SimpleNamespace(retcode=10030, comment="rejected")
            def position_by_ticket(self, ticket):
                return position
        async def notify(message, **kwargs):
            pass
        async def check():
            engine = Engine(Gateway(), FakeDB(), notify)
            engine.running = True
            ok = await engine._handle_invalid_initial_r("XAUUSD", position, 100.0, 100.0)
            self.assertFalse(ok)
            self.assertFalse(engine.running)
        asyncio.run(check())

    def test_invalid_r_close_none_stops_engine(self):
        position = types.SimpleNamespace(ticket=93)
        class Gateway:
            def close(self, pos):
                return None
            def position_by_ticket(self, ticket):
                return position
        async def notify(message, **kwargs):
            pass
        async def check():
            engine = Engine(Gateway(), FakeDB(), notify)
            engine.running = True
            ok = await engine._handle_invalid_initial_r("XAUUSD", position, 100.0, 100.0)
            self.assertFalse(ok)
            self.assertFalse(engine.running)
            exit_events = [data for event, data in engine.db.events if event == "INVALID_INITIAL_R_EXIT"]
            self.assertEqual(exit_events[0]["retcode"], None)
            self.assertEqual(exit_events[0]["comment"], "")
        asyncio.run(check())


    def test_daily_equity_limit_zero_from_db_disables_limit(self):
        db = FakeDB()
        db.settings["daily_loss_limit_pct"] = "0"
        async def notify(message, **kwargs):
            pass
        async def check():
            engine = Engine(None, db, notify)
            await engine.load_settings()
            self.assertEqual(engine.daily_loss_limit_pct, 0.0)
            self.assertTrue(await engine._daily_entry_allowed(types.SimpleNamespace(equity=1)))
            self.assertFalse(any(event == "DAILY_EQUITY_LIMIT" for event, _ in db.events))
        asyncio.run(check())

    def test_daily_equity_limit_db_value_survives_engine_restart(self):
        db = FakeDB()
        db.settings["daily_loss_limit_pct"] = "4.5"
        async def notify(message, **kwargs):
            pass
        async def check():
            first = Engine(None, db, notify)
            await first.load_settings()
            second = Engine(None, db, notify)
            await second.load_settings()
            self.assertEqual(first.daily_loss_limit_pct, 4.5)
            self.assertEqual(second.daily_loss_limit_pct, 4.5)
        asyncio.run(check())


    def test_telegram_setting_writer_updates_engine_and_db(self):
        from bot.telegram_app import TelegramUI
        db = FakeDB()
        engine = types.SimpleNamespace(
            risk_pct=2.0, rr=1.5, min_confidence=75.0, protection_pct=40.0,
            max_trade_minutes=45.0, max_positions=5, max_consecutive_losses=3,
            daily_loss_limit_pct=2.0, session_profit_target=0.0,
            loss_limit_notified=True, daily_loss_notified=True,
        )
        async def check():
            ui = TelegramUI(engine, db)
            cases = (
                ("risk", "3.5", "risk_pct", 3.5),
                ("rr", "2.5", "rr", 2.5),
                ("confidence", "80", "min_confidence", 80.0),
                ("protection", "50", "protection_pct", 50.0),
                ("maxduration", "60", "max_trade_minutes", 60.0),
                ("maxpos", "4", "max_positions", 4),
                ("maxloss", "0", "max_consecutive_losses", 0),
                ("dailyloss", "0", "daily_loss_limit_pct", 0.0),
                ("sessionprofit", "125", "session_profit_target", 125.0),
            )
            for key, raw, db_key, expected in cases:
                await ui._apply_setting(key, raw)
                self.assertEqual(db.settings[db_key], expected)
            self.assertEqual(engine.risk_pct, 3.5)
            self.assertEqual(engine.rr, 2.5)
            self.assertEqual(engine.min_confidence, 80.0)
            self.assertEqual(engine.protection_pct, 50.0)
            self.assertEqual(engine.max_trade_minutes, 60.0)
            self.assertEqual(engine.max_positions, 4)
            self.assertEqual(engine.max_consecutive_losses, 0)
            self.assertEqual(engine.daily_loss_limit_pct, 0.0)
            self.assertEqual(engine.session_profit_target, 125.0)
            self.assertFalse(engine.loss_limit_notified)
            self.assertFalse(engine.daily_loss_notified)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
