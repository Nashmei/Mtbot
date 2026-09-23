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

  # M15 higher-timeframe bias for trend continuation.
  m15_bias=0
  if rates_m15 is not None and len(rates_m15)>=55:
   c15=np.asarray(rates_m15['close'],float)
   ema50_15=self._ema(c15[-55:],50)
   m15_bias=1 if c15[-1]>ema50_15 else (-1 if c15[-1]<ema50_15 else 0)

  # H1 is an additional higher-timeframe guard. M15 must agree with the
  # entry direction, and H1 must not be clearly opposite.
  h1_bias=0
  if rates_h1 is not None and len(rates_h1)>=55:
   c60=np.asarray(rates_h1['close'],float)
   ema50_60=self._ema(c60[-55:],50)
   h1_bias=1 if c60[-1]>ema50_60 else (-1 if c60[-1]<ema50_60 else 0)

  def htf_allows(side):
   want=1 if side==Side.BUY else -1
   return m15_bias==want and h1_bias in (0,want)

  # Closed M5 is context only. Entry direction is driven by live MT5 ticks.
  # Opportunity thresholds are ATR-adaptive with symbol-specific floors.
  # Keep HTF, pullback, spread, risk, and direction guards unchanged.
  if is_gold:
   momentum_min=max(8.0,atrp*.03)
   acceleration_min=max(5.0,atrp*.015)
  else:
   momentum_min=max(.8,atrp*.03)
   acceleration_min=max(.4,atrp*.015)
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

  # Flexible per-symbol market regime router. This does not require one
  # strategy for all symbols: every symbol is classified independently each scan.
  # It only blocks clearly unsuitable setups; ambiguous conditions stay permissive
  # so opportunity count is not unnecessarily reduced.
  trend_strength=abs(context_trend)/max(atrp,1.)
  directional=((m15_bias>0 and context_trend>0) or (m15_bias<0 and context_trend<0))
  clear_trend=directional and (adx>=22 or trend_strength>=.16)
  clear_range=(have and adx<18 and trend_strength<.10 and tick_range<max(10.,atrp*.85))
  expansion=(tick_range>=max(7.,atrp*.65) and abs(tick_momentum)>=max(1.5,atrp*.08))
  market_mode='trend' if clear_trend else ('range' if clear_range else ('expansion' if expansion else 'mixed'))

  def strategy_allowed(name,side=None):
   # Reversal/reversion are unsuitable in a clearly directional trend.
   if name in ('scalp_m5_reversal','scalp_reversion') and clear_trend:
    return False
   # EMA crosses are useful frequently, but avoid clear range/chop where
   # repeated 9/21 crosses are most likely to whipsaw.
   if name=='ema_cross_scalp' and clear_range:
    return False
   # Trend continuation needs at least non-range conditions.
   if name=='scalp_trend' and clear_range:
    return False
   # Breakout/retest and gold expansion remain available in mixed/expansion
   # markets; their own entry rules still decide the actual signal.
   return True

  # Keep the reported regime aligned with the router even while no signal exists.
  # This is diagnostic/display state only; it does not change entry eligibility.
  reg=(Regime.TREND if market_mode=='trend' else
       Regime.RANGE if market_mode=='range' else
       Regime.VOLATILE if market_mode=='expansion' else Regime.RANGE)
  sig=None; decision='waiting_live_momentum'
  blockers=[]
  trend_checks=None
  trend_values=None
  gold_checks=None
  gold_values=None

  # Frequent EMA crossover scalp. Use CLOSED candles only so a forming candle
  # cannot create/disappear a crossover. M1 is primary; M5 is fallback.
  ema_cross_side=None; ema_cross_tf=None; ema_cross_gap=0.0
  cross_rates=rates_m1 if rates_m1 is not None and len(rates_m1)>=30 else rates
  if cross_rates is not None and len(cross_rates)>=(30 if is_gold else 31):
   cc=np.asarray(cross_rates['close'],float)
   # rates() already returns closed candles; compare the last two closed bars.
   ema9_now=self._ema(cc[-24:],9); ema21_now=self._ema(cc[-30:],21)
   ema9_prev=self._ema(cc[-25:-1],9)
   ema21_prev=self._ema(cc[-30:-1] if is_gold else cc[-31:-1],21)
   cross_up=ema9_prev<=ema21_prev and ema9_now>ema21_now
   cross_dn=ema9_prev>=ema21_prev and ema9_now<ema21_now
   ema_cross_gap=abs(ema9_now-ema21_now)/point
   ema_cross_tf='M1' if cross_rates is rates_m1 else 'M5'
   # M15 blocks obvious counter-trend crosses, but H1 is not required here
   # so this setup can add opportunities instead of becoming too restrictive.
   # Confirm the cross is still supported by current price action. This
   # avoids entering after the crossover has already gone stale/reversed.
   recent_move=(live-float(mid[-10]))/point
   buy_live_ok=tick_momentum_fast>0 and recent_move>0 and live>=micro_fast
   sell_live_ok=tick_momentum_fast<0 and recent_move<0 and live<=micro_fast
   if cross_up and m15_bias>=0 and buy_live_ok:
    ema_cross_side=Side.BUY
   elif cross_dn and m15_bias<=0 and sell_live_ok:
    ema_cross_side=Side.SELL

  # Closed-candle M5 reversal scalp helpers. The just-closed candle must form
  # at nearby prior support/resistance; entry is evaluated on the new M5 bar.
  m5_reversal_side=None; m5_reversal_reason=''
  m5_doji=False; m5_sideways=False
  if have and len(rates)>=25:
   o=np.asarray(rates['open'],float); h=np.asarray(rates['high'],float)
   l=np.asarray(rates['low'],float); c=np.asarray(rates['close'],float)
   # Gateway starts at bar position 1, so -1 is the latest closed bar.
   # Retain the existing gold candle selection unchanged.
   i=-2 if is_gold else -1
   body=abs(c[i]-o[i]); candle_range=max(h[i]-l[i],point)
   upper=h[i]-max(o[i],c[i]); lower=min(o[i],c[i])-l[i]
   m5_doji=body<=candle_range*.12
   hammer=body>0 and lower>=body*2.0 and upper<=body*.8 and c[i]>=o[i]
   shooting=body>0 and upper>=body*2.0 and lower<=body*.8 and c[i]<=o[i]
   prev_body_hi=max(o[i-1],c[i-1]); prev_body_lo=min(o[i-1],c[i-1])
   bull_engulf=c[i]>o[i] and c[i-1]<o[i-1] and o[i]<=prev_body_lo and c[i]>=prev_body_hi
   bear_engulf=c[i]<o[i] and c[i-1]>o[i-1] and o[i]>=prev_body_hi and c[i]<=prev_body_lo
   prior_end=-2 if is_gold else -1
   prior_low=float(np.min(l[-22:prior_end])); prior_high=float(np.max(h[-22:prior_end]))
   sr_tol=max(atrp*.18*point,4*point)
   at_support=l[i]<=prior_low+sr_tol
   at_resistance=h[i]>=prior_high-sr_tol
   recent_ranges=h[-8:prior_end]-l[-8:prior_end]
   overlap=sum(1 for j in (range(-7,-2) if is_gold else range(-5,0)) if h[j]>=l[j-1] and l[j]<=h[j-1])
   m5_sideways=(float(np.mean(recent_ranges))/max(atrp*point,point)<.55 and overlap>=4)
   if not m5_doji and not m5_sideways:
    if at_support and (hammer or bull_engulf):
     m5_reversal_side=Side.BUY
     m5_reversal_reason='M5 support + '+('hammer' if hammer else 'bullish engulfing')
    elif at_resistance and (shooting or bear_engulf):
     m5_reversal_side=Side.SELL
     m5_reversal_reason='M5 resistance + '+('shooting star' if shooting else 'bearish engulfing')

  # Breakout entry requires a real retest instead of chasing the impulse.
  # The previous CLOSED M5 candle must have broken prior structure, then live
  # price must return to the broken level and resume in the breakout direction.
  breakout_up=breakout_dn=False
  retest_level=None
  if have and len(rates)>=25:
   o5=np.asarray(rates['open'],float); h5=np.asarray(rates['high'],float)
   l5=np.asarray(rates['low'],float); c5=np.asarray(rates['close'],float)
   closed_index=-2 if is_gold else -1
   prior_hi=float(np.max(h5[-22:closed_index])); prior_lo=float(np.min(l5[-22:closed_index]))
   closed_hi=float(h5[closed_index]); closed_lo=float(l5[closed_index]); closed_close=float(c5[closed_index])
   retest_tol=max(3.0*point,atrp*.15*point)
   broke_up=closed_hi>prior_hi and closed_close>prior_hi
   broke_dn=closed_lo<prior_lo and closed_close<prior_lo
   retest_up=(live>=prior_hi-retest_tol and live<=prior_hi+retest_tol and tick_momentum_fast>0 and micro_trend>0)
   retest_dn=(live<=prior_lo+retest_tol and live>=prior_lo-retest_tol and tick_momentum_fast<0 and micro_trend<0)
   breakout_up=broke_up and retest_up and context_up and htf_allows(Side.BUY)
   breakout_dn=broke_dn and retest_dn and context_dn and htf_allows(Side.SELL)
   if broke_up or broke_dn:
    retest_level=prior_hi if broke_up else prior_lo
    if not (breakout_up or breakout_dn):
     reg=Regime.BREAKOUT
     decision='breakout_wait_retest_or_htf'

  if breakout_up or breakout_dn:
   side=Side.BUY if breakout_up else Side.SELL
   reg=Regime.BREAKOUT
   score=76+min(10,abs(tick_momentum)/max(atrp,1)*14)+min(6,micro_gap/max(atrp,1)*9)
   slp=max(10.,min(1.7*atrp,max(.65*atrp,tick_range*.30)))
   sig=Signal(side,'scalp_breakout',min(.92,score/100.),slp,'M5 breakout + direct retest + M15/H1 trend confirmation')
   decision='scalp_breakout_retest'

  # Frequent EMA 9/21 crossover setup. XAUUSD is reserved for its dedicated gold_scalp logic.
  if sig is None and not is_gold and ema_cross_side is not None and strategy_allowed('ema_cross_scalp',ema_cross_side):
   side=ema_cross_side
   reg=Regime.TREND
   score=72+min(10,ema_cross_gap/max(atrp*.05,1.)*4)+min(8,abs(tick_momentum_fast)/max(atrp,1)*12)
   slp=max(10.,min(1.6*atrp,max(.60*atrp,tick_range*.28)))
   sig=Signal(side,'ema_cross_scalp',min(.88,score/100.),slp,f'EMA 9/21 {ema_cross_tf} closed-candle cross + live momentum + M15 guard')
   decision='ema_cross_scalp'

  # Second priority: trend continuation, but only with M15 bias and an M5
  # pullback/retest instead of chasing an already extended impulse.
  if sig is None and (bull or bear) and have and strategy_allowed('scalp_trend'):
   side=Side.BUY if bull else Side.SELL
   gap_ok=micro_gap>=momentum_min
   momentum_ok=abs(tick_momentum)>=momentum_min
   acceleration=((side==Side.BUY and tick_momentum_fast>=acceleration_min) or
                 (side==Side.SELL and tick_momentum_fast<=-acceleration_min))
   c5=np.asarray(rates['close'],float); h5=np.asarray(rates['high'],float); l5=np.asarray(rates['low'],float)
   ema20_5=self._ema(c5[-30:],20)
   pull_tol=max(atrp*.22*point,4*point)
   pullback=((side==Side.BUY and l5[-1]<=ema20_5+pull_tol and live>ema20_5) or
             (side==Side.SELL and h5[-1]>=ema20_5-pull_tol and live<ema20_5))
   htf_ok=htf_allows(side)
   confirmations={'gap':bool(gap_ok),'momentum':bool(momentum_ok),'acceleration':bool(acceleration),'pullback':bool(pullback)}
   confirmation_score=sum(confirmations.values())
   trend_checks={**confirmations,'htf':bool(htf_ok)}
   pullback_distance=((live-ema20_5)/point if side==Side.BUY else (ema20_5-live)/point)
   trend_values={'side':side.value,'confirmation_score':confirmation_score,'confirmation_required':3,'micro_gap':round(float(micro_gap),2),'momentum':round(float(tick_momentum),2),'momentum_abs':round(abs(float(tick_momentum)),2),'momentum_min':round(float(momentum_min),2),'acceleration':round(float(tick_momentum_fast),2),'acceleration_abs':round(abs(float(tick_momentum_fast)),2),'acceleration_min':round(float(acceleration_min),2),'ema20_m5':round(float(ema20_5),8),'live':round(float(live),8),'pullback_distance_points':round(float(pullback_distance),2),'pullback_tolerance_points':round(float(pull_tol/point),2),'m15_bias':m15_bias,'h1_bias':h1_bias}
   # Direction + HTF are hard guards. The four short-term confirmations are
   # evidence: require any 3/4 instead of making every correlated measure fatal.
   if htf_ok and confirmation_score>=3:
    reg=Regime.TREND
    score=70+min(12,abs(micro_trend)/max(atrp,1)*20)+min(10,abs(tick_momentum)/max(atrp,1)*15)
    slp=max(10.,min(1.8*atrp,max(.70*atrp,tick_range*.33)))
    sig=Signal(side,'scalp_trend',min(.92,score/100.),slp,f'Trend confirmation {confirmation_score}/4 + M15/H1 direction guard')
    decision='scalp_trend'
   else:
    reg=Regime.TREND; decision='trend_wait_pullback'

  # Third priority: dedicated gold expansion setup.
  if sig is None and is_gold:
   gold_up=micro_trend>0 and tick_momentum>0 and tick_momentum_fast>0 and context_up
   gold_dn=micro_trend<0 and tick_momentum<0 and tick_momentum_fast<0 and context_dn
   gold_gap=micro_gap>=momentum_min
   gold_momentum=abs(tick_momentum)>=momentum_min
   gold_fast=abs(tick_momentum_fast)>=acceleration_min
   gold_side=Side.BUY if gold_up else (Side.SELL if gold_dn else None)
   gold_range_min=max(5.0,atrp*.35)
   gold_range=tick_range>=gold_range_min
   gold_range_floor=tick_range>=gold_range_min*.60
   gold_htf=gold_side is not None and htf_allows(gold_side)
   gold_confirmations={'gap':bool(gold_gap),'momentum':bool(gold_momentum),'acceleration':bool(gold_fast),'expansion_range':bool(gold_range)}
   gold_confirmation_score=sum(gold_confirmations.values())
   gold_checks={'direction':bool(gold_up or gold_dn),**gold_confirmations,'htf':bool(gold_htf),'range_floor':bool(gold_range_floor)}
   gold_values={'side':gold_side.value if gold_side is not None else 'NONE','confirmation_score':gold_confirmation_score,'confirmation_required':3,'micro_gap':round(float(micro_gap),2),'gap_min':round(float(momentum_min),2),'momentum':round(float(tick_momentum),2),'momentum_abs':round(abs(float(tick_momentum)),2),'momentum_min':round(float(momentum_min),2),'acceleration':round(float(tick_momentum_fast),2),'acceleration_abs':round(abs(float(tick_momentum_fast)),2),'acceleration_min':round(float(acceleration_min),2),'tick_range':round(float(tick_range),2),'expansion_range_min':round(float(gold_range_min),2),'expansion_range_floor':round(float(gold_range_min*.60),2),'micro_trend':round(float(micro_trend),2),'m15_bias':m15_bias,'h1_bias':h1_bias}
   # Gold keeps direction + HTF as hard guards. Require 3/4 confirmations and
   # a minimum 60% expansion floor so three correlated momentum checks cannot
   # justify chasing a market with materially insufficient range.
   gold_expand=(gold_up or gold_dn) and gold_side is not None and gold_htf and gold_range_floor and gold_confirmation_score>=3
   if gold_expand:
    side=Side.BUY if gold_up else Side.SELL
    reg=Regime.TREND
    score=72+min(12,abs(tick_momentum)/max(atrp,1)*18)+min(8,micro_gap/max(atrp,1)*12)
    slp=max(12.,min(1.7*atrp,max(.70*atrp,tick_range*.32)))
    sig=Signal(side,'gold_scalp',min(.92,score/100.),slp,f'XAUUSD confirmation {gold_confirmation_score}/4 + direction/HTF/range guards')
    decision='gold_scalp'

  # Fourth priority: M5 reversal only in a ranging/non-trending context.
  strong_trend=(have and adx>=25) or abs(context_trend)>max(2.0,atrp*.18)
  if sig is None and m5_reversal_side is not None and range_ok and not strong_trend and strategy_allowed('scalp_m5_reversal',m5_reversal_side):
   side=m5_reversal_side
   reg=Regime.RANGE
   score=76
   slp=max(10.,min(1.6*atrp,max(.65*atrp,tick_range*.30)))
   sig=Signal(side,'scalp_m5_reversal',score/100.,slp,m5_reversal_reason+'; range-only reversal on new candle')
   decision='scalp_m5_reversal'

  # Mean reversion remains last priority and needs a clearer extreme/reversal.
  if sig is None and not (bull or bear) and range_ok and abs(micro_z)>=1.80 and abs(tick_momentum_fast)>=1.25 and strategy_allowed('scalp_reversion'):
   side=Side.SELL if micro_z>0 and tick_momentum_fast<0 else (Side.BUY if micro_z<0 and tick_momentum_fast>0 else None)
   if side:
    reg=Regime.RANGE
    score=69+min(13,(abs(micro_z)-1.80)*13)
    slp=max(10.,min(1.5*atrp,max(.65*atrp,tick_range*.28)))
    sig=Signal(side,'scalp_reversion',min(.88,score/100.),slp,'strong short-term extreme + confirmed live reversal')
    decision='scalp_reversion'

  if sig is None:
   # Explain *why* the current scan did not become a trade. These flags are
   # observational only and intentionally do not participate in entry logic.
   if clear_trend:
    blockers.append('clear_trend')
   if not (live_up or live_dn):
    blockers.append('live_direction_missing')
   if abs(tick_momentum)<momentum_min:
    blockers.append('momentum_below_trend_threshold')
   if abs(tick_momentum_fast)<acceleration_min:
    blockers.append('acceleration_below_trend_threshold')
   if m15_bias and h1_bias and m15_bias!=h1_bias:
    blockers.append('m15_h1_conflict')
   if is_gold and m15_bias and h1_bias and m15_bias==h1_bias:
    want=1 if tick_momentum>0 else (-1 if tick_momentum<0 else 0)
    if want and want!=m15_bias:
     blockers.append('gold_live_vs_htf_conflict')
   # A doji alone must not erase an otherwise valid trend regime. It still
   # suppresses the dedicated reversal pattern above and remains diagnostic.
   if m5_doji and market_mode!='trend':
    reg=Regime.NO_TRADE; decision='m5_doji_no_trade'
   elif m5_sideways and market_mode!='trend':
    reg=Regime.NO_TRADE; decision='m5_sideways_no_trade'
   elif is_gold:
    # Report the router regime consistently; waiting for gold confirmation
    # must not relabel a clear HTF trend as RANGE.
    reg=(Regime.TREND if market_mode=='trend' else
         Regime.VOLATILE if market_mode=='expansion' else Regime.RANGE)
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
