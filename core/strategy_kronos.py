import json, os, time
from . import indicators as ta
from .models import Side
from .strategy_base import StrategySpec, signal_decision, structural_stop

class KronosForecastStrategy:
    def __init__(self, settings=None): self.settings=settings
    def eligible(self, ctx, registry=None):
        if registry is not None and not registry.combo_enabled(self,ctx): return False,'COMBO_DISABLED'
        regime=ctx.regime.value if ctx.regime else 'NO_TRADE'
        if regime not in self.spec.regimes:return False,'REGIME_NOT_ALLOWED'
        if not (ctx.is_gold or 'EURUSD' in ctx.symbol.upper()):return False,'SYMBOL_NOT_ALLOWED'
        return True,'OK'
    def wait(self,ctx,code,reason):
        from .strategy_base import base_decision
        return base_decision(self.spec,ctx,code,reason)

    spec=StrategySpec(id='kronos_forecast',name='Kronos K-line Forecast',family='foundation_model',timeframes=('M5',),symbols=('XAUUSD','EURUSD'),regimes=('TREND','RANGE','BREAKOUT','VOLATILE','NO_TRADE'),indicators=('Kronos-small','OHLCV M5'),tier='C',trial=True,min_confidence=68.0)
    def analyze(self,ctx):
        rows=ctx.frame('M5')
        if len(rows)<80:return self.wait(ctx,'INSUFFICIENT_HISTORY','Kronos needs 80 M5 bars')
        root=os.getenv('KRONOS_SIGNAL_DIR','/home/ubuntu/.wine/drive_c/mt5bot/kronos_runtime/signals')
        safe=''.join(c for c in ctx.symbol if c.isalnum() or c in '._-')
        try:
            with open(os.path.join(root,safe+'_M5.json'),encoding='utf-8') as f: fc=json.load(f)
        except Exception:return self.wait(ctx,'KRONOS_UNAVAILABLE','No Kronos forecast')
        # CPU inference is intentionally asynchronous.  Accept the most recent
        # completed M5 forecast for up to 30 minutes; a new file supersedes it.
        if time.time()-float(fc.get('generated_at',0) or 0)>1800:return self.wait(ctx,'KRONOS_STALE','Kronos forecast stale')
        stxt=str(fc.get('side','NONE')).upper(); conf=float(fc.get('confidence',0) or 0)
        if stxt not in ('BUY','SELL') or conf<68:return self.wait(ctx,'KRONOS_NO_EDGE','No strong Kronos consensus')
        side=Side.BUY if stxt=='BUY' else Side.SELL; price=ctx.price_for(side)
        anchor=min(ta.lows(rows[-12:])) if side==Side.BUY else max(ta.highs(rows[-12:]))
        ss=structural_stop(ctx,side,price,anchor,buffer_points=4,min_atr_frac=.15,atr_frame='M5',max_atr_mult=2.2,min_atr_risk_frac=.45)
        if not ss:return self.wait(ctx,'STOP_TOO_WIDE','Kronos stop too wide')
        stop,risk=ss; target=float(fc.get('expected_high' if side==Side.BUY else 'expected_low',0) or 0)
        if (side==Side.BUY and target<=price) or (side==Side.SELL and target>=price):return self.wait(ctx,'KRONOS_BAD_GEOMETRY','Bad forecast target')
        if abs(target-price)/risk<1.5:return self.wait(ctx,'KRONOS_RR_TOO_LOW','Forecast below 1.5R')
        return signal_decision(self.spec,ctx,side,price,stop,target,conf,f'Kronos {stxt} {conf:.1f}% consensus',reason_code='KRONOS_FORECAST',management={'protection_pct':35.0,'trailing_trigger_pct':65.0,'trailing_gap_pct':8.0,'max_hold_minutes':0},regime=ctx.regime.value)
