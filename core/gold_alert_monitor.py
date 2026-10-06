import asyncio
import time


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    out = float(values[0])
    for value in values[1:]:
        out = float(value) * k + out * (1.0 - k)
    return out


def _atr(rows, period=14):
    if rows is None or len(rows) < period + 1:
        return None
    tr = []
    for i in range(1, len(rows)):
        h, l = float(rows[i]['high']), float(rows[i]['low'])
        pc = float(rows[i - 1]['close'])
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(tr[-period:]) / period


class GoldAlertMonitor:
    """Read-only XAU monitor. It never sends an order to MT5."""
    def __init__(self, gateway, notify, interval=5.0, cooldown=300.0):
        self.gw = gateway
        self.notify = notify
        self.interval = interval
        self.cooldown = cooldown
        self.last_key = None
        self.last_sent = 0.0

    def _gold_symbol(self):
        for name in self.gw.ranked_symbol_names():
            if 'XAUUSD' in name.upper():
                return name
        return None

    def _setup(self, symbol):
        tick = self.gw.tick(symbol)
        m1 = self.gw.rates_m1(symbol, 80)
        m5 = self.gw.rates_m5(symbol, 80)
        m15 = self.gw.rates_m15(symbol, 100)
        h1 = self.gw.rates_h1(symbol, 100)
        if tick is None or any(x is None or len(x) < 30 for x in (m1, m5, m15, h1)):
            return None
        bid, ask = float(tick.bid), float(tick.ask)
        mid = (bid + ask) / 2.0
        atr5 = _atr(m5)
        if not atr5 or atr5 <= 0:
            return None
        spread = ask - bid
        if spread > atr5 * 0.12:
            return None
        h1c = [float(x['close']) for x in h1]
        m15c = [float(x['close']) for x in m15]
        m1c = [float(x['close']) for x in m1]
        h20, h50 = _ema(h1c, 20), _ema(h1c, 50)
        m15_20, m15_50 = _ema(m15c, 20), _ema(m15c, 50)
        m1_5, m1_9 = _ema(m1c, 5), _ema(m1c, 9)
        if None in (h20, h50, m15_20, m15_50, m1_5, m1_9):
            return None
        resistance = max(float(x['high']) for x in m5[-25:-1])
        support = min(float(x['low']) for x in m5[-25:-1])
        last5, prev5 = m5[-1], m5[-2]
        body = abs(float(last5['close']) - float(last5['open']))
        rng = max(float(last5['high']) - float(last5['low']), 1e-9)
        strong = body / rng >= 0.45
        trend_up = h20 > h50 and m15_20 > m15_50
        trend_down = h20 < h50 and m15_20 < m15_50
        m1_up, m1_down = m1_5 > m1_9, m1_5 < m1_9
        close5 = float(last5['close'])
        prev_close = float(prev5['close'])
        buy_break = trend_up and m1_up and strong and close5 > resistance and prev_close <= resistance
        sell_break = trend_down and m1_down and strong and close5 < support and prev_close >= support
        reject_sell = (trend_down and m1_down and float(last5['high']) >= resistance - atr5 * .12
                       and close5 < resistance - atr5 * .08 and float(last5['close']) < float(last5['open']))
        reject_buy = (trend_up and m1_up and float(last5['low']) <= support + atr5 * .12
                      and close5 > support + atr5 * .08 and float(last5['close']) > float(last5['open']))
        side = 'BUY' if buy_break or reject_buy else 'SELL' if sell_break or reject_sell else None
        if not side:
            return None
        reason = ('كسر وإغلاق M5 مؤكد' if buy_break or sell_break else 'رفض سعري مؤكد من منطقة ديناميكية')
        entry = ask if side == 'BUY' else bid
        sl = (min(float(x['low']) for x in m5[-4:]) - atr5 * .12 if side == 'BUY'
              else max(float(x['high']) for x in m5[-4:]) + atr5 * .12)
        risk = abs(entry - sl)
        if risk < atr5 * .18 or risk > atr5 * 1.5:
            return None
        tp1 = entry + risk * 1.8 if side == 'BUY' else entry - risk * 1.8
        tp2 = entry + risk * 2.8 if side == 'BUY' else entry - risk * 2.8
        return dict(symbol=symbol, side=side, entry=entry, sl=sl, tp1=tp1, tp2=tp2,
                    support=support, resistance=resistance, atr=atr5, reason=reason, price=mid)

    async def run(self):
        while True:
            try:
                symbol = self._gold_symbol()
                setup = await asyncio.to_thread(self._setup, symbol) if symbol else None
                if setup:
                    key = (setup['side'], round(setup['support'], 1), round(setup['resistance'], 1))
                    now = time.monotonic()
                    if key != self.last_key or now - self.last_sent >= self.cooldown:
                        self.last_key, self.last_sent = key, now
                        s = setup
                        text = (f"🚨 فرصة {s['symbol']} — {s['side']}\n"
                                f"💹 السعر: {s['price']:.2f}\n✅ {s['reason']}\n"
                                f"📊 دعم: {s['support']:.2f} | مقاومة: {s['resistance']:.2f}\n"
                                f"➡️ دخول مرجعي: {s['entry']:.2f}\n🛑 SL: {s['sl']:.2f}\n"
                                f"🎯 TP1: {s['tp1']:.2f} | TP2: {s['tp2']:.2f}\n"
                                "📋 راجع الصفقة ثم نفّذها يدويًا من MT5 — لا يوجد تنفيذ تلقائي.")
                        await self.notify(text, event_type='gold_setup_alert', symbol=s['symbol'], side=s['side'])
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                print('Gold alert monitor:', ex)
            await asyncio.sleep(self.interval)
