import sys
import types
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

fake_mt5 = types.ModuleType("MetaTrader5")
fake_mt5.ACCOUNT_TRADE_MODE_DEMO = 0
fake_mt5.POSITION_TYPE_BUY = 0
fake_mt5.POSITION_TYPE_SELL = 1
fake_mt5.shutdown = lambda: None
sys.modules["MetaTrader5"] = fake_mt5

from api.control_api import ControlAPI, SettingsPatch


class FakeEngine:
    def __init__(self):
        self.running=False
        self.scan_count=12
        self.last_cycle_seconds=0.2
        self.last_cycle_at=1234.0
        self.trades={}
        self.max_positions=3
        self.symbols=['EURUSD']
        self.symbol='EURUSD'
        self.risk_pct=0.25
        self.rr=3.0
        self.min_confidence=75.0
        self.protection_pct=45.0
        self.max_trade_minutes=10.0
        self.max_consecutive_losses=3
        self.daily_loss_limit_pct=2.0
        self.ai_rr_override=0.0
        self.ai_sl_points_override=0.0
        self.ai_tp_points_override=0.0
        self.ai_protection_override=0.0
        self.ai_trailing_override=0.0
        self.ai_duration_override=0.0
        self.trailing_gap_pct=5.0
        self.loss_limit_notified=False
        self.daily_loss_notified=False


class FakeGateway:
    def __init__(self):
        self._account=SimpleNamespace(
            login=123456,
            server='MetaQuotes-Demo',
            currency='USD',
            balance=10000,
            equity=10010,
            margin=50,
            margin_free=9960,
            profit=10,
            trade_mode=0,
        )

    def account(self):
        return self._account

    def positions(self):
        return ()

    def algo_status(self):
        return {
            'connected':True,
            'trade_allowed':True,
            'account_trade_allowed':True,
            'trade_expert':True,
        }

    def available_symbols(self):
        return (SimpleNamespace(name='EURUSD'),)


class FakeDB:
    async def log(self,*args,**kwargs):
        return None


class FakeHub:
    async def broadcast(self,*args,**kwargs):
        return None


class ControlAPITests(unittest.IsolatedAsyncioTestCase):
    def make_api(self):
        return ControlAPI(
            FakeEngine(),
            FakeDB(),
            FakeGateway(),
            FakeHub(),
            '0123456789abcdef0123456789abcdef',
        )

    def test_bearer_auth_is_exact(self):
        api=self.make_api()
        self.assertTrue(api._authorized('Bearer 0123456789abcdef0123456789abcdef'))
        self.assertFalse(api._authorized('Bearer wrong'))
        self.assertFalse(api._authorized(None))

    def test_settings_patch_requires_at_least_one_value(self):
        with self.assertRaises(ValidationError):
            SettingsPatch()

    def test_settings_patch_enforces_mtbot_ranges(self):
        with self.assertRaises(ValidationError):
            SettingsPatch(risk_pct=0.1)
        with self.assertRaises(ValidationError):
            SettingsPatch(min_confidence=99)
        good=SettingsPatch(
            risk_pct=1.0,
            rr=3.0,
            min_confidence=75,
            protection_pct=45,
            max_trade_minutes=10,
            max_positions=3,
            max_consecutive_losses=0,
            daily_loss_limit_pct=0,
        )
        self.assertEqual(good.max_positions,3)

    async def test_snapshot_matches_t4bot_contract(self):
        api=self.make_api()
        payload=await api.snapshot()
        self.assertTrue(payload['account']['is_demo'])
        self.assertEqual(payload['account']['login'],123456)
        self.assertEqual(payload['settings']['symbols'],['EURUSD'])
        self.assertEqual(payload['engine']['max_positions'],3)
        self.assertTrue(payload['readiness']['connected'])


if __name__=='__main__':
    unittest.main()
