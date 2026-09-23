"""Regression tests for per-account Telegram-managed settings."""
import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace

from core.engine import Engine
from storage.db import DB


class FakeGateway:
    def __init__(self, login):
        self.login = login

    def account(self):
        return SimpleNamespace(login=self.login)


async def _notify(*args, **kwargs):
    return None


class AccountSettingsTests(unittest.TestCase):
    def test_new_account_does_not_inherit_other_account_preferences(self):
        fd,path=tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            db=DB(path)
            asyncio.run(db.init())
            gw=FakeGateway(111)
            engine=Engine(gw,db,_notify)
            asyncio.run(engine.load_settings(login=111,migrate_legacy=False))
            engine.rr=2.0
            engine.risk_pct=1.25
            asyncio.run(engine.save_setting('rr',engine.rr))
            asyncio.run(engine.save_setting('risk_pct',engine.risk_pct))

            gw.login=222
            asyncio.run(engine.load_settings(login=222,migrate_legacy=False))
            self.assertEqual(engine.rr,3.0)
            self.assertEqual(engine.risk_pct,0.25)

            gw.login=111
            asyncio.run(engine.load_settings(login=111,migrate_legacy=False))
            self.assertEqual(engine.rr,2.0)
            self.assertEqual(engine.risk_pct,1.25)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def test_existing_account_can_migrate_legacy_preferences_once(self):
        fd,path=tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            db=DB(path)
            asyncio.run(db.init())
            asyncio.run(db.set('rr',1.5))
            asyncio.run(db.set('risk_pct',2.0))
            gw=FakeGateway(333)
            engine=Engine(gw,db,_notify)
            asyncio.run(engine.load_settings(login=333,migrate_legacy=True))
            self.assertEqual(engine.rr,1.5)
            self.assertEqual(engine.risk_pct,2.0)
            self.assertEqual(asyncio.run(db.get('account:333:rr')),'1.5')
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


if __name__ == '__main__':
    unittest.main()
