"""Broker-free tests for opportunity-expansion strategies."""
import unittest

import numpy as np

from core.models import Side
from core.strategies import scalp_sweep_reversal, scalp_squeeze_expansion


def _strategy_allowed(name, side=None):
    return True


def _base_ctx(rates):
    return {
        'rates': rates,
        'have': True,
        'point': 0.1,
        'atrp': 8.0,
        'tick_range': 12.0,
        'tick_momentum_fast': 2.0,
        'tick_momentum': 4.0,
        'micro_trend': 2.0,
        'context_up': True,
        'context_dn': True,
        'h1_bias': 1,
        'strategy_allowed': _strategy_allowed,
    }


class OpportunityExpansionTests(unittest.TestCase):
    def test_sweep_reversal_buy_after_failed_breakout(self):
        dtype=[('open','f8'),('high','f8'),('low','f8'),('close','f8')]
        rates=np.zeros(60,dtype=dtype)
        rates['open']=rates['close']=100.0
        rates['high']=101.0
        rates['low']=99.0
        rates[-1]=(100.0,100.4,98.5,99.6)
        sig=scalp_sweep_reversal(_base_ctx(rates))
        self.assertIsNotNone(sig)
        self.assertEqual(sig.strategy,'scalp_sweep_reversal')
        self.assertEqual(sig.side,Side.BUY)

    def test_sweep_reversal_sell_is_symmetric(self):
        dtype=[('open','f8'),('high','f8'),('low','f8'),('close','f8')]
        rates=np.zeros(60,dtype=dtype)
        rates['open']=rates['close']=100.0
        rates['high']=101.0
        rates['low']=99.0
        rates[-1]=(100.0,101.5,99.6,100.4)
        ctx=_base_ctx(rates)
        ctx.update({
            'tick_momentum_fast':-2.0,
            'tick_momentum':-4.0,
            'micro_trend':-2.0,
            'h1_bias':-1,
        })
        sig=scalp_sweep_reversal(ctx)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.strategy,'scalp_sweep_reversal')
        self.assertEqual(sig.side,Side.SELL)

    def test_sweep_reversal_rejects_opposing_h1(self):
        dtype=[('open','f8'),('high','f8'),('low','f8'),('close','f8')]
        rates=np.zeros(60,dtype=dtype)
        rates['open']=rates['close']=100.0
        rates['high']=101.0
        rates['low']=99.0
        rates[-1]=(100.0,100.4,98.5,99.6)
        ctx=_base_ctx(rates)
        ctx['h1_bias']=-1
        self.assertIsNone(scalp_sweep_reversal(ctx))

    def test_squeeze_expansion_buy_after_compression(self):
        dtype=[('open','f8'),('high','f8'),('low','f8'),('close','f8')]
        rates=np.zeros(60,dtype=dtype)
        rates['open']=rates['close']=100.0
        rates['high'][:45]=100.5
        rates['low'][:45]=99.5
        rates['high'][45:59]=100.1
        rates['low'][45:59]=99.9
        rates[-1]=(100.0,100.4,99.9,100.3)
        sig=scalp_squeeze_expansion(_base_ctx(rates))
        self.assertIsNotNone(sig)
        self.assertEqual(sig.strategy,'scalp_squeeze_expansion')
        self.assertEqual(sig.side,Side.BUY)

    def test_squeeze_requires_prior_compression(self):
        dtype=[('open','f8'),('high','f8'),('low','f8'),('close','f8')]
        rates=np.zeros(60,dtype=dtype)
        rates['open']=rates['close']=100.0
        rates['high']=100.5
        rates['low']=99.5
        rates[-1]=(100.0,101.3,99.9,101.2)
        self.assertIsNone(scalp_squeeze_expansion(_base_ctx(rates)))


if __name__ == '__main__':
    unittest.main()
