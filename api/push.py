import asyncio
import time
from pathlib import Path

import httpx
import jwt


class APNsPushService:
    """Optional APNs provider. No-ops until token-auth credentials are configured."""

    def __init__(self, db, settings):
        self.db = db
        self.enabled = bool(getattr(settings, 'apns_enabled', False))
        self.key_id = (getattr(settings, 'apns_key_id', None) or '').strip()
        self.team_id = (getattr(settings, 'apns_team_id', None) or '').strip()
        self.auth_key_path = (getattr(settings, 'apns_auth_key_path', None) or '').strip()
        self.bundle_id = (getattr(settings, 'apns_bundle_id', None) or '').strip()
        self.environment = (getattr(settings, 'apns_environment', 'production') or 'production').strip().lower()
        self._jwt = None
        self._jwt_issued_at = 0.0

    @property
    def configured(self):
        return (
            self.enabled
            and bool(self.key_id)
            and bool(self.team_id)
            and bool(self.bundle_id)
            and bool(self.auth_key_path)
            and Path(self.auth_key_path).is_file()
        )

    def _provider_token(self):
        now = int(time.time())
        if self._jwt and now - self._jwt_issued_at < 45 * 60:
            return self._jwt

        key = Path(self.auth_key_path).read_text()
        self._jwt = jwt.encode(
            {'iss': self.team_id, 'iat': now},
            key,
            algorithm='ES256',
            headers={'kid': self.key_id},
        )
        self._jwt_issued_at = now
        return self._jwt

    def _category(self, text, trade_result):
        text = str(text or '')
        if trade_result is not None or 'النتيجة' in text or 'TP' in text or 'SL' in text:
            return 'trade_closed'
        if 'حماية' in text:
            return 'profit_protection'
        if 'اتصال' in text or 'MT5' in text:
            return 'connection_alerts'
        if 'تنفيذ' in text or 'صفقة' in text:
            return 'trade_opened'
        return 'engine_alerts'

    async def send_engine_notification(self, text, trade_result=None, trade_ticket=None):
        if not self.configured:
            return

        category = self._category(text, trade_result)
        devices = await self.db.active_push_devices()
        if not devices:
            return

        host = 'api.push.apple.com' if self.environment == 'production' else 'api.sandbox.push.apple.com'
        url_base = f'https://{host}/3/device/'
        token = self._provider_token()
        title = 'T4Bot'
        if category == 'trade_closed':
            title = 'نتيجة الصفقة'
        elif category == 'trade_opened':
            title = 'T4Bot • صفقة'
        elif category == 'profit_protection':
            title = 'حماية الربح'
        elif category in ('engine_alerts', 'connection_alerts'):
            title = 'تنبيه T4Bot'

        aps = {
            'alert': {'title': title, 'body': str(text or '')[:1500]},
            'sound': 'default',
            'mutable-content': 1,
        }
        payload = {
            'aps': aps,
            'category': category,
            'trade_ticket': trade_ticket,
            'trade_result': trade_result,
        }
        headers = {
            'authorization': f'bearer {token}',
            'apns-topic': self.bundle_id,
            'apns-push-type': 'alert',
            'apns-priority': '10',
        }

        async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
            tasks = []
            targets = []
            for device in devices:
                prefs = device.get('preferences') or {}
                if prefs.get(category, True) is False:
                    continue
                targets.append(device)
                tasks.append(client.post(url_base + device['token'], headers=headers, json=payload))

            if not tasks:
                return

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for device, result in zip(targets, results):
                if isinstance(result, Exception):
                    continue
                if result.status_code in (400, 410):
                    try:
                        body = result.json()
                    except Exception:
                        body = {}
                    if body.get('reason') in ('BadDeviceToken', 'DeviceTokenNotForTopic', 'Unregistered'):
                        await self.db.upsert_push_device(
                            device['token'],
                            enabled=False,
                            preferences=device.get('preferences') or {},
                        )
