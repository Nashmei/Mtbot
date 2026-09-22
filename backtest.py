import argparse
from datetime import datetime, timezone
import MetaTrader5 as mt5
import numpy as np
from core.analyzer import Analyzer
from core.models import Side

SYMBOLS=['EURUSD','XAUUSD','NZDUSD','GBPUSD','AUDUSD','USDJPY']
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
  # Sample once per minute; feed only information available at that moment.
  last_exit=0
  for bar in m1[60:]:
   ts=int(bar['time'])
   if ts<last_exit: continue
   tk=ticks[(ticks['time']<=ts)&(ticks['time']>=ts-1800)]
   r5=m5[m5['time']<ts][-200:]; r15=m15[m15['time']<ts][-200:]
   if len(tk)<80 or len(r5)<60: continue
   reg,sig,meta=an.analyze(tk,info.point,r5,symbol=symbol,rates_m15=r15)
   if not sig or sig.confidence*100<MIN_CONFIDENCE: continue
   entry=float(tk['ask'][-1] if sig.side==Side.BUY else tk['bid'][-1])
   spread=float(tk['ask'][-1]-tk['bid'][-1])
   d=max(float(sig.sl_points),10.0)*info.point+spread
   sl=entry-d if sig.side==Side.BUY else entry+d
   tp=entry+abs(entry-sl)*RR if sig.side==Side.BUY else entry-abs(entry-sl)*RR
   future=m1[(m1['time']>ts)&(m1['time']<=ts+MAX_MINUTES*60)]
   exitp=float(future[-1]['close']) if len(future) else entry
   reason='TIME'
   for b in future:
    hi=float(b['high']); lo=float(b['low'])
    if sig.side==Side.BUY:
     if lo<=sl: exitp=sl; reason='SL'; break
     if hi>=tp: exitp=tp; reason='TP'; break
    else:
     if hi>=sl: exitp=sl; reason='SL'; break
     if lo<=tp: exitp=tp; reason='TP'; break
   r=((exitp-entry)/(entry-sl) if sig.side==Side.BUY else (entry-exitp)/(sl-entry))
   rows.append((symbol,sig.strategy,sig.side.value,r,reason,sig.confidence*100))
   last_exit=ts+MAX_MINUTES*60
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
 print('NOTE: conservative historical approximation; current live protection/trailing and portfolio concurrency are not fully simulated.')
if __name__=='__main__':
 main()
