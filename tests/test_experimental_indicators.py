"""Smoke tests for the five experimental indicator strategies on branch 5test."""
import unittest
import numpy as np
from core.strategies import (
    macd_momentum, alligator_trend, moving_average_trend,
    rsi_reversal, bollinger_reversion,
)

class ExperimentalIndicatorStrategyTests(unittest.TestCase):
    def _ctx(self, closes):
        c=np.asarray(closes,dtype=float)
        rates={
            'open':c.copy(),
            'high':c+0.0004,
            'low':c-0.0004,
            'close':c,
        }
        return {
            'rates':rates,'point':0.00001,'live':float(c[-1]),
            'tick_momentum_fast':2.0,'tick_momentum':4.0,'tick_range':20.0,
            'atrp':25.0,'range_ok':True,'m15_bias':0,'h1_bias':0,
            'htf_allows':lambda side: True,
            'strategy_allowed':lambda name,side=None: True,
        }

    def test_strategies_are_callable_and_safe_on_flat_data(self):
        ctx=self._ctx(np.ones(80)*1.1000)
        for fn in (macd_momentum,alligator_trend,moving_average_trend,rsi_reversal,bollinger_reversion):
            result=fn(ctx)
            self.assertTrue(result is None or hasattr(result,'strategy'))

    def test_short_history_returns_none(self):
        ctx=self._ctx(np.linspace(1.0,1.01,10))
        for fn in (macd_momentum,alligator_trend,moving_average_trend,rsi_reversal,bollinger_reversion):
            self.assertIsNone(fn(ctx))

if __name__=='__main__':
    unittest.main()
