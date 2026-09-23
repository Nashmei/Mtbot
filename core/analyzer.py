import numpy as np
from .models import Regime, Side
from .strategies import (
 SCALP_TREND, GOLD_SCALP,
 scalp_breakout, ema_cross_scalp, scalp_trend, gold_scalp,
 m5_reversal_candidate, scalp_m5_reversal, scalp_reversion,
 scalp_sweep_reversal, scalp_squeeze_expansion,
)

# MT5Gateway.rates() starts at position 1, so -1 is the latest fully closed M5 bar.
_M5_REVERSAL_CLOSED_INDEX=-1
_M5_BREAKOUT_CLOSED_INDEX=-1
# XAUUSD intentionally moves its sideways window two bars forward with the -1 unification.
_M5_SIDEWAYS_OVERLAP_INDICES=range(-5,0)

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

 def analyze(self,ticks,point,rates=None,symbol=None,rates_m15=None,rates_h1=None,rates_m1=None):
  if ticks is None or len(ticks)<80 or point<=0:
   return Regime.NO_TRADE,None,{'decision':'insufficient_ticks'}

  is_gold=str(symbol or '').upper().startswith('XAUUSD')
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

  m15_bias=0
  if rates_m15 is not None and len(rates_m15)>=55:
   c15=np.asarray(rates_m15['close'],float); ema50_15=self._ema(c15[-55:],50)
   m15_bias=1 if c15[-1]>ema50_15 else (-1 if c15[-1]<ema50_15 else 0)

  h1_bias=0
  if rates_h1 is not None and len(rates_h1)>=55:
   c60=np.asarray(rates_h1['close'],float); ema50_60=self._ema(c60[-55:],50)
   h1_bias=1 if c60[-1]>ema50_60 else (-1 if c60[-1]<ema50_60 else 0)

  def htf_allows(side):
   want=1 if side==Side.BUY else -1
   return m15_bias==want and h1_bias in (0,want)

  cfg=GOLD_SCALP if is_gold else SCALP_TREND
  momentum_min=max(cfg['momentum_floor'],atrp*cfg['momentum_atr_factor'])
  acceleration_min=max(cfg['acceleration_floor'],atrp*cfg['acceleration_atr_factor'])
  live_up=micro_trend>0 and tick_momentum>0 and tick_momentum_fast>=-max(1.,atrp*.08)
  live_dn=micro_trend<0 and tick_momentum<0 and tick_momentum_fast<=max(1.,atrp*.08)
  context_up=(not have) or context_trend>=-atrp*.20
  context_dn=(not have) or context_trend<=atrp*.20
  bull=live_up and context_up; bear=live_dn and context_dn
  micro_gap=abs(micro_fast-micro_slow)/point
  local_mean=float(np.mean(mid[-30:])); local_std=max(float(np.std(mid[-30:])),point)
  micro_z=(live-local_mean)/local_std
  range_ok=(not have) or adx<25

  trend_strength=abs(context_trend)/max(atrp,1.)
  directional=((m15_bias>0 and context_trend>0) or (m15_bias<0 and context_trend<0))
  clear_trend=directional and (adx>=22 or trend_strength>=.16)
  clear_range=(have and adx<18 and trend_strength<.10 and tick_range<max(10.,atrp*.85))
  expansion=(tick_range>=max(7.,atrp*.65) and abs(tick_momentum)>=max(1.5,atrp*.08))
  market_mode='trend' if clear_trend else ('range' if clear_range else ('expansion' if expansion else 'mixed'))

  def strategy_allowed(name,side=None):
   if name in ('scalp_m5_reversal','scalp_reversion') and clear_trend:return False
   if name=='ema_cross_scalp' and clear_range:return False
   if name=='scalp_trend' and clear_range:return False
   return True

  reg=(Regime.TREND if market_mode=='trend' else Regime.RANGE if market_mode=='range' else Regime.VOLATILE if market_mode=='expansion' else Regime.RANGE)
  sig=None; decision='waiting_live_momentum'; blockers=[]
  trend_checks=trend_values=gold_checks=gold_values=None
  retest_level=None; ema_cross_tf=None; ema_cross_gap=0.0

  ctx={
   'is_gold':is_gold,'point':point,'rates':rates,'rates_m1':rates_m1,'have':have,
   'mid':mid,'live':live,'tick_momentum_fast':tick_momentum_fast,'tick_momentum':tick_momentum,
   'tick_range':tick_range,'micro_fast':micro_fast,'micro_trend':micro_trend,'micro_gap':micro_gap,
   'micro_z':micro_z,'atrp':atrp,'adx':adx,'context_trend':context_trend,
   'context_up':context_up,'context_dn':context_dn,'bull':bull,'bear':bear,'range_ok':range_ok,
   'm15_bias':m15_bias,'h1_bias':h1_bias,'momentum_min':momentum_min,'acceleration_min':acceleration_min,
   'ema':self._ema,'htf_allows':htf_allows,'strategy_allowed':strategy_allowed,
   'm5_reversal_closed_index':_M5_REVERSAL_CLOSED_INDEX,
   'm5_breakout_closed_index':_M5_BREAKOUT_CLOSED_INDEX,
   'm5_sideways_overlap_indices':_M5_SIDEWAYS_OVERLAP_INDICES,
  }

  # Build reversal diagnostics once; actual reversal remains fourth priority.
  reversal_side,reversal_reason,m5_doji,m5_sideways=m5_reversal_candidate(ctx)

  # Priority 1: breakout/retest.
  sig,reg_override,decision_override,retest_level=scalp_breakout(ctx)
  if reg_override is not None: reg=reg_override
  if decision_override is not None: decision=decision_override

  # Priority 2a: EMA cross (non-gold only).
  ema_candidate,ema_cross_tf,ema_cross_gap=ema_cross_scalp(ctx)
  if sig is None and ema_candidate is not None:
   sig=ema_candidate; reg=Regime.TREND; decision='ema_cross_scalp'

  # Priority 2b: trend continuation (non-gold only).
  trend_candidate,trend_checks,trend_values=scalp_trend(ctx)
  if sig is None and trend_candidate is not None:
   sig=trend_candidate; reg=Regime.TREND; decision='scalp_trend'
  elif sig is None and trend_checks is not None:
   reg=Regime.TREND; decision='trend_wait_pullback'

  # Priority 3: dedicated XAUUSD strategy.
  gold_candidate,gold_checks,gold_values=gold_scalp(ctx)
  if sig is None and gold_candidate is not None:
   sig=gold_candidate; reg=Regime.TREND; decision='gold_scalp'

  # Priority 4: M5 reversal.
  if sig is None:
   reversal_candidate=scalp_m5_reversal(ctx,reversal_side,reversal_reason)
   if reversal_candidate is not None:
    sig=reversal_candidate; reg=Regime.RANGE; decision='scalp_m5_reversal'

  # Priority 5: mean reversion.
  if sig is None:
   reversion_candidate=scalp_reversion(ctx)
   if reversion_candidate is not None:
    sig=reversion_candidate; reg=Regime.RANGE; decision='scalp_reversion'

  # Priority 6: failed-breakout / liquidity-sweep reversal.
  # New opportunity strategies run only after all existing strategies so
  # existing signal selection remains unchanged.
  if sig is None:
   sweep_candidate=scalp_sweep_reversal(ctx)
   if sweep_candidate is not None:
    sig=sweep_candidate; reg=Regime.BREAKOUT; decision='scalp_sweep_reversal'

  # Priority 7: volatility compression -> expansion.
  if sig is None:
   squeeze_candidate=scalp_squeeze_expansion(ctx)
   if squeeze_candidate is not None:
    sig=squeeze_candidate; reg=Regime.VOLATILE; decision='scalp_squeeze_expansion'

  if sig is None:
   if clear_trend:blockers.append('clear_trend')
   if not (live_up or live_dn):blockers.append('live_direction_missing')
   if abs(tick_momentum)<momentum_min:blockers.append('momentum_below_trend_threshold')
   if abs(tick_momentum_fast)<acceleration_min:blockers.append('acceleration_below_trend_threshold')
   if m15_bias and h1_bias and m15_bias!=h1_bias:blockers.append('m15_h1_conflict')
   if is_gold and m15_bias and h1_bias and m15_bias==h1_bias:
    want=1 if tick_momentum>0 else (-1 if tick_momentum<0 else 0)
    if want and want!=m15_bias:blockers.append('gold_live_vs_htf_conflict')
   if m5_doji and market_mode!='trend':
    reg=Regime.NO_TRADE; decision='m5_doji_no_trade'
   elif m5_sideways and market_mode!='trend':
    reg=Regime.NO_TRADE; decision='m5_sideways_no_trade'
   elif is_gold:
    reg=(Regime.TREND if market_mode=='trend' else Regime.VOLATILE if market_mode=='expansion' else Regime.RANGE)
    decision='gold_wait_confirmation'
   elif abs(micro_trend)>max(1.5,atrp*.10):
    reg=Regime.TREND; decision='waiting_momentum'
   elif tick_range>max(8.,atrp*.8):
    reg=Regime.VOLATILE; decision='volatile_no_direction'

  return reg,sig,{
   'decision':decision,'blockers':blockers,'trend_checks':trend_checks,'trend_values':trend_values,'gold_checks':gold_checks,'gold_values':gold_values,'market_mode':market_mode,'price':round(live,8),'spread_points':round(spread,1),
   'spread_ratio':round(spread_ratio,2),'tick_momentum_fast':round(float(tick_momentum_fast),2),
   'tick_momentum':round(float(tick_momentum),2),'micro_trend':round(float(micro_trend),2),
   'atr_points':round(float(atrp),2),'adx':round(float(adx),1),
   'di_plus':round(float(dp),1),'di_minus':round(float(dm),1),
   'context_trend':round(float(context_trend),2),'m15_bias':m15_bias,'h1_bias':h1_bias,'retest_level':retest_level,'ema_cross_tf':ema_cross_tf,'ema_cross_gap':round(float(ema_cross_gap),2),'micro_z':round(float(micro_z),2),'momentum_min':round(float(momentum_min),2),'acceleration_min':round(float(acceleration_min),2),'live':True
  }
