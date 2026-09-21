from collections import deque
import math
from .config import settings
class Risk:
 def __init__(self):
  self.spreads={}
 def spread_ok(self,tick,info):
  key=str(getattr(info,'name','UNKNOWN'))
  q=self.spreads.get(key)
  if q is None:
   q=deque(maxlen=settings.spread_sample_size)
   self.spreads[key]=q

  sp=(tick.ask-tick.bid)/info.point
  q.append(sp)
  avg=sum(q)/len(q)
  lim=avg*settings.max_spread_multiplier

  if settings.max_spread_points>0:
   lim=min(lim,settings.max_spread_points)

  return sp<=lim,sp,avg,lim
 def volume(self,account,info,sl_points,risk_pct=None):
  if risk_pct is None: risk_pct=settings.risk_per_trade_pct
  risk_cash=account.equity*(risk_pct/100); tick_value=info.trade_tick_value_loss or info.trade_tick_value; tick_size=info.trade_tick_size
  if not tick_value or not tick_size:return info.volume_min
  loss_per_lot=(sl_points*info.point/tick_size)*tick_value; raw=risk_cash/loss_per_lot
  steps=math.floor(max(0,(raw-info.volume_min)/info.volume_step)); return max(info.volume_min,min(info.volume_max,info.volume_min+steps*info.volume_step))
