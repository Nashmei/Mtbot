import asyncio
import hmac
import json
import math
import os
import time
from pathlib import Path

import MetaTrader5 as mt5
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from core.config import settings


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    risk_pct: float | None = Field(default=None, ge=0.25, le=50)
    min_confidence: float | None = Field(default=None, ge=50, le=95)
    min_entry_confidence: float | None = Field(default=None, ge=50, le=95)
    max_positions: int | None = Field(default=None, ge=1, le=10)
    max_consecutive_losses: int | None = Field(default=None, ge=0, le=20)
    daily_loss_limit_pct: float | None = Field(default=None, ge=0, le=100)
    max_daily_trades: int | None = Field(default=None, ge=0, le=200)
    max_correlated_positions: int | None = Field(default=None, ge=0, le=10)
    session_profit_limit: float | None = Field(default=None, ge=0, le=1000000000)
    real_trading_enabled: bool | None = None

    @model_validator(mode='after')
    def require_value(self):
        values = self.model_dump()
        if not any(value is not None for value in values.values()):
            raise ValueError('at least one setting is required')
        return self


class SymbolsPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbols: list[str] = Field(min_length=1)


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server: str = Field(min_length=1, max_length=160)
    login: int = Field(gt=0)
    password: SecretStr = Field(min_length=1, max_length=256)


class TrendViewPayload(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str = Field(min_length=1, max_length=160)
    side: str = Field(min_length=3, max_length=4)
    confidence: float | None = Field(default=75, ge=0, le=100)
    symbol: str | None = Field(default='XAUUSD', max_length=40)
    token: str = Field(min_length=8, max_length=256)


class ControlAPI:
    """Authenticated API façade over the existing Mtbot engine.

    It deliberately delegates all trading decisions and validation to the
    Engine, deterministic StrategyRegistry and MT5Gateway rather than
    duplicating that logic.
    """

    def __init__(self, engine, db, gateway, event_hub, token):
        token = (token or '').strip()
        if len(token) < 8:
            raise ValueError('CONTROL_API_TOKEN must be at least 8 characters')

        self.engine = engine
        self.db = db
        self.gateway = gateway
        self.event_hub = event_hub
        self._token = token
        self._analysis_cache = {}
        self._strategy_cache = {}

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

        @app.post('/v1/trendview/webhook')
        async def trendview_webhook(payload: TrendViewPayload):
            expected = os.getenv('TRENDVIEW_WEBHOOK_TOKEN', '').strip()
            if not expected:
                try:
                    for line in open('/home/ubuntu/.config/mtbot/trendview.env', encoding='utf-8'):
                        if line.startswith('TRENDVIEW_WEBHOOK_TOKEN='):
                            expected = line.split('=', 1)[1].strip()
                            break
                except OSError:
                    pass
            if not expected or not hmac.compare_digest(payload.token.strip(), expected):
                raise HTTPException(status_code=401, detail='Unauthorized')
            account = self.gateway.account()
            if not account or getattr(account, 'trade_mode', None) != mt5.ACCOUNT_TRADE_MODE_DEMO:
                raise HTTPException(status_code=409, detail='trendView يعمل على DEMO فقط.')
            side = payload.side.strip().upper()
            if side not in ('BUY', 'SELL'):
                raise HTTPException(status_code=422, detail='side must be BUY or SELL')
            symbol = (payload.symbol or 'XAUUSD').upper().replace('/', '')
            if 'XAU' not in symbol and 'GOLD' not in symbol:
                raise HTTPException(status_code=422, detail='trendView مخصص للذهب فقط.')
            self.engine.trendview_signal = {
                'id': payload.id, 'side': side, 'confidence': payload.confidence or 75,
                'symbol': symbol, 'received_at': time.time(),
            }
            await self.db.log('TRENDVIEW_WEBHOOK_RECEIVED', symbol, signal_id=payload.id, side=side, confidence=payload.confidence)
            return {'ok': True, 'queued': True, 'strategy': 'trendView'}

        @app.get('/v1/health')
        async def health(_=Depends(auth)):
            return {
                'ok': True,
                'service': 'mtbot-control-api',
                'api_version': 1,
                'demo_only': False,
                'supports_real': True,
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

            trade_mode = getattr(account, 'trade_mode', None)
            allowed_modes = {mt5.ACCOUNT_TRADE_MODE_DEMO, getattr(mt5, 'ACCOUNT_TRADE_MODE_REAL', 2)}
            if trade_mode not in allowed_modes:
                password = None
                mt5.shutdown()
                await self.db.log(
                    'MT5_LOGIN_REJECTED',
                    login=int(payload.login),
                    server=server,
                    reason='UNSUPPORTED_ACCOUNT_TYPE',
                    source='t4bot',
                )
                raise HTTPException(status_code=403, detail='T4Bot يدعم حسابات MT5 Demo وReal فقط.')

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
                'message': ('تم الاتصال بحساب MT5 التجريبي.' if trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else 'تم الاتصال بحساب MT5 الحقيقي. فعّل قفل Real قبل تشغيل المحرك.'),
                'account': self._account_payload(account),
            }

        @app.patch('/v1/settings')
        async def update_settings(payload: SettingsPatch, _=Depends(auth)):
            mapping = {
                'risk_pct': ('risk_pct', 'risk_pct'),
                'min_confidence': ('min_confidence', 'min_confidence'),
                'min_entry_confidence': ('min_entry_confidence', 'min_entry_confidence'),
                'max_positions': ('max_positions', 'max_positions'),
                'max_consecutive_losses': ('max_consecutive_losses', 'max_consecutive_losses'),
                'daily_loss_limit_pct': ('daily_loss_limit_pct', 'daily_loss_limit_pct'),
                'max_daily_trades': ('max_daily_trades', 'max_daily_trades'),
                'max_correlated_positions': ('max_correlated_positions', 'max_correlated_positions'),
                'session_profit_limit': ('session_profit_limit', 'session_profit_limit'),
                'real_trading_enabled': ('real_trading_enabled', 'real_trading_enabled'),
            }

            changed = {}
            for field, value in payload.model_dump(exclude_none=True).items():
                attr, db_key = mapping[field]
                setattr(self.engine, attr, value)
                stored_value = int(value) if field == 'real_trading_enabled' else value
                await self.engine.save_setting(db_key, stored_value)
                changed[field] = value

            if 'max_consecutive_losses' in changed:
                self.engine.loss_limit_notified = False
            if 'daily_loss_limit_pct' in changed:
                self.engine.daily_loss_notified = False
            if changed:
                self._strategy_cache.clear()

            await self.db.log('SETTINGS_UPDATED', source='t4bot', changed=changed)
            await self.event_hub.broadcast('settings_changed', changed)
            return {'ok': True, 'message': 'تم حفظ الإعدادات على Mtbot.'}

        @app.get('/v1/symbols')
        async def get_symbols(
            q: str = Query(default='', max_length=80),
            limit: int = Query(default=200, ge=1, le=500),
            offset: int = Query(default=0, ge=0),
            _=Depends(auth),
        ):
            symbols = list(self.gateway.available_symbols())
            active = [item.name for item in symbols if bool(getattr(item, 'visible', False))]
            names = self.gateway.ranked_symbol_names(symbols)
            query = q.strip().upper()
            if query:
                names = [name for name in names if query in name.upper()]
            total = len(names)
            page = names[offset:offset + limit]
            return {
                'selected': list(self.engine.symbols),
                'active': active,
                'available': page,
                'total': total,
                'has_more': offset + len(page) < total,
            }

        @app.put('/v1/symbols')
        async def put_symbols(payload: SymbolsPayload, _=Depends(auth)):
            symbol_rows = list(self.gateway.available_symbols())
            available = [item.name for item in symbol_rows]
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
            active = [item.name for item in symbol_rows if bool(getattr(item, 'visible', False))]
            ranked = self.gateway.ranked_symbol_names(symbol_rows)
            return {
                'selected': selected,
                'active': active,
                'available': ranked[:200],
                'total': len(ranked),
                'has_more': len(ranked) > 200,
            }

        @app.post('/v1/analysis/run')
        async def run_analysis(_=Depends(auth)):
            rows = await self._run_analysis()
            await self.event_hub.broadcast('analysis_updated', {'symbols': [row['symbol'] for row in rows]})
            return rows

        @app.get('/v1/strategies')
        async def strategies(_=Depends(auth)):
            return {
                'catalog': self.engine.strategy_catalog(),
                'performance': await self._strategy_performance(200),
            }

        @app.get('/v1/strategies/performance')
        async def strategies_performance(
            window: int = Query(default=200, ge=1, le=2000),
            _=Depends(auth),
        ):
            return await self._strategy_performance(window)

        @app.get('/v1/audit')
        async def audit(limit: int = Query(default=100, ge=1, le=500), _=Depends(auth)):
            return await self.db.recent_audit(limit)
        @app.get('/v1/trades/history')
        async def trade_history(limit: int = Query(default=100, ge=1, le=500), _=Depends(auth)):
            account = self.gateway.account()
            login = int(getattr(account, 'login', 0) or 0) if account else None
            rows = await self.db.closed_trades(limit, account_login=login)
            for row in rows:
                row['image_id'] = self.event_hub.media_id_for_ticket(row.get('ticket'))
            return rows

        @app.get('/v1/media/{media_id}')
        async def media(media_id: str, _=Depends(auth)):
            path = self.event_hub.media_path(media_id)
            if not path:
                raise HTTPException(status_code=404, detail='الصورة غير موجودة.')
            return FileResponse(path, media_type='image/png', filename=path.name)

        @app.websocket('/v1/ws')
        async def websocket_endpoint(websocket: WebSocket):
            if not self._authorized(websocket.headers.get('authorization')):
                await websocket.close(code=4401)
                return

            queue = await self.event_hub.connect(websocket)
            last_state = None
            last_snapshot_sent = 0.0
            next_snapshot = 0.0

            try:
                while True:
                    now = time.monotonic()
                    timeout = max(0.0, next_snapshot - now)

                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=timeout)
                        await websocket.send_json(event)
                        continue
                    except asyncio.TimeoutError:
                        pass

                    payload = await self.snapshot()
                    comparable = dict(payload)
                    comparable['server_time'] = 0.0
                    now = time.monotonic()

                    if comparable != last_state or now - last_snapshot_sent >= 1.0:
                        await websocket.send_json({
                            'type': 'snapshot',
                            'ts': time.time(),
                            'payload': payload,
                        })
                        last_state = comparable
                        last_snapshot_sent = now

                    # MT5-backed values are sampled at 20 Hz, while unchanged
                    # payloads are coalesced. Engine events bypass this cadence
                    # through the queue above and are sent immediately.
                    next_snapshot = time.monotonic() + 0.05
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
        session_visible = bool(self.engine.session_active or self.engine.session_profit_hit)
        session_start = self._number(self.engine.session_start_balance) if session_visible else 0.0
        session_profit = (
            self._number((getattr(account,'balance',0) or 0)-self.engine.session_start_balance)
            if session_visible and self.engine.session_start_balance>0 else 0.0
        )
        return {
            'server_time': time.time(),
            'engine': {
                'running': bool(self.engine.running),
                'scan_count': int(self.engine.scan_count),
                'last_cycle_seconds': self._number(self.engine.last_cycle_seconds),
                'last_cycle_at': self._number(self.engine.last_cycle_at),
                'tracked_positions': len(self.engine.trades),
                'max_positions': int(self.engine.max_positions),
                'session_start_balance': session_start,
                'session_profit': session_profit,
                'session_profit_hit': bool(self.engine.session_profit_hit),
            },
            'account': self._account_payload(account) if account else None,
            'positions': [self._position_payload(position) for position in positions],
            'settings': {
                'symbols': list(self.engine.symbols),
                'risk_pct': self._number(self.engine.risk_pct),
                'min_confidence': self._number(self.engine.min_confidence),
                'min_entry_confidence': self._number(self.engine.min_entry_confidence),
                'max_positions': int(self.engine.max_positions),
                'max_consecutive_losses': int(self.engine.max_consecutive_losses),
                'daily_loss_limit_pct': self._number(self.engine.daily_loss_limit_pct),
                'max_daily_trades': int(self.engine.max_daily_trades),
                'max_correlated_positions': int(self.engine.max_correlated_positions),
                'session_profit_limit': self._number(self.engine.session_profit_limit),
                'real_trading_enabled': bool(self.engine.real_trading_enabled),
            },
            'analysis': list(self._analysis_cache.values()),
            'strategies': {
                'catalog_size': len(self.engine.registry.strategies),
                'performance': await self._strategy_performance(200),
            },
            'readiness': {
                'connected': bool(readiness.get('connected')),
                'trade_allowed': bool(readiness.get('trade_allowed')),
                'account_trade_allowed': bool(readiness.get('account_trade_allowed')),
                'trade_expert': bool(readiness.get('trade_expert')),
            },
        }

    async def _strategy_performance(self, window=200):
        """Cached per strategy/symbol/regime metrics shared by HTTP surfaces."""
        now = time.monotonic()
        cached = self._strategy_cache.get(int(window))
        if cached and (now - cached['at']) < 5.0:
            return cached['rows']
        rows = await self.engine.strategy_report(window=window)
        self._strategy_cache[int(window)] = {'rows': rows, 'at': now}
        return rows

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
            live_tick = self.gateway.tick(symbol)
            live_epoch = (
                float(getattr(live_tick, 'time_msc', 0) or 0) / 1000.0
                if live_tick else 0.0
            )
            if live_epoch <= 0 and live_tick:
                live_epoch = float(getattr(live_tick, 'time', 0) or 0)
            if live_epoch <= 0 or abs(live_epoch - tick_ts) > settings.max_tick_age_seconds:
                row = self._analysis_unavailable(symbol, 'stale_tick', updated_at)
                rows.append(row)
                self._analysis_cache[symbol] = row
                continue

            decision, error = await self.engine.analyze_symbol(symbol)
            if error:
                row = self._analysis_unavailable(symbol, error.lower(), updated_at)
            elif decision.get('decision') == 'SIGNAL':
                row = {
                    'symbol': symbol, 'regime': decision.get('regime', 'UNKNOWN'), 'state': 'signal',
                    'side': decision.get('side'), 'strategy': decision.get('strategy_id'),
                    'confidence': self._number(float(decision.get('confidence', 0)) / 100.0),
                    'reason': decision.get('reason', ''), 'updated_at': updated_at,
                }
            else:
                row = {
                    'symbol': symbol, 'regime': decision.get('regime', 'NO_TRADE'), 'state': 'no_signal',
                    'side': None, 'strategy': None,
                    'confidence': self._number(float(decision.get('confidence', 0)) / 100.0),
                    'reason': decision.get('reason_code') or decision.get('reason', 'NO_TRADE'),
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
            'account_type': ('demo' if getattr(account, 'trade_mode', None) == mt5.ACCOUNT_TRADE_MODE_DEMO else ('real' if getattr(account, 'trade_mode', None) == getattr(mt5, 'ACCOUNT_TRADE_MODE_REAL', 2) else 'unsupported')),
            'real_trading_enabled': bool(self.engine.real_trading_enabled),
        }

    def _position_payload(self, position):
        position_type = getattr(position, 'type', None)
        side = 'BUY' if position_type == mt5.POSITION_TYPE_BUY else 'SELL'
        payload = {
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
            'image_id': self.event_hub.media_id_for_ticket(getattr(position, 'ticket', 0)),
        }
        state = self.engine.trades.get(payload['ticket'])
        if state is not None:
            payload.update({
                'strategy': state.strategy,
                'regime': state.regime,
                'confidence': self._number(state.confidence),
                'tp1': self._number(state.tp1),
                'tp2': self._number(state.tp2),
                'mfe_r': self._number(state.mfe_r),
                'mae_r': self._number(state.mae_r),
                'risk_cash': self._number(state.risk_cash),
                'protection_active': bool(state.protection_45_active),
                'trailing_active': bool(state.trailing_moved),
                'breakeven_done': bool(state.be_done),
                'tp1_hit': bool(state.tp1_hit),
            })
        return payload

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
