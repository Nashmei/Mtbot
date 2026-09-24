import asyncio
import time
from pathlib import Path

import httpx
import jwt


class APNsPushService:
    """Optional APNs provider using Apple's token-based HTTP/2 API."""

    def __init__(self, db, settings):
        self.db = db
        self.enabled = bool(getattr(settings, 'apns_enabled', False))
        self.key_id = (getattr(settings, 'apns_key_id', None) or '').strip()
        self.team_id = (getattr(settings, 'apns_team_id', None) or '').strip()
        self.auth_key_path = (getattr(settings, 'apns_auth_key_path', None) or '').strip()
        self.bundle_id = (getattr(settings, 'apns_bundle_id', None) or '').strip()
        self.environment = (
            getattr(settings, 'apns_environment', 'production') or 'production'
        ).strip().lower()
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

    async def status(self):
        devices = await self.db.active_push_devices()
        return {
            'configured': bool(self.configured),
            'registered_devices': len(devices),
            'environment': self.environment,
        }

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

    def _title(self, category):
        if category == 'trade_closed':
            return 'نتيجة الصفقة'
        if category == 'trade_opened':
            return 'T4Bot • صفقة'
        if category == 'profit_protection':
            return 'حماية الربح'
        if category in ('engine_alerts', 'connection_alerts'):
            return 'تنبيه T4Bot'
        return 'T4Bot'

    async def send_test(self):
        return await self.send_engine_notification(
            'إشعار اختبار من خادم Mtbot.',
            trade_result=None,
            trade_ticket=None,
            category_override='engine_alerts',
        )

    async def send_engine_notification(
        self,
        text,
        trade_result=None,
        trade_ticket=None,
        category_override=None,
    ):
        devices = await self.db.active_push_devices()

        if not self.configured:
            return {
                'ok': False,
                'message': 'APNs غير مهيأ على خادم Mtbot.',
                'sent': 0,
                'failed': 0,
                'registered_devices': len(devices),
            }

        if not devices:
            return {
                'ok': False,
                'message': 'لا يوجد جهاز T4Bot مسجل لدى الخادم.',
                'sent': 0,
                'failed': 0,
                'registered_devices': 0,
            }

        category = category_override or self._category(text, trade_result)
        host = (
            'api.push.apple.com'
            if self.environment == 'production'
            else 'api.sandbox.push.apple.com'
        )
        url_base = f'https://{host}/3/device/'
        token = self._provider_token()

        payload = {
            'aps': {
                'alert': {
                    'title': self._title(category),
                    'body': str(text or '')[:1500],
                },
                'sound': 'default',
            },
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

        targets = []
        tasks = []

        async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
            for device in devices:
                preferences = device.get('preferences') or {}
                if preferences.get(category, True) is False:
                    continue

                targets.append(device)
                tasks.append(
                    client.post(
                        url_base + device['token'],
                        headers=headers,
                        json=payload,
                    )
                )

            if not tasks:
                return {
                    'ok': True,
                    'message': 'لا توجد أجهزة مفعّل لها هذا النوع من الإشعارات.',
                    'sent': 0,
                    'failed': 0,
                    'registered_devices': len(devices),
                }

            results = await asyncio.gather(*tasks, return_exceptions=True)

        sent = 0
        failed = 0

        for device, result in zip(targets, results):
            if isinstance(result, Exception):
                failed += 1
                continue

            if result.status_code == 200:
                sent += 1
                continue

            failed += 1
            reason = ''
            try:
                body = result.json()
                reason = str(body.get('reason') or '')
            except Exception:
                pass

            if result.status_code in (400, 410) and reason in (
                'BadDeviceToken',
                'DeviceTokenNotForTopic',
                'Unregistered',
            ):
                await self.db.upsert_push_device(
                    device['token'],
                    enabled=False,
                    preferences=device.get('preferences') or {},
                )

        ok = sent > 0 and failed == 0
        if sent > 0:
            message = f'تم إرسال Push إلى {sent} جهاز.'
            if failed:
                message += f' فشل {failed}.'
        else:
            message = 'لم يقبل APNs أي إشعار.'

        return {
            'ok': ok,
            'message': message,
            'sent': sent,
            'failed': failed,
            'registered_devices': len(devices),
        }
