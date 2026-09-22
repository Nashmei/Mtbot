from collections import deque
import math
from .config import settings

class Risk:
 def __init__(self):
  self.spreads={}
  self.spread_reject_streak={}
  self.spread_relax_level={}

 def spread_ok(self,tick,info):
  key=str(getattr(info,'name','UNKNOWN'))
  q=self.spreads.get(key)
  if q is None:
   q=deque(maxlen=settings.spread_sample_size)
   self.spreads[key]=q

  sp=(tick.ask-tick.bid)/info.point

  # Baseline uses recent spread history before the current tick.
  avg=(sum(q)/len(q)) if q else sp
  base_lim=max(avg*settings.max_spread_multiplier,avg+1.0)

  # Every 3 consecutive spread rejections widens the baseline by another 10%.
  # The widened level persists until a trade is successfully opened.
  level=self.spread_relax_level.get(key,0)
  lim=base_lim*(1.0+(0.10*level))

  if settings.max_spread_points>0:
   lim=min(lim,settings.max_spread_points)

  ok=sp<=lim
  q.append(sp)

  if ok:
   self.spread_reject_streak[key]=0
  else:
   streak=self.spread_reject_streak.get(key,0)+1
   if streak>=3:
    self.spread_relax_level[key]=level+1
    streak=0
   self.spread_reject_streak[key]=streak

  return ok,sp,avg,lim

 def reset_spread_relaxation(self,symbol):
  key=str(symbol)
  self.spread_reject_streak[key]=0
  self.spread_relax_level[key]=0

 def volume(self,account,info,sl_points,risk_pct=None):
  if risk_pct is None: risk_pct=settings.risk_per_trade_pct
  risk_cash=account.equity*(risk_pct/100); tick_value=info.trade_tick_value_loss or info.trade_tick_value; tick_size=info.trade_tick_size
  if not tick_value or not tick_size:return info.volume_min
  loss_per_lot=(sl_points*info.point/tick_size)*tick_value; raw=risk_cash/loss_per_lot
  steps=math.floor(max(0,(raw-info.volume_min)/info.volume_step)); return max(info.volume_min,min(info.volume_max,info.volume_min+steps*info.volume_step))
