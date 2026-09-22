import argparse
from datetime import datetime, timezone
import MetaTrader5 as mt5
import numpy as np
from core.analyzer import Analyzer
from core.models import Side

SYMBOLS=['EURUSD','XAUUSD','USDJPY']
RR=1.5
RISK_PCT=2.0
PROTECTION_PCT=50.0
MAX_MINUTES=15
MAX_POSITIONS=5
MIN_CONFIDENCE=75.0

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--days',type=int,default=30)
 a=p.parse_args()
 if not mt5.initialize():
  raise SystemExit(f'MT5 initialize failed: {mt5.last_error()}')
 end=datetime.now(timezone.utc); start=datetime.fromtimestamp(end.timestamp()-a.days*86400,timezone.utc)
 an=Analyzer(); rows=[]
 for symbol in SYMBOLS:
  info=mt5.symbol_info(symbol)
  if not info: continue
  mt5.symbol_select(symbol,True)
  m1=mt5.copy_rates_range(symbol,mt5.TIMEFRAME_M1,start,end)
  m5=mt5.copy_rates_range(symbol,mt5.TIMEFRAME_M5,start,end)
  m15=mt5.copy_rates_range(symbol,mt5.TIMEFRAME_M15,start,end)
  ticks=mt5.copy_ticks_range(symbol,start,end,mt5.COPY_TICKS_ALL)
  if m1 is None or m5 is None or m15 is None or ticks is None: continue
  # Pre-index time arrays so each minute slices data in O(log n), not full-array scans.
  tt=ticks['time']; t5=m5['time']; t15=m15['time']; t1=m1['time']
  print(f'[{symbol}] loaded: {len(m1)} M1 bars, {len(ticks)} ticks', flush=True)
  last_exit=0
  for bar in m1[60:]:
   ts=int(bar['time'])
   if ts<last_exit: continue
   lo=np.searchsorted(tt,ts-1800,side='left'); hi=np.searchsorted(tt,ts,side='right')
   i5=np.searchsorted(t5,ts,side='left'); i15=np.searchsorted(t15,ts,side='left')
   tk=ticks[lo:hi]
   r5=m5[max(0,i5-200):i5]; r15=m15[max(0,i15-200):i15]
   if len(tk)<80 or len(r5)<60: continue
   reg,sig,meta=an.analyze(tk,info.point,r5,symbol=symbol,rates_m15=r15)
   if not sig or sig.confidence*100<MIN_CONFIDENCE: continue
   entry=float(tk['ask'][-1] if sig.side==Side.BUY else tk['bid'][-1])
   spread=float(tk['ask'][-1]-tk['bid'][-1])
   d=max(float(sig.sl_points),10.0)*info.point+spread
   sl=entry-d if sig.side==Side.BUY else entry+d
   tp=entry+abs(entry-sl)*RR if sig.side==Side.BUY else entry-abs(entry-sl)*RR
   # Tick-level exit simulation: original SL/TP, protection trigger,
   # 5% of original entry-to-TP trailing gap, and 60s no-new-best exit.
   ex0=np.searchsorted(tt,ts,side='right'); ex1=np.searchsorted(tt,ts+MAX_MINUTES*60,side='right')
   future_ticks=ticks[ex0:ex1]
   exitp=entry; reason='TIME'; activated=False; best=None; last_best_time=None
   trigger=entry+(tp-entry)*(PROTECTION_PCT/100.0)
   trail_gap=abs(tp-entry)*0.05
   for x in future_ticks:
    xt=int(x['time'])
    px=float(x['bid'] if sig.side==Side.BUY else x['ask'])
    if sig.side==Side.BUY:
     if px<=sl: exitp=sl; reason='SL' if not activated else 'PROTECT_SL'; break
     if px>=tp: exitp=tp; reason='TP'; break
     if not activated and px>=trigger:
      activated=True; best=px; last_best_time=xt; sl=max(sl,trigger)
     elif activated:
      if px>best:
       best=px; last_best_time=xt; sl=max(sl,trigger,best-trail_gap)
      elif xt-last_best_time>=60:
       exitp=px; reason='PROTECT_60S'; break
    else:
     if px>=sl: exitp=sl; reason='SL' if not activated else 'PROTECT_SL'; break
     if px<=tp: exitp=tp; reason='TP'; break
     if not activated and px<=trigger:
      activated=True; best=px; last_best_time=xt; sl=min(sl,trigger)
     elif activated:
      if px<best:
       best=px; last_best_time=xt; sl=min(sl,trigger,best+trail_gap)
      elif xt-last_best_time>=60:
       exitp=px; reason='PROTECT_60S'; break
   else:
    if len(future_ticks):
     x=future_ticks[-1]; exitp=float(x['bid'] if sig.side==Side.BUY else x['ask'])
   r=((exitp-entry)/(entry-sl) if sig.side==Side.BUY else (entry-exitp)/(sl-entry))
   rows.append((symbol,sig.strategy,sig.side.value,r,reason,sig.confidence*100))
   last_exit=ts+MAX_MINUTES*60
  print(f'[{symbol}] done | total qualifying trades so far: {len(rows)}', flush=True)
 mt5.shutdown()
 print(f'BACKTEST {a.days} days | risk={RISK_PCT}% | RR=1:{RR} | protection={PROTECTION_PCT}% | max={MAX_MINUTES}m | positions={MAX_POSITIONS} | confidence={MIN_CONFIDENCE}%')
 if not rows:
  print('No qualifying trades.'); return
 rs=np.array([x[3] for x in rows],float); wins=int((rs>0).sum()); losses=int((rs<0).sum())
 eq=100.0; peak=eq; dd=0.0
 for r in rs:
  eq*=1+(RISK_PCT/100.0)*r; peak=max(peak,eq); dd=max(dd,(peak-eq)/peak*100)
 gp=float(rs[rs>0].sum()); gl=abs(float(rs[rs<0].sum()))
 print(f'Trades: {len(rows)} | Wins: {wins} | Losses: {losses} | Win rate: {wins/len(rows)*100:.1f}%')
 print(f'Net: {rs.sum():+.2f}R | Profit factor: {(gp/gl if gl else float("inf")):.2f} | Max DD: {dd:.2f}% | Equity index: {eq:.2f}')
 for s in SYMBOLS:
  z=[x for x in rows if x[0]==s]
  if z: print(f'{s}: {len(z)} trades | {sum(x[3] for x in z):+.2f}R')
 print('By strategy:')
 for st in sorted(set(x[1] for x in rows)):
  z=[x for x in rows if x[1]==st]
  print(f'  {st}: {len(z)} | {sum(x[3] for x in z):+.2f}R')
 print('NOTE: protection/trailing/60s inactivity are simulated from ticks; portfolio concurrency is still not fully simulated.')
if __name__=='__main__':
 main()
