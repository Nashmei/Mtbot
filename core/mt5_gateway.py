import MetaTrader5 as mt5
import time
from .config import settings

class MT5Gateway:
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # seconds

    def _enable_algo_trading(self):
        """Enable MT5 Expert/API trading flags for the single shared terminal."""
        from pathlib import Path
        import configparser

        terminal = Path(settings.mt5_terminal_path) if settings.mt5_terminal_path else None
        if not terminal:
            return
        terminal_dir = terminal.parent
        candidates = [
            terminal_dir / 'Config' / 'common.ini',
            terminal_dir / 'config' / 'common.ini',
        ]
        ini = next((p for p in candidates if p.exists()), candidates[0])
        ini.parent.mkdir(parents=True, exist_ok=True)

        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        encoding = 'utf-16'
        if ini.exists():
            raw = ini.read_bytes()
            if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
                encoding = 'utf-16'
            elif raw.startswith(b'\xef\xbb\xbf'):
                encoding = 'utf-8-sig'
            else:
                encoding = 'utf-8'
            try:
                parser.read_string(raw.decode(encoding))
            except Exception:
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                parser.optionxform = str
        if not parser.has_section('Experts'):
            parser.add_section('Experts')
        parser.set('Experts', 'AllowLiveTrading', '1')
        parser.set('Experts', 'Enabled', '1')
        parser.set('Experts', 'Account', '0')
        parser.set('Experts', 'Profile', '0')
        parser.set('Experts', 'Api', '0')
        with open(ini, 'w', encoding=encoding, newline='') as out:
            parser.write(out, space_around_delimiters=False)

    def initialize_terminal(self):
        self._enable_algo_trading()
        for attempt in range(self.MAX_RETRIES):
            result = mt5.initialize(settings.mt5_terminal_path) if settings.mt5_terminal_path else mt5.initialize()
            if result:
                return True
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_DELAY)
        return False

    def connect(self):
        for attempt in range(self.MAX_RETRIES):
            if settings.mt5_login and settings.mt5_password and settings.mt5_server:
                res = self.login(settings.mt5_login, settings.mt5_password, settings.mt5_server)
                if res[0]:  # success
                    return res[2]  # account info
            else:
                if self.initialize_terminal():
                    return mt5.account_info()
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_DELAY)
        return None

    def login(self, login, password, server):
        self._enable_algo_trading()
        for attempt in range(self.MAX_RETRIES):
            mt5.shutdown()
            kw = {'login': int(login), 'password': password, 'server': server}
            ok = mt5.initialize(settings.mt5_terminal_path, **kw) if settings.mt5_terminal_path else mt5.initialize(**kw)
            if not ok:
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_DELAY)
                continue
            a = mt5.account_info()
            if not a:
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_DELAY)
                continue
            return True, (1, 'Success'), a
        return False, mt5.last_error(), None

    def account(self):
        for attempt in range(self.MAX_RETRIES):
            acc = mt5.account_info()
            if acc is not None:
                return acc
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_DELAY)
        return None

    def terminal(self):
        return mt5.terminal_info()

    def algo_status(self):
        t = self.terminal()
        a = self.account()
        return {
            'connected': bool(getattr(t, 'connected', False)) if t else False,
            'trade_allowed': bool(getattr(t, 'trade_allowed', False)) if t else False,
            'account_trade_allowed': bool(getattr(a, 'trade_allowed', False)) if a else False,
            'trade_expert': bool(getattr(a, 'trade_expert', False)) if a else False,
        }

    def tick(self, s):
        return mt5.symbol_info_tick(s)

    def info(self, s):
        x = mt5.symbol_info(s)
        if x and not x.visible:
            mt5.symbol_select(s, True)
            x = mt5.symbol_info(s)
        return x

    def ticks(self, s, n=300):
        from datetime import datetime, timezone, timedelta
        return mt5.copy_ticks_from(s, datetime.now(timezone.utc) - timedelta(minutes=30), n, mt5.COPY_TICKS_ALL)

    def rates(self, s, timeframe, count=200):
        # شموع مغلقة فقط
        return mt5.copy_rates_from_pos(s, timeframe, 1, count)

    def rates_m1(self, s, count=200):
        return self.rates(s, mt5.TIMEFRAME_M1, count)

    def rates_m5(self, s, count=200):
        return self.rates(s, mt5.TIMEFRAME_M5, count)

    def rates_m15(self, s, count=200):
        return self.rates(s, mt5.TIMEFRAME_M15, count)

    def rates_h1(self, s, count=200):
        return self.rates(s, mt5.TIMEFRAME_H1, count)

    def symbols(self):
        return mt5.symbols_get() or ()

    def available_symbols(self):
        return self.symbols()

    def positions(self, s=None):
        return mt5.positions_get(symbol=s) if s else mt5.positions_get()

    def position_by_ticket(self, ticket):
        rows = mt5.positions_get(ticket=int(ticket))
        return rows[0] if rows else None

    def find_new_bot_position(self, symbol, before_tickets):
        rows = mt5.positions_get(symbol=symbol) or ()
        candidates = [
            p for p in rows
            if p.ticket not in before_tickets
            and getattr(p, 'magic', 0) == 4009
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: getattr(p, 'time_msc', 0))

    def history_deals_by_position(self, position_id):
        return mt5.history_deals_get(position=position_id) or ()

    def order_check(self, r):
        return mt5.order_check(r)

    def send(self, r):
        return mt5.order_send(r)

    def modify(self, ticket, symbol, sl, tp):
        return mt5.order_send({'action': mt5.TRADE_ACTION_SLTP, 'position': ticket, 'symbol': symbol, 'sl': sl, 'tp': tp})

    def close(self, p):
        t = self.tick(p.symbol)
        side = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = t.bid if side == mt5.ORDER_TYPE_SELL else t.ask
        return mt5.order_send({
            'action': mt5.TRADE_ACTION_DEAL,
            'position': p.ticket,
            'symbol': p.symbol,
            'volume': p.volume,
            'type': side,
            'price': price,
            'deviation': settings.max_slippage_points,
            'magic': 4009,
            'comment': 'TGSCALP_CLOSE',
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': mt5.ORDER_FILLING_FOK
        })