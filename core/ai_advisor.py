"""Optional AI cross-check layer.

Design decision: deterministic strategies own every entry, stop and target.
The AI's only permitted role is to *optionally veto* a deterministic signal
when an independent, data-only reading of the same supplied bars disagrees.
It can never:

  * create a trade on its own,
  * move a stop or target,
  * change position size or risk,
  * see or request data that was not supplied by the engine.

The layer is disabled unless ``AI_CROSSCHECK_ENABLED=true`` and a provider
key are present, so a missing/broken provider can never block trading, and no
network call is made in backtests.
"""

import asyncio
import json
import math
import os
import re
import time
import urllib.error
import urllib.request

from .config import settings

SYSTEM = '''You are a conservative intraday risk screener for MetaTrader 5.

You receive closed-bar market data and one already-validated deterministic setup
(side, entry, stop loss, target). Your ONLY job is to decide whether that setup
should be VETOED.

Rules:
- Use only the supplied data. Never invent prices, news, sentiment or volume.
- VETO only for a concrete, observable contradiction in the supplied data,
  e.g. price is already at/through the stop, the stated direction fights an
  obvious dominant trend on the supplied M15 bars, or an extreme spread/ATR
  ratio makes execution unsafe.
- Do not veto merely because you would have chosen different levels. The
  deterministic strategy owns entry, stop and target.
- If the data is sufficient and no contradiction exists, do not veto.

Return ONLY a JSON object:
{"veto": true|false, "confidence": 0-100, "reason_code": "SHORT_TOKEN", "reason": "short rationale"}
'''


class AIAdvisor:
    def __init__(self, db=None, provider=None):
        self.db = db
        self.provider = provider
        self.enabled = _env_flag('AI_CROSSCHECK_ENABLED', False)
        self.api_key = (os.getenv('DEEPSEEK_API_KEY') or '').strip()
        self.base_url = os.getenv('AI_CROSSCHECK_URL', 'https://api.deepseek.com/chat/completions').strip()
        self.model = os.getenv('AI_CROSSCHECK_MODEL', 'deepseek-chat').strip()
        self.timeout = float(os.getenv('AI_CROSSCHECK_TIMEOUT', '8.0') or 8.0)
        self.min_veto_confidence = float(os.getenv('AI_CROSSCHECK_MIN_VETO_CONF', '80') or 80)
        self.error_streak = 0
        self.max_error_streak = 5
        self.cooldown_until = 0.0

    @property
    def active(self):
        return bool(self.enabled and self.api_key) and time.time() >= self.cooldown_until

    # ------------------------------------------------------------------
    def snapshot(self, ctx, decision):
        """Data-only payload built from the same closed bars the strategy used."""
        def bars(name, count):
            out = []
            for row in ctx.frame(name)[-count:]:
                out.append({
                    't': int(float(row.get('time') or 0)),
                    'o': round(float(row.get('open') or 0), 6),
                    'h': round(float(row.get('high') or 0), 6),
                    'l': round(float(row.get('low') or 0), 6),
                    'c': round(float(row.get('close') or 0), 6),
                    'v': float(row.get('tick_volume') or 0),
                })
            return out
        atr5 = ctx.atr('M5', 14)
        return {
            'symbol': ctx.symbol,
            'symbol_class': getattr(ctx, 'symbol_class', '') or '',
            'bid': ctx.bid,
            'ask': ctx.ask,
            'spread_points': round(ctx.spread_points, 2),
            'regime': ctx.regime.value,
            'regime_detail': {k: v for k, v in ctx.regime_detail.items() if not isinstance(v, dict)},
            'setup': {
                'strategy_id': decision.get('strategy_id'),
                'side': decision.get('side'),
                'entry': decision.get('entry'),
                'stop_loss': decision.get('sl_price'),
                'take_profit': decision.get('tp_price'),
                'tp1': decision.get('tp1'),
                'reason_code': decision.get('reason_code'),
            },
            'atr_m5': round(atr5[-1], 6) if atr5 and atr5[-1] else None,
            'm1': bars('M1', 30),
            'm5': bars('M5', 30),
            'm15': bars('M15', 24),
        }

    # ------------------------------------------------------------------
    async def review(self, ctx, decision):
        """Return ``(allow, meta)``. Never raises; fails open (allow)."""
        if not self.active:
            return True, {'enabled': False}
        payload = self.snapshot(ctx, decision)
        try:
            raw = await asyncio.to_thread(self._call_sync, payload)
        except Exception as exc:
            self._note_error()
            await self._log(decision.get('symbol') or payload['symbol'],
                            {'error': repr(exc), 'outcome': 'fail_open'})
            return True, {'enabled': True, 'error': repr(exc)}
        parsed = self._parse(raw)
        if parsed is None:
            self._note_error()
            await self._log(payload['symbol'], {'error': 'parse_failed', 'raw': raw[:400],
                                                'outcome': 'fail_open'})
            return True, {'enabled': True, 'error': 'parse_failed'}
        self.error_streak = 0
        veto = bool(parsed.get('veto'))
        confidence = float(parsed.get('confidence') or 0)
        allowed = not (veto and confidence >= self.min_veto_confidence)
        meta = {
            'enabled': True, 'veto': veto, 'confidence': confidence,
            'reason_code': parsed.get('reason_code'), 'reason': parsed.get('reason'),
            'allowed': allowed,
        }
        await self._log(payload['symbol'], meta)
        return allowed, meta

    def _note_error(self):
        self.error_streak += 1
        if self.error_streak >= self.max_error_streak:
            self.cooldown_until = time.time() + 300.0
            self.error_streak = 0

    async def _log(self, symbol, payload):
        if self.db is None:
            return
        try:
            await self.db.log('AI_CROSSCHECK', symbol, **payload)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _call_sync(self, payload):
        body = json.dumps({
            'model': self.model,
            'temperature': 0,
            'messages': [
                {'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ],
        }).encode('utf-8')
        request = urllib.request.Request(
            self.base_url, data=body,
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {self.api_key}'},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
        choices = data.get('choices') or []
        if not choices:
            raise RuntimeError('provider returned no choices')
        return str(choices[0].get('message', {}).get('content') or '')

    @staticmethod
    def _parse(raw):
        text = str(raw or '').strip()
        if not text:
            return None
        match = re.search(r'\{.*\}', text, re.S)
        if match:
            text = match.group(0)
        try:
            obj = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(obj, dict) or 'veto' not in obj:
            return None
        out = {
            'veto': bool(obj.get('veto')),
            'confidence': 0.0,
            'reason_code': str(obj.get('reason_code') or '')[:40],
            'reason': str(obj.get('reason') or '')[:280],
        }
        try:
            confidence = float(obj.get('confidence') or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        out['confidence'] = confidence if math.isfinite(confidence) else 0.0
        return out


def _env_flag(name, default=False):
    raw = (os.getenv(name) or '').strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on')
