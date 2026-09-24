import asyncio
import hmac
import json
import math
import os
import time
from pathlib import Path

import MetaTrader5 as mt5
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from core.config import settings


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    risk_pct: float | None = Field(default=None, ge=0.25, le=50)
    rr: float | None = Field(default=None, ge=0.5, le=10)
    min_confidence: float | None = Field(default=None, ge=50, le=95)
    protection_pct: float | None = Field(default=None, ge=5, le=90)
    max_trade_minutes: float | None = Field(default=None, ge=3, le=240)
    max_positions: int | None = Field(default=None, ge=1, le=10)
    max_consecutive_losses: int | None = Field(default=None, ge=0, le=20)
    daily_loss_limit_pct: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode='after')
    def require_value(self):
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError('at least one setting is required')
        return self


class SymbolsPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbols: list[str] = Field(min_length=1, max_length=20)


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server: str = Field(min_length=1, max_length=160)
    login: int = Field(gt=0)
    password: SecretStr = Field(min_length=1, max_length=256)


class ControlAPI:
    """Authenticated API façade over the existing Mtbot engine.

    It deliberately delegates all trading decisions and validation to Engine,
    Analyzer, Risk and MT5Gateway rather than duplicating that logic.
    """

    def __init__(self, engine, db, gateway, event_hub, token):
        token = (token or '').strip()
        if len(token) < 24:
            raise ValueError('CONTROL_API_TOKEN must be at least 24 characters')

        self.engine = engine
        self.db = db
        self.gateway = gateway
        self.event_hub = event_hub
        self._token = token
        self._analysis_cache = {}

        self.app = FastAPI(
            title='Mtbot Control API',
            version='1.0.0',
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        self._install_routes()

    def _authorized(self, authorization):
        if not authorization or not authorization.startswith('Bearer '):
            return False
        supplied = authorization[7:].strip()
        return bool(supplied) and hmac.compare_digest(supplied, self._token)

    async def _authorize(self, authorization: str | None = Header(default=None)):
        if not self._authorized(authorization):
            raise HTTPException(status_code=401, detail='Unauthorized')

    def _install_routes(self):
        app = self.app
        auth = self._authorize

        @app.get('/v1/health')
        async def health(_=Depends(auth)):
            return {
                'ok': True,
                'service': 'mtbot-control-api',
                'api_version': 1,
                'demo_only': True,
            }

        @app.get('/v1/snapshot')
        async def snapshot(_=Depends(auth)):
            return await self.snapshot()

        @app.post('/v1/engine/start')
        async def start_engine(_=Depends(auth)):
            started = await self.engine.start()
            await self.event_hub.broadcast('engine_state_changed', {'running': bool(self.engine.running)})
            return {
                'ok': bool(started),
                'message': 'تم تشغيل المحرك.' if started else 'لم يبدأ المحرك؛ راجع حالة الجاهزية وسجل Mtbot.',
            }

        @app.post('/v1/engine/stop')
        async def stop_engine(_=Depends(auth)):
            await self.engine.stop()
            await self.event_hub.broadcast('engine_state_changed', {'running': False})
            return {'ok': True, 'message': 'تم إيقاف المحرك.'}

        @app.post('/v1/account/login')
        async def account_login(payload: LoginPayload, _=Depends(auth)):
            if self.engine.running:
                raise HTTPException(status_code=409, detail='أوقف المحرك قبل تغيير حساب MT5.')

            server = payload.server.strip()
            if not server:
                raise HTTPException(status_code=422, detail='Server مطلوب.')

            password = payload.password.get_secret_value()
            ok, err, account = await asyncio.to_thread(
                self.gateway.login,
                int(payload.login),
                password,
                server,
            )

            if not ok or not account:
                password = None
                await self.db.log(
                    'MT5_LOGIN_FAILED',
                    login=int(payload.login),
                    server=server,
                    error=str(err),
                    source='t4bot',
                )
                raise HTTPException(status_code=400, detail=f'فشل تسجيل الدخول إلى MT5: {err}')

            if getattr(account, 'trade_mode', None) != mt5.ACCOUNT_TRADE_MODE_DEMO:
                password = None
                mt5.shutdown()
                await self.db.log(
                    'MT5_LOGIN_REJECTED',
                    login=int(payload.login),
                    server=server,
                    reason='NON_DEMO',
                    source='t4bot',
                )
                raise HTTPException(status_code=403, detail='T4Bot يسمح بحسابات MT5 التجريبية فقط.')

            self._store_credentials(int(payload.login), server, password)
            password = None
            await self.engine.load_settings(login=int(payload.login), migrate_legacy=False)
            await self.db.log(
                'MT5_LOGIN_SUCCESS',
                login=int(payload.login),
                server=server,
                source='t4bot',
            )
            await self.event_hub.broadcast(
                'account_changed',
                {'login': int(payload.login), 'server': server},
            )
            return {
                'ok': True,
                'message': 'تم الاتصال بحساب MT5 التجريبي.',
                'account': self._account_payload(account),
            }

        @app.patch('/v1/settings')
        async def update_settings(payload: SettingsPatch, _=Depends(auth)):
            mapping = {
                'risk_pct': ('risk_pct', 'risk_pct'),
                'rr': ('rr', 'rr'),
                'min_confidence': ('min_confidence', 'min_confidence'),
                'protection_pct': ('protection_pct', 'protection_pct'),
                'max_trade_minutes': ('max_trade_minutes', 'max_trade_minutes'),
                'max_positions': ('max_positions', 'max_positions'),
                'max_consecutive_losses': ('max_consecutive_losses', 'max_consecutive_losses'),
                'daily_loss_limit_pct': ('daily_loss_limit_pct', 'daily_loss_limit_pct'),
            }

            changed = {}
            for field, value in payload.model_dump(exclude_none=True).items():
                attr, db_key = mapping[field]
                setattr(self.engine, attr, value)
                await self.engine.save_setting(db_key, value)
                changed[field] = value

            if 'max_consecutive_losses' in changed:
                self.engine.loss_limit_notified = False
            if 'daily_loss_limit_pct' in changed:
                self.engine.daily_loss_notified = False

            await self.db.log('SETTINGS_UPDATED', source='t4bot', changed=changed)
            await self.event_hub.broadcast('settings_changed', changed)
            return {'ok': True, 'message': 'تم حفظ الإعدادات على Mtbot.'}

        @app.get('/v1/symbols')
        async def get_symbols(_=Depends(auth)):
            available = [item.name for item in self.gateway.available_symbols()]
            return {'selected': list(self.engine.symbols), 'available': available}

        @app.put('/v1/symbols')
        async def put_symbols(payload: SymbolsPayload, _=Depends(auth)):
            available = [item.name for item in self.gateway.available_symbols()]
            selected = []
            for requested in payload.symbols:
                key = requested.strip().upper()
                if not key:
                    continue
                exact = [name for name in available if name.upper() == key]
                matches = exact or [name for name in available if key in name.upper()]
                if matches and matches[0] not in selected:
                    selected.append(matches[0])

            if not selected:
                raise HTTPException(status_code=422, detail='لم يتم العثور على أي رمز مطلوب في MT5.')

            self.engine.symbols = selected
            self.engine.symbol = selected[0]
            await self.engine.save_setting('symbols', json.dumps(selected))
            await self.db.log('SYMBOLS_UPDATED', source='t4bot', symbols=selected)
            await self.event_hub.broadcast('symbols_changed', {'symbols': selected})
            return {'selected': selected, 'available': available}

        @app.post('/v1/analysis/run')
        async def run_analysis(_=Depends(auth)):
            rows = await self._run_analysis()
            await self.event_hub.broadcast('analysis_updated', {'symbols': [row['symbol'] for row in rows]})
            return rows

        @app.get('/v1/audit')
        async def audit(limit: int = Query(default=100, ge=1, le=500), _=Depends(auth)):
            return await self.db.recent_audit(limit)

        @app.websocket('/v1/ws')
        async def websocket_endpoint(websocket: WebSocket):
            if not self._authorized(websocket.headers.get('authorization')):
                await websocket.close(code=4401)
                return

            await self.event_hub.connect(websocket)
            try:
                # Stream live server state directly to T4Bot. This removes the
                # old client-side polling delay while keeping MT5 as the source
                # of truth. EventHub messages still arrive immediately between
                # snapshots for command/state invalidation.
                while True:
                    await websocket.send_json({
                        'type': 'snapshot',
                        'ts': time.time(),
                        'payload': await self.snapshot(),
                    })
                    await asyncio.sleep(0.25)
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                await self.event_hub.disconnect(websocket)

    async def snapshot(self):
        account = self.gateway.account()
        positions = self.gateway.positions()
        if positions is None:
            positions = ()

        readiness = self.gateway.algo_status()
        return {
            'server_time': time.time(),
            'engine': {
                'running': bool(self.engine.running),
                'scan_count': int(self.engine.scan_count),
                'last_cycle_seconds': self._number(self.engine.last_cycle_seconds),
                'last_cycle_at': self._number(self.engine.last_cycle_at),
                'tracked_positions': len(self.engine.trades),
                'max_positions': int(self.engine.max_positions),
            },
            'account': self._account_payload(account) if account else None,
            'positions': [self._position_payload(position) for position in positions],
            'settings': {
                'symbols': list(self.engine.symbols),
                'risk_pct': self._number(self.engine.risk_pct),
                'rr': self._number(self.engine.rr),
                'min_confidence': self._number(self.engine.min_confidence),
                'protection_pct': self._number(self.engine.protection_pct),
                'max_trade_minutes': self._number(self.engine.max_trade_minutes),
                'max_positions': int(self.engine.max_positions),
                'max_consecutive_losses': int(self.engine.max_consecutive_losses),
                'daily_loss_limit_pct': self._number(self.engine.daily_loss_limit_pct),
            },
            'analysis': list(self._analysis_cache.values()),
            'readiness': {
                'connected': bool(readiness.get('connected')),
                'trade_allowed': bool(readiness.get('trade_allowed')),
                'account_trade_allowed': bool(readiness.get('account_trade_allowed')),
                'trade_expert': bool(readiness.get('trade_expert')),
            },
        }

    async def _run_analysis(self):
        if not self.gateway.account():
            raise HTTPException(status_code=409, detail='MT5 غير متصل.')

        rows = []
        for symbol in list(self.engine.symbols):
            updated_at = time.time()
            info = self.gateway.info(symbol)
            if not info:
                row = self._analysis_unavailable(symbol, 'symbol_info_unavailable', updated_at)
                rows.append(row)
                self._analysis_cache[symbol] = row
                continue

            ticks = self.gateway.ticks(symbol)
            if ticks is None or len(ticks) < 80:
                row = self._analysis_unavailable(symbol, 'insufficient_ticks', updated_at)
                rows.append(row)
                self._analysis_cache[symbol] = row
                continue

            tick_ts = (
                float(ticks['time_msc'][-1]) / 1000.0
                if 'time_msc' in ticks.dtype.names
                else float(ticks['time'][-1])
            )
            if abs(time.time() - tick_ts) > settings.max_tick_age_seconds:
                row = self._analysis_unavailable(symbol, 'stale_tick', updated_at)
                rows.append(row)
                self._analysis_cache[symbol] = row
                continue

            regime, signal, meta = self.engine.an.analyze(
                ticks,
                info.point,
                self.gateway.rates_m5(symbol, 200),
                symbol=symbol,
                rates_m15=self.gateway.rates_m15(symbol, 200),
                rates_h1=self.gateway.rates_h1(symbol, 200),
                rates_m1=self.gateway.rates_m1(symbol, 200),
                strategy_performance=self.engine.strategy_performance,
                min_confidence=self.engine.min_confidence,
            )

            if signal:
                row = {
                    'symbol': symbol,
                    'regime': regime.value,
                    'state': 'signal',
                    'side': signal.side.value,
                    'strategy': signal.strategy,
                    'confidence': self._number(signal.confidence),
                    'reason': signal.reason,
                    'updated_at': updated_at,
                }
            else:
                row = {
                    'symbol': symbol,
                    'regime': regime.value,
                    'state': 'no_signal',
                    'side': None,
                    'strategy': None,
                    'confidence': None,
                    'reason': str(meta.get('decision', 'waiting')),
                    'updated_at': updated_at,
                }

            rows.append(row)
            self._analysis_cache[symbol] = row

        return rows

    def _analysis_unavailable(self, symbol, reason, updated_at):
        return {
            'symbol': symbol,
            'regime': 'NO_TRADE',
            'state': 'unavailable',
            'side': None,
            'strategy': None,
            'confidence': None,
            'reason': reason,
            'updated_at': updated_at,
        }

    def _account_payload(self, account):
        return {
            'login': int(getattr(account, 'login', 0) or 0),
            'server': str(getattr(account, 'server', '') or ''),
            'currency': str(getattr(account, 'currency', '') or ''),
            'balance': self._number(getattr(account, 'balance', 0)),
            'equity': self._number(getattr(account, 'equity', 0)),
            'margin': self._number(getattr(account, 'margin', 0)),
            'margin_free': self._number(getattr(account, 'margin_free', 0)),
            'profit': self._number(getattr(account, 'profit', 0)),
            'is_demo': getattr(account, 'trade_mode', None) == mt5.ACCOUNT_TRADE_MODE_DEMO,
        }

    def _position_payload(self, position):
        position_type = getattr(position, 'type', None)
        side = 'BUY' if position_type == mt5.POSITION_TYPE_BUY else 'SELL'
        return {
            'ticket': int(getattr(position, 'ticket', 0) or 0),
            'symbol': str(getattr(position, 'symbol', '') or ''),
            'side': side,
            'volume': self._number(getattr(position, 'volume', 0)),
            'price_open': self._number(getattr(position, 'price_open', 0)),
            'price_current': self._number(getattr(position, 'price_current', 0)),
            'sl': self._number(getattr(position, 'sl', 0)),
            'tp': self._number(getattr(position, 'tp', 0)),
            'profit': self._number(getattr(position, 'profit', 0)),
            'magic': int(getattr(position, 'magic', 0) or 0),
        }

    def _store_credentials(self, login, server, password):
        cred_file = Path.home() / '.mt5bot_credentials.json'
        temp_file = cred_file.with_suffix('.tmp')
        payload = json.dumps(
            {'login': int(login), 'server': str(server), 'password': str(password)},
            ensure_ascii=False,
        )
        temp_file.write_text(payload)
        os.chmod(temp_file, 0o600)
        temp_file.replace(cred_file)
        os.chmod(cred_file, 0o600)

    @staticmethod
    def _number(value):
        try:
            value = float(value or 0)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) else 0.0
