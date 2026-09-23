"""Tests for rolling strategy-performance ranking."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest

from core.models import Signal, Side
from core.strategy_ranker import (
    choose_best,
    effective_performance_score,
    normalize_average_points,
)
from storage.db import DB


class StrategyRankerTests(unittest.TestCase):
    def test_performance_scale_is_neutral_at_zero(self):
        self.assertEqual(normalize_average_points(-1), 0.0)
        self.assertEqual(normalize_average_points(0), 50.0)
        self.assertEqual(normalize_average_points(3), 100.0)

    def test_small_samples_are_shrunk_toward_neutral(self):
        weak=effective_performance_score({'trades':3,'points':9,'avg_points':3})
        full=effective_performance_score({'trades':30,'points':90,'avg_points':3})
        self.assertGreater(weak['score'],50.0)
        self.assertLess(weak['score'],full['score'])
        self.assertEqual(full['score'],100.0)

    def test_recent_performance_can_change_the_winner(self):
        higher_conf=Signal(Side.BUY,'higher_conf',.82,10,'')
        stronger_recent=Signal(Side.BUY,'stronger_recent',.78,10,'')
        candidates=[
            {'signal':higher_conf,'regime':None,'decision':'a','legacy_priority':1},
            {'signal':stronger_recent,'regime':None,'decision':'b','legacy_priority':2},
        ]
        perf={
            'higher_conf':{'trades':30,'points':-30,'avg_points':-1},
            'stronger_recent':{'trades':30,'points':90,'avg_points':3},
        }
        winner,ranked=choose_best(candidates,perf)
        self.assertEqual(winner['strategy'],'stronger_recent')
        self.assertGreater(ranked[0]['final_score'],ranked[1]['final_score'])

    def test_below_confidence_candidate_cannot_block_eligible_signal(self):
        below=Signal(Side.BUY,'below',.74,10,'')
        eligible=Signal(Side.BUY,'eligible',.76,10,'')
        candidates=[
            {'signal':below,'regime':None,'decision':'a','legacy_priority':1},
            {'signal':eligible,'regime':None,'decision':'b','legacy_priority':2},
        ]
        perf={
            'below':{'trades':30,'points':90,'avg_points':3},
            'eligible':{'trades':30,'points':0,'avg_points':0},
        }
        winner,ranked=choose_best(candidates,perf,min_confidence=75)
        self.assertEqual(winner['strategy'],'eligible')
        self.assertFalse(next(r for r in ranked if r['strategy']=='below')['eligible'])

    def test_new_strategy_is_neutral_not_penalized(self):
        sig=Signal(Side.SELL,'new_strategy',.76,10,'')
        winner,ranked=choose_best(
            [{'signal':sig,'regime':None,'decision':'x','legacy_priority':7}],
            {},
        )
        self.assertEqual(winner['strategy'],'new_strategy')
        self.assertEqual(ranked[0]['performance_score'],50.0)
        self.assertEqual(ranked[0]['performance_trades'],0)


class StrategyPerformanceDBTests(unittest.TestCase):
    def test_latest_50_closed_trades_per_strategy_are_used(self):
        fd,path=tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            db=DB(path)
            asyncio.run(db.init())
            con=sqlite3.connect(path)
            ts=1000.0
            # Old 10 winners should fall outside the latest-50 window.
            for i in range(60):
                open_details=json.dumps({'strategy':'scalp_trend','side':'BUY'})
                con.execute(
                    'INSERT INTO audit(ts,event,symbol,details) VALUES(?,?,?,?)',
                    (ts,'OPEN','EURUSD',open_details),
                )
                points_win=i<10
                close_event='TP' if points_win else 'SL'
                close_details=json.dumps({'pnl':100.0 if points_win else -100.0})
                con.execute(
                    'INSERT INTO audit(ts,event,symbol,details) VALUES(?,?,?,?)',
                    (ts+0.5,close_event,'EURUSD',close_details),
                )
                ts+=1.0
            con.commit()
            con.close()

            stats=asyncio.run(db.strategy_performance(50))
            row=stats['scalp_trend']
            self.assertEqual(row['trades'],50)
            self.assertEqual(row['points'],-50.0)
            self.assertEqual(row['avg_points'],-1.0)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


if __name__ == '__main__':
    unittest.main()
