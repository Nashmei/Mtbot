import numpy as np
from .models import Regime,Signal,Side

class Analyzer:
 def analyze(self,ticks,point):
  if ticks is None or len(ticks)<120:
   return Regime.NO_TRADE,None,{'reason':'insufficient_ticks'}

  bid=np.asarray(ticks['bid'],dtype=float)
  ask=np.asarray(ticks['ask'],dtype=float)
  mid=(bid+ask)/2.0
  spread=(ask-bid)/point

  if not np.all(np.isfinite(mid[-120:])) or point<=0:
   return Regime.NO_TRADE,None,{'reason':'bad_data'}

  def ema(a,n):
   alpha=2.0/(n+1.0)
   out=float(a[0])
   for v in a[1:]:
    out=alpha*float(v)+(1-alpha)*out
   return out

  fast=ema(mid[-60:],12)
  slow=ema(mid[-100:],32)
  r=np.diff(mid)
  vol=max(float(np.std(r[-80:])/point),0.01)
  mom10=float((mid[-1]-mid[-11])/point)
  mom30=float((mid[-1]-mid[-31])/point)
  trend=float((fast-slow)/point)

  base=mid[-100:-5]
  hi=float(np.max(base))
  lo=float(np.min(base))
  mean=float(np.mean(mid[-60:]))
  std=max(float(np.std(mid[-60:])),point)
  z=float((mid[-1]-mean)/std)

  current_spread=float(spread[-1])
  normal_spread=max(float(np.median(spread[-60:])),0.01)
  spread_ratio=current_spread/normal_spread

  # سبريد غير طبيعي = لا تداول
  if spread_ratio>1.8:
   return Regime.NO_TRADE,None,{'reason':'spread_spike'}

  sig=None
  reg=Regime.NO_TRADE
  score=0

  # 1) Breakout: اختراق + زخم متعدد الفترات + اتجاه
  up_break=mid[-1]>hi and mom10>0 and mom30>0 and trend>0
  dn_break=mid[-1]<lo and mom10<0 and mom30<0 and trend<0
  if up_break or dn_break:
   reg=Regime.BREAKOUT
   side=Side.BUY if up_break else Side.SELL
   score=75
   score+=min(8,abs(mom10)/max(vol,0.1)*2)
   score+=min(7,abs(mom30)/max(vol,0.1))
   score+=min(5,abs(trend)/max(vol,0.1))
   score-=max(0,(spread_ratio-1)*8)
   slp=max(10.0,vol*3.0)
   sig=Signal(side,'breakout_confirmed',min(.95,score/100),slp,
              'confirmed breakout + momentum + trend')

  # 2) Trend pullback: اتجاه واضح مع رجوع قصير ثم استئناف
  elif abs(trend)>=max(1.5,vol*1.2) and abs(mom30)>=max(2.0,vol*1.5):
   side=Side.BUY if trend>0 else Side.SELL
   aligned=(side==Side.BUY and mom10>=-vol) or (side==Side.SELL and mom10<=vol)
   if aligned:
    reg=Regime.TREND
    score=72
    score+=min(10,abs(trend)/max(vol,0.1)*2)
    score+=min(8,abs(mom30)/max(vol,0.1))
    score-=max(0,(spread_ratio-1)*8)
    slp=max(10.0,vol*2.8)
    sig=Signal(side,'trend_pullback',min(.94,score/100),slp,
               'trend + multi-period momentum confirmation')

  # 3) Range reversion: فقط عند ابتعاد قوي عن المتوسط وبدون اتجاه قوي
  elif abs(z)>=1.7 and abs(trend)<max(2.0,vol*1.4) and vol<5.0:
   reg=Regime.RANGE
   side=Side.SELL if z>0 else Side.BUY
   score=70+min(15,(abs(z)-1.7)*12)
   score+=max(0,5-spread_ratio*3)
   slp=max(10.0,vol*2.5)
   sig=Signal(side,'range_reversion',min(.90,score/100),slp,
              'range extreme + mean reversion filter')

  elif vol>6:
   reg=Regime.VOLATILE

  # فلتر تأكيد السوق النهائي قبل السماح بالتنفيذ
  if sig is not None:
   direction_ok = (
    (sig.side==Side.BUY and trend>0) or
    (sig.side==Side.SELL and trend<0)
   )
   momentum_ok = (
    (sig.side==Side.BUY and mom10>0 and mom30>0) or
    (sig.side==Side.SELL and mom10<0 and mom30<0)
   )

   if sig.strategy=='breakout_confirmed':
    market_ok = direction_ok and momentum_ok and vol<=8.0

   elif sig.strategy=='trend_pullback':
    market_ok = direction_ok and (
     (sig.side==Side.BUY and mom30>0 and mom10>=-vol*0.25) or
     (sig.side==Side.SELL and mom30<0 and mom10<=vol*0.25)
    ) and vol<=8.0

   elif sig.strategy=='range_reversion':
    # الرينج عكس الاتجاه بطبيعته؛ نطلب ضعف الاتجاه وبدء انعكاس الزخم القصير
    weak_trend = abs(trend)<max(2.0,vol*1.4)
    reversal_ok = (
     (sig.side==Side.BUY and mom10>0) or
     (sig.side==Side.SELL and mom10<0)
    )
    market_ok = weak_trend and reversal_ok and vol<5.0

   else:
    market_ok=False

   if not market_ok:
    sig=None
    reg=Regime.NO_TRADE

  meta={
   'vol_points':round(vol,2),
   'trend_points':round(trend,2),
   'momentum10':round(mom10,2),
   'momentum30':round(mom30,2),
   'spread_ratio':round(spread_ratio,2),
   'zscore':round(z,2),
   'score':round(score,1)
  }
  return reg,sig,meta
