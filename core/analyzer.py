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

 def analyze(self,ticks,point,rates=None,symbol=None,rates_m15=None):
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

  # M15 higher-timeframe bias for trend continuation.
  m15_bias=0
  if rates_m15 is not None and len(rates_m15)>=55:
   c15=np.asarray(rates_m15['close'],float)
   ema50_15=self._ema(c15[-55:],50)
   m15_bias=1 if c15[-1]>ema50_15 else (-1 if c15[-1]<ema50_15 else 0)

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

  # Closed-candle M5 reversal scalp helpers. The just-closed candle must form
  # at nearby prior support/resistance; entry is evaluated on the new M5 bar.
  m5_reversal_side=None; m5_reversal_reason=''
  m5_doji=False; m5_sideways=False
  if have and len(rates)>=25:
   o=np.asarray(rates['open'],float); h=np.asarray(rates['high'],float)
   l=np.asarray(rates['low'],float); c=np.asarray(rates['close'],float)
   # MT5 rates may include the forming bar: use -2 as the confirmed closed bar.
   i=-2
   body=abs(c[i]-o[i]); candle_range=max(h[i]-l[i],point)
   upper=h[i]-max(o[i],c[i]); lower=min(o[i],c[i])-l[i]
   m5_doji=body<=candle_range*.12
   hammer=body>0 and lower>=body*2.0 and upper<=body*.8 and c[i]>=o[i]
   shooting=body>0 and upper>=body*2.0 and lower<=body*.8 and c[i]<=o[i]
   prev_body_hi=max(o[i-1],c[i-1]); prev_body_lo=min(o[i-1],c[i-1])
   bull_engulf=c[i]>o[i] and c[i-1]<o[i-1] and o[i]<=prev_body_lo and c[i]>=prev_body_hi
   bear_engulf=c[i]<o[i] and c[i-1]>o[i-1] and o[i]>=prev_body_hi and c[i]<=prev_body_lo
   prior_low=float(np.min(l[-22:-2])); prior_high=float(np.max(h[-22:-2]))
   sr_tol=max(atrp*.18*point,4*point)
   at_support=l[i]<=prior_low+sr_tol
   at_resistance=h[i]>=prior_high-sr_tol
   recent_ranges=h[-8:-2]-l[-8:-2]
   overlap=sum(1 for j in range(-7,-2) if h[j]>=l[j-1] and l[j]<=h[j-1])
   m5_sideways=(float(np.mean(recent_ranges))/max(atrp*point,point)<.55 and overlap>=4)
   if not m5_doji and not m5_sideways:
    if at_support and (hammer or bull_engulf):
     m5_reversal_side=Side.BUY
     m5_reversal_reason='M5 support + '+('hammer' if hammer else 'bullish engulfing')
    elif at_resistance and (shooting or bear_engulf):
     m5_reversal_side=Side.SELL
     m5_reversal_reason='M5 resistance + '+('shooting star' if shooting else 'bearish engulfing')

  # Breakout is the first-priority setup for every symbol. Require live
  # micro-direction confirmation so a one-tick poke is less likely to trigger.
  breakout_up=break_up and context_up and micro_trend>0 and tick_momentum>0
  breakout_dn=break_dn and context_dn and micro_trend<0 and tick_momentum<0
  if breakout_up or breakout_dn:
   side=Side.BUY if breakout_up else Side.SELL
   reg=Regime.BREAKOUT
   score=74+min(12,abs(tick_momentum)/max(atrp,1)*18)+min(6,micro_gap/max(atrp,1)*10)
   slp=max(10.,min(1.7*atrp,max(.65*atrp,tick_range*.30)))
   sig=Signal(side,'scalp_breakout',min(.92,score/100.),slp,'priority live structure breakout + confirmed micro momentum')
   decision='scalp_breakout'

  # Second priority: trend continuation, but only with M15 bias and an M5
  # pullback/retest instead of chasing an already extended impulse.
  if sig is None and (bull or bear) and have:
   side=Side.BUY if bull else Side.SELL
   strong=micro_gap>=max(1.25,atrp*.07) and abs(tick_momentum)>=max(1.25,atrp*.07)
   acceleration=((side==Side.BUY and tick_momentum_fast>=max(1.0,atrp*.035)) or
                 (side==Side.SELL and tick_momentum_fast<=-max(1.0,atrp*.035)))
   c5=np.asarray(rates['close'],float); h5=np.asarray(rates['high'],float); l5=np.asarray(rates['low'],float)
   ema20_5=self._ema(c5[-30:],20)
   pull_tol=max(atrp*.22*point,4*point)
   pullback=((side==Side.BUY and l5[-1]<=ema20_5+pull_tol and live>ema20_5) or
             (side==Side.SELL and h5[-1]>=ema20_5-pull_tol and live<ema20_5))
   htf_ok=((side==Side.BUY and m15_bias>0) or (side==Side.SELL and m15_bias<0))
   if strong and acceleration and pullback and htf_ok:
    reg=Regime.TREND
    score=70+min(12,abs(micro_trend)/max(atrp,1)*20)+min(10,abs(tick_momentum)/max(atrp,1)*15)
    slp=max(10.,min(1.8*atrp,max(.70*atrp,tick_range*.33)))
    sig=Signal(side,'scalp_trend',min(.92,score/100.),slp,'M15 bias + M5 EMA20 pullback + live acceleration')
    decision='scalp_trend'
   else:
    reg=Regime.TREND; decision='trend_wait_pullback'

  # Third priority: dedicated gold expansion setup.
  is_gold=str(symbol or '').upper().startswith('XAUUSD')
  if sig is None and is_gold:
   gold_up=micro_trend>0 and tick_momentum>0 and tick_momentum_fast>0 and context_up
   gold_dn=micro_trend<0 and tick_momentum<0 and tick_momentum_fast<0 and context_dn
   gold_gap=micro_gap>=max(1.5,atrp*.07)
   gold_momentum=abs(tick_momentum)>=max(1.5,atrp*.07)
   gold_fast=abs(tick_momentum_fast)>=max(1.0,atrp*.035)
   gold_expand=(gold_up or gold_dn) and gold_gap and gold_momentum and gold_fast and tick_range>=max(5.0,atrp*.35)
   if gold_expand:
    side=Side.BUY if gold_up else Side.SELL
    reg=Regime.TREND
    score=72+min(12,abs(tick_momentum)/max(atrp,1)*18)+min(8,micro_gap/max(atrp,1)*12)
    slp=max(12.,min(1.7*atrp,max(.70*atrp,tick_range*.32)))
    sig=Signal(side,'gold_scalp',min(.92,score/100.),slp,'XAUUSD confirmed live expansion; breakout remains first priority')
    decision='gold_scalp'

  # Fourth priority: M5 reversal only in a ranging/non-trending context.
  strong_trend=(have and adx>=25) or abs(context_trend)>max(2.0,atrp*.18)
  if sig is None and m5_reversal_side is not None and range_ok and not strong_trend:
   side=m5_reversal_side
   reg=Regime.RANGE
   score=76
   slp=max(10.,min(1.6*atrp,max(.65*atrp,tick_range*.30)))
   sig=Signal(side,'scalp_m5_reversal',score/100.,slp,m5_reversal_reason+'; range-only reversal on new candle')
   decision='scalp_m5_reversal'

  # Mean reversion remains last priority and needs a clearer extreme/reversal.
  if sig is None and not (bull or bear) and range_ok and abs(micro_z)>=1.80 and abs(tick_momentum_fast)>=1.25:
   side=Side.SELL if micro_z>0 and tick_momentum_fast<0 else (Side.BUY if micro_z<0 and tick_momentum_fast>0 else None)
   if side:
    reg=Regime.RANGE
    score=69+min(13,(abs(micro_z)-1.80)*13)
    slp=max(10.,min(1.5*atrp,max(.65*atrp,tick_range*.28)))
    sig=Signal(side,'scalp_reversion',min(.88,score/100.),slp,'strong short-term extreme + confirmed live reversal')
    decision='scalp_reversion'

  if sig is None:
   if m5_doji:
    reg=Regime.NO_TRADE; decision='m5_doji_no_trade'
   elif m5_sideways:
    reg=Regime.NO_TRADE; decision='m5_sideways_no_trade'
   elif is_gold:
    reg=Regime.VOLATILE if tick_range>max(8.,atrp*.8) else (Regime.TREND if abs(micro_trend)>max(1.5,atrp*.10) else Regime.RANGE)
    decision='gold_wait_confirmation'
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
   'context_trend':round(float(context_trend),2),'m15_bias':m15_bias,'micro_z':round(float(micro_z),2),'live':True
  }
