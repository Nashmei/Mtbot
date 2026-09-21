import numpy as np
from .models import Regime,Signal,Side

class Analyzer:
 def _ema(self,a,n):
  a=np.asarray(a,dtype=float)
  alpha=2.0/(n+1.0)
  out=float(a[0])
  for v in a[1:]: out=alpha*float(v)+(1-alpha)*out
  return out

 def _alma_series(self,a,window=34,offset=.85,sigma=6.0):
  a=np.asarray(a,dtype=float)
  if len(a)<window:return np.array([])
  m=offset*(window-1); s=window/sigma
  w=np.exp(-((np.arange(window)-m)**2)/(2*s*s)); w=w/w.sum()
  return np.array([float(np.dot(a[i-window+1:i+1],w)) for i in range(window-1,len(a))])

 def _atr_series(self,h,l,c,n=14):
  prev=np.r_[c[0],c[:-1]]
  tr=np.maximum(h-l,np.maximum(np.abs(h-prev),np.abs(l-prev)))
  if len(tr)<n:return np.array([])
  out=np.empty(len(tr)); out[:]=np.nan
  out[n-1]=np.mean(tr[:n])
  for i in range(n,len(tr)): out[i]=(out[i-1]*(n-1)+tr[i])/n
  return out

 def _dmi(self,h,l,c,n=14):
  if len(c)<n+2:return 0.,0.,0.
  up=np.diff(h); dn=-np.diff(l)
  plus=np.where((up>dn)&(up>0),up,0.0)
  minus=np.where((dn>up)&(dn>0),dn,0.0)
  prev=c[:-1]
  tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-prev),np.abs(l[1:]-prev)))
  atr=max(float(np.mean(tr[-n:])),1e-12)
  p=100*float(np.mean(plus[-n:]))/atr
  m=100*float(np.mean(minus[-n:]))/atr
  dx=[]
  for i in range(n,len(tr)+1):
   aa=max(float(np.mean(tr[i-n:i])),1e-12)
   pp=100*float(np.mean(plus[i-n:i]))/aa
   mm=100*float(np.mean(minus[i-n:i]))/aa
   dx.append(100*abs(pp-mm)/max(pp+mm,1e-12))
  adx=float(np.mean(dx[-n:])) if dx else 0.
  return adx,p,m

 def _pivot_levels(self,h,l,strength=3):
  ph=[]; pl=[]
  for i in range(strength,len(h)-strength):
   if h[i]>=np.max(h[i-strength:i+strength+1]): ph.append(float(h[i]))
   if l[i]<=np.min(l[i-strength:i+strength+1]): pl.append(float(l[i]))
  return (ph[-1] if ph else float(np.max(h[-20:])),
          pl[-1] if pl else float(np.min(l[-20:])))

 def analyze(self,ticks,point,rates=None):
  if ticks is None or len(ticks)<120 or point<=0:
   return Regime.NO_TRADE,None,{'reason':'insufficient_data'}

  bid=np.asarray(ticks['bid'],dtype=float); ask=np.asarray(ticks['ask'],dtype=float)
  mid=(bid+ask)/2.0; spread=(ask-bid)/point
  if not np.all(np.isfinite(mid[-120:])):
   return Regime.NO_TRADE,None,{'reason':'bad_data'}

  current_spread=float(spread[-1])
  normal_spread=max(float(np.median(spread[-60:])),.01)
  spread_ratio=current_spread/normal_spread
  if spread_ratio>1.8:return Regime.NO_TRADE,None,{'reason':'spread_spike'}

  # Candle indicators use CLOSED M5 bars only. Tick logic remains as a fast confirmation layer.
  have_rates=rates is not None and len(rates)>=80
  if have_rates:
   o=np.asarray(rates['open'],dtype=float); h=np.asarray(rates['high'],dtype=float)
   l=np.asarray(rates['low'],dtype=float); c=np.asarray(rates['close'],dtype=float)
   atrs=self._atr_series(h,l,c,14); atr=float(atrs[-1]) if len(atrs) and np.isfinite(atrs[-1]) else max(float(np.std(np.diff(c[-30:]))),point)
   atrp=max(atr/point,.01)
   fast=self._ema(c[-80:],20); slow=self._ema(c[-120:] if len(c)>=120 else c,50)
   trend=(fast-slow)/point
   alma=self._alma_series(c,34)
   alma_now=float(alma[-1]) if len(alma) else fast
   alma_prev=float(alma[-4]) if len(alma)>=4 else alma_now
   alma_slope=(alma_now-alma_prev)/max(atr,point)/3.0
   std=float(np.std(c[-34:]))
   upper=alma_now+std; lower=alma_now-std
   adx,di_plus,di_minus=self._dmi(h,l,c,14)
   swing_hi,swing_lo=self._pivot_levels(h,l,3)

   # SuperTrend-style ATR direction: one canonical ATR trend filter, not a separate entry strategy.
   hl2=(h[-1]+l[-1])/2.0
   st_upper=hl2+3.0*atr; st_lower=hl2-3.0*atr
   super_dir=1 if c[-1]>st_lower and fast>=slow else (-1 if c[-1]<st_upper and fast<slow else 0)

   # LazyBear/TTM idea: BB inside Keltner = compression; release = possible expansion.
   bb_mid=float(np.mean(c[-20:])); bb_std=float(np.std(c[-20:]))
   bb_up=bb_mid+2*bb_std; bb_dn=bb_mid-2*bb_std
   kc_up=bb_mid+1.5*atr; kc_dn=bb_mid-1.5*atr
   squeeze_on=(bb_up<kc_up and bb_dn>kc_dn)
   prev_std=float(np.std(c[-21:-1])); prev_mid=float(np.mean(c[-21:-1]))
   squeeze_prev=(prev_mid+2*prev_std < prev_mid+1.5*atr and prev_mid-2*prev_std > prev_mid-1.5*atr)
   squeeze_release=squeeze_prev and not squeeze_on

   mom10=(c[-1]-c[-11])/point; mom30=(c[-1]-c[-31])/point
   base_hi=float(np.max(h[-55:-2])); base_lo=float(np.min(l[-55:-2]))
   mean=float(np.mean(c[-40:])); z=(c[-1]-mean)/max(float(np.std(c[-40:])),point)
  else:
   r=np.diff(mid); atrp=max(float(np.std(r[-80:])/point),.01); atr=atrp*point
   fast=self._ema(mid[-60:],12); slow=self._ema(mid[-100:],32); trend=(fast-slow)/point
   mom10=(mid[-1]-mid[-11])/point; mom30=(mid[-1]-mid[-31])/point
   alma_now=fast; alma_slope=trend/max(atrp,1); upper=fast+atr; lower=fast-atr
   adx=0.; di_plus=di_minus=0.; super_dir=0; squeeze_on=squeeze_release=False
   base_hi=float(np.max(mid[-100:-5])); base_lo=float(np.min(mid[-100:-5]))
   swing_hi=base_hi; swing_lo=base_lo
   mean=float(np.mean(mid[-60:])); z=(mid[-1]-mean)/max(float(np.std(mid[-60:])),point)
   c=mid

  price=float(c[-1]); vol=atrp
  bull=trend>0 and mom30>0 and (not have_rates or (di_plus>di_minus and adx>=20))
  bear=trend<0 and mom30<0 and (not have_rates or (di_minus>di_plus and adx>=20))
  structure_up=price>swing_hi; structure_dn=price<swing_lo
  breakout_up=price>base_hi; breakout_dn=price<base_lo

  sig=None; reg=Regime.NO_TRADE; score=0.; slp=max(10.,atrp*2.0)

  # Priority 1: confirmed structure break. BOS/MSS and old breakout are one strategy to avoid duplicates.
  if (breakout_up and bull) or (breakout_dn and bear):
   side=Side.BUY if breakout_up else Side.SELL
   reg=Regime.BREAKOUT; score=78+min(8,adx/10 if have_rates else 0)+min(6,abs(mom10)/max(vol,1))
   ref=swing_lo if side==Side.BUY else swing_hi
   structure_risk=abs(price-ref)/point
   slp=max(.75*atrp,min(3.0*atrp,structure_risk)) if have_rates else max(10.,vol*3)
   sig=Signal(side,'breakout_confirmed',min(.95,score/100),slp,'BOS + trend + directional strength')

  # Priority 2: volatility compression release in the established direction.
  elif have_rates and squeeze_release and ((bull and mom10>0) or (bear and mom10<0)):
   side=Side.BUY if bull else Side.SELL
   reg=Regime.BREAKOUT; score=76+min(10,adx/10)
   ref=swing_lo if side==Side.BUY else swing_hi
   slp=max(.75*atrp,min(3.0*atrp,abs(price-ref)/point))
   sig=Signal(side,'squeeze_breakout',min(.93,score/100),slp,'BB/Keltner squeeze release + momentum + ADX/DI')

  # Priority 3: trend continuation. EMA is baseline; ALMA slope/band adds conviction without duplicating momentum.
  elif (bull or bear):
   side=Side.BUY if bull else Side.SELL
   alma_ok=(side==Side.BUY and alma_slope>=.08 and price>upper) or (side==Side.SELL and alma_slope<=-.08 and price<lower)
   continuation=(side==Side.BUY and mom10>=-vol*.25) or (side==Side.SELL and mom10<=vol*.25)
   st_ok=(super_dir in (0,1)) if side==Side.BUY else (super_dir in (0,-1))
   if continuation and st_ok and (alma_ok or abs(trend)>=max(1.5,vol*1.2)):
    reg=Regime.TREND; score=72+min(8,abs(alma_slope)*20)+min(8,adx/10 if have_rates else 0)
    ref=swing_lo if side==Side.BUY else swing_hi
    slp=max(.75*atrp,min(3.0*atrp,abs(price-ref)/point)) if have_rates else max(10.,vol*2.8)
    sig=Signal(side,'trend_pullback',min(.94,score/100),slp,'EMA trend + ALMA conviction + ADX/DI + structure stop')

  # Priority 4: mean reversion only when trend strength is weak. No trend indicators are reused here.
  elif abs(z)>=1.8 and (not have_rates or adx<20) and vol<max(8.,float(np.median(np.abs(np.diff(c[-50:]))))/point*4):
   side=Side.SELL if z>0 else Side.BUY
   reg=Regime.RANGE; score=70+min(15,(abs(z)-1.8)*12)
   slp=max(10.,min(3.0*atrp,2.0*atrp))
   sig=Signal(side,'range_reversion',min(.90,score/100),slp,'range extreme + weak ADX mean reversion')

  elif have_rates and (adx>=25 or abs(alma_slope)>=.08): reg=Regime.TREND
  elif vol>8: reg=Regime.VOLATILE
  else: reg=Regime.RANGE

  meta={
   'atr_points':round(atrp,2),'trend_points':round(float(trend),2),
   'momentum10':round(float(mom10),2),'momentum30':round(float(mom30),2),
   'spread_ratio':round(spread_ratio,2),'zscore':round(float(z),2),
   'adx':round(float(adx),1),'di_plus':round(float(di_plus),1),'di_minus':round(float(di_minus),1),
   'alma_slope_atr':round(float(alma_slope),3),'squeeze':bool(squeeze_on),
   'structure_up':bool(structure_up),'structure_down':bool(structure_dn),'score':round(float(score),1)
  }
  return reg,sig,meta
