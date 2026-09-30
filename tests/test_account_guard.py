"""Account-level correlation / concentration guard tests."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import account_guard as ag


class TestExposure(unittest.TestCase):
    def test_opposite_usd_hedge_detected(self):
        # Selling EURUSD is long USD; buying USDCHF is also long USD, so the
        # hedge case is BUY USDCHF against a BUY EURUSD position.
        self.assertTrue(ag.conflicting('EURUSD', 'BUY', 'USDCHF', 'BUY'))
        self.assertTrue(ag.conflicting('EURJPY', 'SELL', 'USDJPY', 'BUY'))
        self.assertFalse(ag.conflicting('EURUSD', 'BUY', 'USDCHF', 'SELL'))

    def test_gold_is_usd_denominated(self):
        # Buying gold is short USD; selling EURUSD is also short USD.  Buying
        # EURUSD (long USD) would be the synthetic hedge.
        self.assertTrue(ag.conflicting('XAUUSD', 'BUY', 'EURUSD', 'SELL'))
        self.assertFalse(ag.conflicting('XAUUSD', 'BUY', 'EURUSD', 'BUY'))

    def test_index_and_gold_correlate(self):
        self.assertTrue(ag.correlated('XAUUSD', 'US100'))
        self.assertFalse(ag.correlated('XAUUSD', 'GBPJPY') is None)

    def test_unrelated_pairs_not_flagged(self):
        self.assertFalse(ag.conflicting('GBPUSD', 'BUY', 'EURUSD', 'BUY'))
        self.assertFalse(ag.conflicting('AUDUSD', 'SELL', 'EURGBP', 'BUY'))


class TestCheck(unittest.TestCase):
    def test_same_side_limit(self):
        positions = [{'symbol': 'EURUSD', 'side': 'BUY', 'risk_cash': 10.0},
                     {'symbol': 'GBPUSD', 'side': 'BUY', 'risk_cash': 10.0}]
        allowed, reason, _ = ag.check('AUDUSD', 'BUY', positions, 1, 0.5, 10000)
        self.assertFalse(allowed)
        self.assertEqual(reason, 'CORRELATED_LIMIT')
        allowed, reason, _ = ag.check('AUDUSD', 'BUY', positions, 3, 0.5, 10000)
        self.assertTrue(allowed)

    def test_opposite_side_rejected(self):
        allowed, reason, _ = ag.check('EURUSD', 'BUY',
                                      [{'symbol': 'USDCHF', 'side': 'BUY', 'risk_cash': 10}],
                                      3, 0.5, 10000)
        self.assertFalse(allowed)
        self.assertEqual(reason, 'CORRELATED_OPPOSITE_SIDE')

    def test_risk_budget(self):
        positions = [{'symbol': 'EURUSD', 'side': 'BUY', 'risk_cash': 90.0}]
        allowed, reason, detail = ag.check('GBPUSD', 'BUY', positions, 2, 0.5, 10000)
        self.assertFalse(allowed)
        self.assertEqual(reason, 'CORRELATED_RISK_BUDGET')
        self.assertIn('budget_cash', detail)

    def test_disabled_limit_allows(self):
        allowed, reason, _ = ag.check('EURUSD', 'SELL', [{'symbol': 'USDCHF', 'side': 'SELL'}],
                                      0, 0.5, 10000)
        self.assertTrue(allowed)
        self.assertEqual(reason, 'OK')


if __name__ == '__main__':
    unittest.main()
