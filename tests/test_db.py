"""DB pairing / aggregation tests using a tiny sqlite3-backed aiosqlite shim."""
import asyncio
import os
import sqlite3
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import aiosqlite  # noqa: F401
    HAVE_AIOSQLITE = True
except Exception:
    HAVE_AIOSQLITE = False


def _install_shim():
    if HAVE_AIOSQLITE:
        return
    mod = types.ModuleType('aiosqlite')

    class _Cur:
        def __init__(self, cur):
            self._cur = cur

        def __await__(self):
            async def run():
                return self
            return run().__await__()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self._cur.close()

        def __aiter__(self):
            return self

        async def __anext__(self):
            row = self._cur.fetchone()
            if row is None:
                raise StopAsyncIteration
            return row

    class _Conn:
        def __init__(self, path):
            self.c = sqlite3.connect(path, timeout=5)

        def execute(self, sql, args=()):
            return _Cur(self.c.execute(sql, args))

        async def executescript(self, script):
            self.c.executescript(script)

        async def commit(self):
            self.c.commit()

        async def close(self):
            self.c.close()

    async def connect(path, timeout=5.0):
        return _Conn(path)

    mod.connect = connect
    sys.modules['aiosqlite'] = mod


_install_shim()
from storage.db import DB  # noqa: E402


class TestDBMetrics(unittest.TestCase):
    def test_closed_trade_metrics_pairs_open_and_close(self):
        async def run():
            path = tempfile.mktemp(suffix='.db')
            db = DB(path)
            await db.init()
            await db.log('OPEN', 'EURUSD', ticket=1, strategy='trend_ema_pullback', regime='TREND',
                         side='BUY', entry=1.1, sl=1.09, tp=1.13, volume=0.1, account_login=42)
            await db.log('TP', 'EURUSD', ticket=1, pnl=20.0, r_multiple=2.0,
                         mfe_r=2.2, mae_r=-0.3, account_login=42)
            await db.log('OPEN', 'XAUUSD', ticket=2, strategy='gold_trend_pullback', regime='TREND',
                         side='SELL', entry=2300, sl=2310, tp=2280, volume=0.1, account_login=42)
            await db.log('SL', 'XAUUSD', ticket=2, pnl=-30.0, r_multiple=-1.0,
                         mfe_r=0.5, mae_r=-1.1, account_login=42)
            rows = await db.closed_trade_metrics(login=42, window=50)
            by = {r['strategy']: r for r in rows}
            self.assertEqual(len(rows), 2)
            self.assertEqual(by['trend_ema_pullback']['trades'], 1)
            self.assertAlmostEqual(by['trend_ema_pullback']['net'], 20.0)
            self.assertEqual(by['trend_ema_pullback']['profit_factor'], 999.0)
            self.assertEqual(by['gold_trend_pullback']['profit_factor'], 0.0)
            self.assertAlmostEqual(by['trend_ema_pullback']['avg_r'], 2.0)
            self.assertAlmostEqual(by['trend_ema_pullback']['avg_mfe_r'], 2.2)
            self.assertAlmostEqual(by['gold_trend_pullback']['avg_mae_r'], -1.1)
            # Strict account scoping: another account's closes must not pair.
            await db.log('SL', 'EURUSD', ticket=1, pnl=5.0, account_login=99)
            rows_other = await db.closed_trade_metrics(login=99, window=50)
            self.assertEqual(rows_other, [])

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
