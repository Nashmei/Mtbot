import time
import numpy as np
from .models import Regime, Signal, Side

class Analyzer:
 def _ema(self,a,n):
  a=np.asarray(a,dtype=float)
  if len(a)==0:return 0.0
  k=2.0/(n+1.0); v=float(a[0])
  for x in a[1:]: v=k*float(x)+(1-k)*v
  return v

 def _atr(self,h,l,c,n=14):
  if len(c)<n+1:return max(float(np.std(np.diff(c))),1e-12)
  prev=np.r_[c[0],c[:-1]]
  tr=np.maximum(h-l,np.maximum(np.abs(h-prev),np.abs(l-prev)))
  return max(float(np.mean(tr[-n:])),1e-12)

 def _dmi(self,h,l,c,n=14):
  if len(c)<n+2:return 0.,0.,0.
  up=np.diff(h); dn=-np.diff(l)
  plus=np.where((up>dn)&(up>0),up,0.); minus=np.where((dn>up)&(dn>0),dn,0.)
  prev=c[:-1]; tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-prev),np.abs(l[1:]-prev)))
  atr=max(float(np.mean(tr[-n:])),1e-12)
  p=100*float(np.mean(plus[-n:]))/atr; m=100*float(np.mean(minus[-n:]))/atr
  dx=[]
  for i in range(n,len(tr)+1):
   aa=max(float(np.mean(tr[i-n:i])),1e-12)
   pp=100*float(np.mean(plus[i-n:i]))/aa; mm=100*float(np.mean(minus[i-n:i]))/aa
   dx.append(100*abs(pp-mm)/max(pp+mm,1e-12))
  return (float(np.mean(dx[-n:])) if dx else 0.),p,m

 def analyze(self,ticks,point,rates=None):
  if ticks is None or len(ticks)<80 or point<=0:
   return Regime.NO_TRADE,None,{'decision':'insufficient_ticks'}

  bid=np.asarray(ticks['bid'],dtype=float); ask=np.asarray(ticks['ask'],dtype=float)
  mid=(bid+ask)/2.; spreads=(ask-bid)/point
  good=spreads[np.isfinite(spreads)&(spreads>0)]
  spread=float(good[-1]) if len(good) else 0.
  normal=float(np.median(good[-80:])) if len(good) else 0.
  spread_ratio=spread/normal if normal>0 else 1.
  live=float(mid[-1])
  tick_momentum_fast=(live-float(mid[-6]))/point
  tick_momentum=(live-float(mid[-21]))/point
  tick_range=(float(np.max(mid[-40:]))-float(np.min(mid[-40:])))/point
  micro_fast=self._ema(mid[-30:],6); micro_slow=self._ema(mid[-60:],18)
  micro_trend=(micro_fast-micro_slow)/point

  if spread>0 and spread_ratio>1.8:
   return Regime.NO_TRADE,None,{'decision':'spread_spike','price':live,'spread_points':round(spread,1),'spread_ratio':round(spread_ratio,2)}

  have=rates is not None and len(rates)>=60
  atrp=max(float(np.std(np.diff(mid[-60:]))/point),1.)
  adx=dp=dm=0.; context_trend=0.; structure_hi=float(np.max(mid[-60:-3])); structure_lo=float(np.min(mid[-60:-3]))
  if have:
   h=np.asarray(rates['high'],float); l=np.asarray(rates['low'],float); c=np.asarray(rates['close'],float)
   atr=self._atr(h,l,c); atrp=max(atr/point,1.)
   fast=self._ema(c[-50:],12); slow=self._ema(c[-60:],26); context_trend=(fast-slow)/point
   adx,dp,dm=self._dmi(h,l,c)
   structure_hi=float(np.max(h[-20:])); structure_lo=float(np.min(l[-20:]))

  # Closed M5 is context only. Entry direction is driven by live MT5 ticks.
  live_up=micro_trend>0 and tick_momentum>0 and tick_momentum_fast>=-max(1.,atrp*.08)
  live_dn=micro_trend<0 and tick_momentum<0 and tick_momentum_fast<=max(1.,atrp*.08)
  context_up=(not have) or context_trend>=-atrp*.20
  context_dn=(not have) or context_trend<=atrp*.20
  # Scalping entry stays live-first: M5 is context only; ADX/DI never blocks an entry.
  bull=live_up and context_up
  bear=live_dn and context_dn
  break_up=live>structure_hi and tick_momentum_fast>0
  break_dn=live<structure_lo and tick_momentum_fast<0

  # Lightweight TradingView-inspired scalp setups:
  # EMA-style continuation, momentum breakout, and fast mean reversion.
  micro_gap=abs(micro_fast-micro_slow)/point
  local_mean=float(np.mean(mid[-30:]))
  local_std=max(float(np.std(mid[-30:])),point)
  micro_z=(live-local_mean)/local_std
  range_ok=(not have) or adx<25

  sig=None; reg=Regime.RANGE; decision='waiting_live_momentum'
  # 1) Fast structure breakout: designed for immediate expansion, not long holds.
  if (break_up and context_up) or (break_dn and context_dn):
   side=Side.BUY if break_up else Side.SELL
   reg=Regime.BREAKOUT
   score=72+min(14,abs(tick_momentum)/max(atrp,1)*20)
   slp=max(10.,min(1.8*atrp,max(.60*atrp,tick_range*.30)))
   sig=Signal(side,'scalp_breakout',min(.92,score/100.),slp,'live structure break + tick momentum')
   decision='scalp_breakout'

  # 2) EMA/micro-trend continuation: simple trend-following scalp.
  elif bull or bear:
   side=Side.BUY if bull else Side.SELL
   strong=micro_gap>=max(1.0,atrp*.05) and abs(tick_momentum)>=max(1.0,atrp*.05)
   acceleration=(side==Side.BUY and tick_momentum_fast>0) or (side==Side.SELL and tick_momentum_fast<0)
   if strong or acceleration:
    strategy='scalp_trend'
    reg=Regime.BREAKOUT if breakout else Regime.TREND
    score=65+min(15,abs(micro_trend)/max(atrp,1)*24)+min(12,abs(tick_momentum)/max(atrp,1)*18)
    slp=max(10.,min(2.0*atrp,max(.65*atrp,tick_range*.35)))
    sig=Signal(side,strategy,min(.92,score/100.),slp,'EMA-style micro trend + live momentum; M5 context only')
    decision=strategy
   else:
    reg=Regime.TREND; decision='trend_wait_acceleration'
  # 3) Fast range mean reversion: only near a clear short-term statistical extreme.
  elif range_ok and abs(micro_z)>=1.65 and abs(tick_momentum_fast)>=1.0:
   side=Side.SELL if micro_z>0 and tick_momentum_fast<0 else (Side.BUY if micro_z<0 and tick_momentum_fast>0 else None)
   if side:
    reg=Regime.RANGE
    score=67+min(14,(abs(micro_z)-1.65)*14)
    slp=max(10.,min(1.6*atrp,max(.60*atrp,tick_range*.28)))
    sig=Signal(side,'scalp_reversion',min(.88,score/100.),slp,'short-term extreme + live reversal')
    decision='scalp_reversion'
  elif abs(micro_trend)>max(1.5,atrp*.10):
   reg=Regime.TREND; decision='waiting_momentum'
  elif tick_range>max(8.,atrp*.8):
   reg=Regime.VOLATILE; decision='volatile_no_direction'

  return reg,sig,{
   'decision':decision,'price':round(live,8),'spread_points':round(spread,1),
   'spread_ratio':round(spread_ratio,2),'tick_momentum_fast':round(float(tick_momentum_fast),2),
   'tick_momentum':round(float(tick_momentum),2),'micro_trend':round(float(micro_trend),2),
   'atr_points':round(float(atrp),2),'adx':round(float(adx),1),
   'di_plus':round(float(dp),1),'di_minus':round(float(dm),1),
   'context_trend':round(float(context_trend),2),'micro_z':round(float(micro_z),2),'live':True
  }
