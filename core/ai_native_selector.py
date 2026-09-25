import asyncio,json,os,time,urllib.request
from .models import Signal,Side,Regime
from .strategy_library import PLAYBOOKS,CATALOG

URL="https://integrate.api.nvidia.com/v1/chat/completions"
MODEL="google/diffusiongemma-26b-a4b-it"
SYSTEM="""You are the sole signal generator for an experimental MT5 scalping bot. Legacy strategy signals are disabled.
Use only the supplied closed bars, live tick summary, and the supplied 50-playbook catalog.
Return ONLY JSON with: decision SIGNAL or NO_TRADE; side BUY, SELL or NONE; strategy_id; confidence integer 0..100; regime TREND,RANGE,BREAKOUT,VOLATILE,MIXED,UNKNOWN; sl_atr_multiple 0.55..2.0; research_required boolean; research_query; reason_code; reason.
For SIGNAL choose only a strategy_id present in the catalog. Be conservative and choose NO_TRADE when evidence conflicts or is unclear.
Never choose volume, monetary risk, TP, leverage, or execute a trade."""

class AINativeSelector:
 def __init__(self,db):
  self.db=db;self.key=os.getenv("NVIDIA_API_KEY","").strip()
  self.model=os.getenv("MTBOT_AI_NATIVE_MODEL",MODEL).strip() or MODEL
  self.timeout=float(os.getenv("MTBOT_AI_NATIVE_TIMEOUT","10"))
  self.min_conf=int(os.getenv("MTBOT_AI_NATIVE_MIN_CONF","75"))

 @staticmethod
 def _bars(r,n):
  if r is None:return []
  names=getattr(getattr(r,"dtype",None),"names",()) or ()
  return [{"t":int(x["time"]),"o":float(x["open"]),"h":float(x["high"]),"l":float(x["low"]),"c":float(x["close"]),"v":float(x["tick_volume"]) if "tick_volume" in names else 0} for x in r[-n:]]

 @staticmethod
 def _atr(r,point,n=14):
  if r is None or len(r)<n+1 or point<=0:return 0.0
  rows=r[-(n+1):];vals=[]
  for i in range(1,len(rows)):
   h=float(rows[i]["high"]);l=float(rows[i]["low"]);pc=float(rows[i-1]["close"])
   vals.append(max(h-l,abs(h-pc),abs(l-pc)))
  return sum(vals[-n:])/len(vals[-n:])/point if vals else 0.0

 def snapshot(self,symbol,tick,info,ticks,m1,m5,m15,h1,performance=None,news=None):
  p=float(info.point);m=[(float(x["bid"])+float(x["ask"]))/2 for x in ticks[-80:]]
  return {"symbol":symbol,"bid":float(tick.bid),"ask":float(tick.ask),"spread_points":round((float(tick.ask)-float(tick.bid))/p,2),
   "tick_momentum_5":round((m[-1]-m[-6])/p,2),"tick_momentum_20":round((m[-1]-m[-21])/p,2),
   "tick_range_40":round((max(m[-40:])-min(m[-40:]))/p,2),"atr_m5_points":round(self._atr(m5,p),2),
   "m1":self._bars(m1,16),"m5":self._bars(m5,16),"m15":self._bars(m15,8),"h1":self._bars(h1,8),
   "performance":performance or {},"news":news or {},"playbooks":[{"id":x[0],"regime":x[1]} for x in PLAYBOOKS]}

 @staticmethod
 def _parse(text):
  text=(text or "").strip().replace("<<<","").replace(">>>","")
  if text.startswith("json"):text=text[4:].strip()
  try:o=json.loads(text)
  except Exception:
   a=text.find("{");b=text.rfind("}")
   if a<0 or b<=a:raise ValueError("invalid AI JSON")
   o=json.loads(text[a:b+1])
  d=str(o.get("decision","")).upper();s=str(o.get("side","")).upper();sid=str(o.get("strategy_id",""));reg=str(o.get("regime","")).upper();c=o.get("confidence")
  if isinstance(c,bool) or not isinstance(c,int) or not 0<=c<=100:raise ValueError("invalid confidence")
  if d not in ("SIGNAL","NO_TRADE"):raise ValueError("invalid decision")
  if reg not in ("TREND","RANGE","BREAKOUT","VOLATILE","MIXED","UNKNOWN"):raise ValueError("invalid regime")
  if d=="SIGNAL":
   if s not in ("BUY","SELL") or sid not in CATALOG or sid=="no_trade_unclear":raise ValueError("invalid bounded selection")
   mult=float(o.get("sl_atr_multiple",1.0))
   if not .55<=mult<=2.0:raise ValueError("invalid stop multiple")
  else:s="NONE";sid="none";mult=0.0
  return {"decision":d,"side":s,"strategy_id":sid,"confidence":c,"regime":reg,"sl_atr_multiple":mult,
   "research_required":bool(o.get("research_required",False)),"research_query":str(o.get("research_query",""))[:160],
   "reason_code":str(o.get("reason_code","AI_NATIVE"))[:80],"reason":str(o.get("reason",""))[:160]}

 def _sync(self,snap):
  body=json.dumps({"model":self.model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":json.dumps(snap,separators=(",",":"))}],"temperature":0.0,"top_p":1.0,"max_tokens":512,"stream":False}).encode()
  req=urllib.request.Request(URL,data=body,headers={"Authorization":"Bearer "+self.key,"Content-Type":"application/json"})
  start=time.monotonic()
  with urllib.request.urlopen(req,timeout=self.timeout) as r:raw=json.loads(r.read().decode())
  return self._parse(raw["choices"][0]["message"].get("content") or ""),int((time.monotonic()-start)*1000),raw.get("usage",{})

 async def decide(self,symbol,snap):
  if not self.key:return {"decision":"NO_TRADE","confidence":100,"reason_code":"AI_UNAVAILABLE","reason":"NVIDIA_API_KEY missing"}
  try:
   o,lat,u=await asyncio.wait_for(asyncio.to_thread(self._sync,snap),timeout=self.timeout+1)
   o.update({"model":self.model,"latency_ms":lat})
   if o["decision"]=="SIGNAL" and o["confidence"]<self.min_conf:o.update({"decision":"NO_TRADE","reason_code":"AI_LOW_CONFIDENCE","reason":"AI-native confidence below threshold"})
   await self.db.log("AI_NATIVE_DECISION",symbol,**o,usage=u);return o
  except Exception as ex:
   await self.db.log("AI_NATIVE_ERROR",symbol,error=repr(ex))
   return {"decision":"NO_TRADE","confidence":100,"reason_code":"AI_ERROR","reason":str(ex)[:160]}

 def to_signal(self,o,snap):
  if o.get("decision")!="SIGNAL":return None,Regime.NO_TRADE
  side=Side.BUY if o["side"]=="BUY" else Side.SELL
  atr=max(float(snap.get("atr_m5_points",0) or 0),1.0)
  sl=max(8.0,atr*float(o["sl_atr_multiple"]))
  reg=o.get("regime","UNKNOWN");reg=Regime[reg] if reg in Regime.__members__ else Regime.NO_TRADE
  return Signal(side,o["strategy_id"],o["confidence"]/100.0,sl,o.get("reason","AI-native signal")),reg
