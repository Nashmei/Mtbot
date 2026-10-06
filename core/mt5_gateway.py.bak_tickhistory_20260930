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
            if self.initialize_terminal():
                return mt5.account_info()
            if attempt < self.MAX_RETRIES - 1:
                time.sleep(self.RETRY_DELAY)
        return None

    def login(self, login, password, server):
        self._enable_algo_trading()

        # Reuse an already-authorized terminal session when it is the requested
        # account. This preserves broker discovery/session state established by
        # the MT5 terminal itself instead of tearing it down and reconnecting.
        attached = mt5.initialize(settings.mt5_terminal_path) if settings.mt5_terminal_path else mt5.initialize()
        if attached:
            current = mt5.account_info()
            if current is not None and int(current.login) == int(login):
                return True, (1, 'Success - existing terminal session'), current

        # Resolve an exact broker server name first. Unknown brokers therefore
        # behave like MT5's Find Your Broker flow without exposing credentials
        # to the resolver. The original server name remains the final fallback.
        from .broker_resolver import resolve_server
        server_name = str(server).strip()
        candidates = resolve_server(server_name, timeout=5.0)
        if server_name and server_name not in candidates:
            candidates.append(server_name)

        for candidate in candidates:
            mt5.shutdown()
            kw = {
                'login': int(login),
                'password': password,
                'server': candidate,
                'timeout': 8000,
            }
            ok = mt5.initialize(settings.mt5_terminal_path, **kw) if settings.mt5_terminal_path else mt5.initialize(**kw)
            if not ok:
                continue
            a = mt5.account_info()
            if a is not None and int(a.login) == int(login):
                # Refresh the broker-specific symbol universe after every
                # account switch. Selecting popular tradable symbols primes
                # MT5 market data without carrying assumptions from the prior
                # broker (suffixes such as EURUSDm vs EURUSD can differ).
                symbols = mt5.symbols_get() or ()
                popular = ('EURUSD','GBPUSD','USDJPY','AUDUSD','USDCAD','USDCHF','NZDUSD','XAUUSD','XAGUSD')
                for base in popular:
                    matches = [x.name for x in symbols if getattr(x, 'trade_mode', 0) != mt5.SYMBOL_TRADE_MODE_DISABLED and (x.name == base or x.name.startswith(base))]
                    if matches:
                        mt5.symbol_select(matches[0], True)
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
        # MT5/Wine may transiently return None/zero quotes even while the
        # terminal is connected. Hydrate/select once and absorb short gaps
        # here so every caller gets the same validated quote behaviour.
        last = None
        for attempt in range(5):
            x = mt5.symbol_info_tick(s)
            if x is not None:
                last = x
                if float(getattr(x, 'bid', 0) or 0) > 0 and float(getattr(x, 'ask', 0) or 0) > float(getattr(x, 'bid', 0) or 0):
                    return x
            if attempt == 0:
                mt5.symbol_select(s, True)
            time.sleep(0.08)
        return last

    def info(self, s):
        x = mt5.symbol_info(s)
        if x and not x.visible:
            mt5.symbol_select(s, True)
            x = mt5.symbol_info(s)
        return x

    def ticks(self, s, n=300, minimum=40):
        """Return recent ticks and adapt immediately after broker/account switches.

        MT5 can keep an empty local tick cache for newly selected symbols after
        login. copy_ticks_from asks the terminal to hydrate that symbol history;
        the range scan remains a fallback for brokers that serve range history
        more reliably.
        """
        from datetime import datetime, timezone, timedelta
        info = self.info(s)
        if info is None:
            return None
        # Explicitly select the symbol for the current account before requesting
        # history. This is harmless when already selected and important after a
        # broker/account switch where the symbol universe may have changed.
        mt5.symbol_select(s, True)
        live = mt5.symbol_info_tick(s)
        live_epoch = float(getattr(live, 'time_msc', 0) or 0) / 1000.0
        if live_epoch <= 0:
            live_epoch = float(getattr(live, 'time', 0) or 0)
        # Anchor history to the broker's own live-tick clock. Some MT5/Wine
        # broker sessions expose timestamps offset from the host UTC clock.
        # Using the same broker clock at both ends avoids requesting a future
        # range after an account/server switch.
        end = datetime.fromtimestamp(live_epoch, timezone.utc) if live_epoch > 0 else datetime.now(timezone.utc)
        best = None

        for minutes in (1, 5, 15, 30, 60, 180, 360, 720, 1440):
            rows = mt5.copy_ticks_range(s, end - timedelta(minutes=minutes), end, mt5.COPY_TICKS_ALL)
            if rows is not None and (best is None or len(rows) > len(best)):
                best = rows
            if rows is not None and len(rows) >= minimum:
                return rows[-n:]
        return best[-n:] if best is not None else None

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

    def rates_h4(self, s, count=200):
        return self.rates(s, mt5.TIMEFRAME_H4, count)

    def rates_d1(self, s, count=200):
        return self.rates(s, mt5.TIMEFRAME_D1, count)

    def rates_w1(self, s, count=200):
        return self.rates(s, mt5.TIMEFRAME_W1, count)

    def symbols(self):
        return mt5.symbols_get() or ()

    def available_symbols(self):
        # Broker is the source of truth. Do not hard-code a tradable universe.
        return tuple(x for x in self.symbols() if getattr(x, 'trade_mode', 0) != mt5.SYMBOL_TRADE_MODE_DISABLED)

    def ranked_symbol_names(self, symbols=None):
        """Popular markets first, then every other tradable broker symbol."""
        popular = ('XAUUSD','EURUSD','GBPUSD','USDJPY','AUDUSD','USDCAD','USDCHF','NZDUSD',
                   'EURJPY','GBPJPY','EURGBP','XAGUSD')
        source = tuple(symbols) if symbols is not None else self.available_symbols()
        names = [x.name for x in source]
        def base_rank(name):
            upper = name.upper()
            for i, base in enumerate(popular):
                if base in upper:
                    return (i, upper)
            return (len(popular), upper)
        return sorted(dict.fromkeys(names), key=base_rank)

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

    def filling_for(self, info):
        """Choose a filling policy permitted by this symbol's execution mode."""
        mode = getattr(info, 'trade_exemode', None)
        flags = int(getattr(info, 'filling_mode', 0) or 0)
        if mode in (mt5.SYMBOL_TRADE_EXECUTION_REQUEST, mt5.SYMBOL_TRADE_EXECUTION_INSTANT) or flags & 1:
            return mt5.ORDER_FILLING_FOK
        if flags & 2:
            return mt5.ORDER_FILLING_IOC
        if mode != mt5.SYMBOL_TRADE_EXECUTION_MARKET:
            return mt5.ORDER_FILLING_RETURN
        return None

    def send(self, r):
        return mt5.order_send(r)

    def modify(self, ticket, symbol, sl, tp):
        return mt5.order_send({'action': mt5.TRADE_ACTION_SLTP, 'position': ticket, 'symbol': symbol, 'sl': sl, 'tp': tp})

    def close_partial(self, p, volume, deviation=None):
        t = self.tick(p.symbol)
        info = self.info(p.symbol)
        filling = self.filling_for(info) if info else None
        if not t or not info or filling is None:
            return None
        volume = float(volume)
        if volume <= 0 or volume >= float(p.volume):
            return None
        side = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = t.bid if side == mt5.ORDER_TYPE_SELL else t.ask
        return mt5.order_send({
            'action': mt5.TRADE_ACTION_DEAL,
            'position': p.ticket,
            'symbol': p.symbol,
            'volume': volume,
            'type': side,
            'price': price,
            'deviation': int(deviation if deviation is not None else settings.max_slippage_points),
            'magic': 4009,
            'comment': 'TGSCALP_TP1',
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': filling
        })

    def close(self, p, deviation=None):
        t = self.tick(p.symbol)
        info = self.info(p.symbol)
        filling = self.filling_for(info) if info else None
        if not t or filling is None:
            return None
        side = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = t.bid if side == mt5.ORDER_TYPE_SELL else t.ask
        return mt5.order_send({
            'action': mt5.TRADE_ACTION_DEAL,
            'position': p.ticket,
            'symbol': p.symbol,
            'volume': p.volume,
            'type': side,
            'price': price,
            'deviation': int(deviation if deviation is not None else settings.max_slippage_points),
            'magic': 4009,
            'comment': 'TGSCALP_CLOSE',
            'type_time': mt5.ORDER_TIME_GTC,
            'type_filling': filling
        })
