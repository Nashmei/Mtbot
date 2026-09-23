from collections import deque
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

  # Allow at most two 10% relaxations. Rejected quotes must not inflate the
  # baseline until an unusually wide spread becomes the new normal.
  level=self.spread_relax_level.get(key,0)
  lim=base_lim*(1.0+(0.10*level))

  if settings.max_spread_points>0:
   lim=min(lim,settings.max_spread_points)

  ok=sp<=lim

  if ok:
   q.append(sp)
   self.spread_reject_streak[key]=0
  else:
   streak=self.spread_reject_streak.get(key,0)+1
   if streak>=3:
    self.spread_relax_level[key]=min(2,level+1)
    streak=0
   self.spread_reject_streak[key]=streak

  return ok,sp,avg,lim

 def reset_spread_relaxation(self,symbol):
  key=str(symbol)
  self.spread_reject_streak[key]=0
  self.spread_relax_level[key]=0
