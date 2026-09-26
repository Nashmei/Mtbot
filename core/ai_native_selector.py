import asyncio,json,os,time,urllib.error,urllib.request
from .models import Signal,Side,Regime
from .strategy_library import PLAYBOOKS,CATALOG

URL="https://integrate.api.nvidia.com/v1/chat/completions"
MODEL="google/diffusiongemma-26b-a4b-it"
SYSTEM="""You are the sole AI signal generator and trade-plan designer for an experimental MT5 short-horizon scalping system. Legacy strategy signals are disabled.
Your objective is not to maximize trade count. Select only high-quality, executable opportunities with positive expected value. NO_TRADE is a successful decision when evidence is weak, conflicting, late, overextended, or trade geometry is poor.

Use only supplied market data: live bid/ask and spread, live tick statistics, closed M1/M5 bars, M15/H1 context, volatility/ATR, the supplied playbook catalog and performance, and supplied news/research when available. Never invent prices, indicators, news, or unavailable conditions.

TIMEFRAME PRIORITY: live ticks for immediate timing; M1 for microstructure/setup confirmation; M5 for short-term regime; M15/H1 only for context and conflict detection. M15/H1 must never create a trade unsupported by ticks/M1/M5.

A SIGNAL requires a clear directional edge, a valid catalog playbook, acceptable current conditions, a non-late entry, a logical invalidation level, a realistic target supported by structure/volatility, and coherent immediate price action. Otherwise return NO_TRADE. Use supplied strategy performance as supporting evidence, not an automatic ban or guarantee.

For every SIGNAL choose exact sl_price and tp_price. SL must represent genuine setup invalidation, not an arbitrary distance. TP must be realistically reachable from current structure, momentum, volatility, tick behavior and expected duration. Never tighten SL or extend TP merely to manufacture reward/risk.

Do not output an rr field. The deterministic engine calculates actual reward/risk from fresh MT5 prices. Aim for initial R:R >= 1.50 when realistic, prefer >= 2.00 when structure supports it, and return NO_TRADE when >= 1.50 requires an unsafe SL or unrealistic TP. The engine may observe/reject plans independently.

Reject chasing: return NO_TRADE when most of the expected move already occurred, entry is too close to target, price is excessively extended, spread consumes too much expected movement, a reasonable SL makes the trade unattractive, or price lacks directional edge.

Choose protection_pct and trailing_gap_pct for the setup and current noise/volatility. Preserve meaningful profit without choking normal movement. Trending/impulsive setups may need more breathing room; fast exhaustion/reversal setups may need tighter protection.

Expected duration should normally be 2-10 minutes. Do not reject an otherwise excellent setup solely because it may finish slightly faster than 2 minutes, but reject setups that materially exceed the short-horizon thesis or whose duration cannot be estimated reliably.

manual_overrides value 0 means AI decides that field; any positive value is authoritative and applied by the deterministic engine. risk_pct is immutable. Never choose volume, monetary risk, risk_pct, leverage, or execute a trade.

Confidence must reflect evidence quality and must not default repeatedly to the same value. High confidence requires multiple independent observations to agree.

Set research_required=true only when fresh external information could materially change the immediate decision.

reason_code MUST be exactly one of:
SIGNAL: TREND_ALIGNED, MOMENTUM_CONFIRM, BREAKOUT_CONFIRM, PULLBACK_FORMED, REVERSION_SETUP, LIQUIDITY_SWEEP
NO_TRADE: LACK_MOMENTUM, CONFLICT_SIGNALS, RANGE_NO_BREAKOUT, OVEREXTENDED, POOR_RR, LATE_ENTRY, SPREAD_TOO_WIDE, DURATION_UNCERTAIN, NEWS_RISK

Return ONLY one valid JSON object with: decision SIGNAL or NO_TRADE; side BUY, SELL or NONE; strategy_id; confidence integer 0..100; regime TREND,RANGE,BREAKOUT,VOLATILE,MIXED,UNKNOWN; sl_price positive number; tp_price positive number; protection_pct number 15..80; trailing_gap_pct number 2..25; expected_duration_minutes normally 2..10 for SIGNAL; research_required boolean; research_query; reason_code; reason.

For SIGNAL choose only a strategy_id present in the catalog and use a SIGNAL reason_code. SL/TP must be on the correct side of supplied bid/ask. For NO_TRADE use side NONE, strategy_id none, zero prices/management values, and a NO_TRADE reason_code. Never output anything outside the JSON object."""

class AINativeSelector:
 def __init__(self,db):
  self.db=db;self.key=os.getenv("NVIDIA_API_KEY","").strip()
  self.model=os.getenv("MTBOT_AI_NATIVE_MODEL",MODEL).strip() or MODEL
  self.timeout=float(os.getenv("MTBOT_AI_NATIVE_TIMEOUT","10"))
  self.min_conf=int(os.getenv("MTBOT_AI_NATIVE_MIN_CONF","75"))
  self.min_interval=float(os.getenv("MTBOT_AI_NATIVE_MIN_INTERVAL","30"))
  self.global_interval=float(os.getenv("MTBOT_AI_NATIVE_GLOBAL_INTERVAL","3"))
  self.max_backoff=float(os.getenv("MTBOT_AI_NATIVE_MAX_BACKOFF","120"))
  self._last_symbol_call={}
  self._cache={}
  self._consumed_signal_keys=set()
  self._global_lock=asyncio.Lock()
  self._last_global_call=0.0
  self._backoff_until=0.0

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
  return {"symbol":symbol,"point":p,"digits":int(getattr(info,"digits",5) or 5),"bid":float(tick.bid),"ask":float(tick.ask),"spread_points":round((float(tick.ask)-float(tick.bid))/p,2),
   "tick_momentum_5":round((m[-1]-m[-6])/p,2),"tick_momentum_20":round((m[-1]-m[-21])/p,2),
   "tick_range_40":round((max(m[-40:])-min(m[-40:]))/p,2),"atr_m5_points":round(self._atr(m5,p),2),
   "m1":self._bars(m1,10),"m5":self._bars(m5,10),"m15":self._bars(m15,6),"h1":self._bars(h1,4),
   "performance":performance or {},"news":news or {},"playbooks":[{"id":x[0],"regime":x[1]} for x in PLAYBOOKS]}

 @staticmethod
 def _parse(text):
  text=(text or "").strip().replace("<<<","").replace(">>>","")
  if text.startswith("json"):text=text[4:].strip()
  try:o=json.loads(text,strict=False)
  except Exception:
   a=text.find("{");b=text.rfind("}")
   if a<0 or b<=a:raise ValueError("invalid AI JSON")
   candidate=text[a:b+1]
   try:o=json.loads(candidate,strict=False)
   except Exception:
    # Some models occasionally emit literal control characters inside JSON strings.
    candidate="".join((" " if ord(ch)<32 and ch not in "\\t\\r\\n" else ch) for ch in candidate)
    o=json.loads(candidate,strict=False)
  d=str(o.get("decision","")).upper();s=str(o.get("side","")).upper();sid=str(o.get("strategy_id",""));reg=str(o.get("regime","")).upper();c=o.get("confidence")
  if isinstance(c,bool) or not isinstance(c,int) or not 0<=c<=100:raise ValueError("invalid confidence")
  if d not in ("SIGNAL","NO_TRADE"):raise ValueError("invalid decision")
  signal_codes={"TREND_ALIGNED","MOMENTUM_CONFIRM","BREAKOUT_CONFIRM","PULLBACK_FORMED","REVERSION_SETUP","LIQUIDITY_SWEEP"}
  no_trade_codes={"LACK_MOMENTUM","CONFLICT_SIGNALS","RANGE_NO_BREAKOUT","OVEREXTENDED","POOR_RR","LATE_ENTRY","SPREAD_TOO_WIDE","DURATION_UNCERTAIN","NEWS_RISK"}
  reason_code=str(o.get("reason_code","")).upper()
  if reg not in ("TREND","RANGE","BREAKOUT","VOLATILE","MIXED","UNKNOWN"):raise ValueError("invalid regime")
  if d=="SIGNAL":
   if s not in ("BUY","SELL") or sid not in CATALOG or sid=="no_trade_unclear":raise ValueError("invalid bounded selection")
   sl_price=float(o.get("sl_price",0));tp_price=float(o.get("tp_price",0))
   protection=float(o.get("protection_pct",0));trailing=float(o.get("trailing_gap_pct",0))
   duration=float(o.get("expected_duration_minutes",0))
   if sl_price<=0 or tp_price<=0:raise ValueError("invalid AI SL/TP")
   if not 15.0<=protection<=80.0:raise ValueError("invalid protection percent")
   if not 2.0<=trailing<=25.0:raise ValueError("invalid trailing gap percent")
   if not 2.0<=duration<=10.0:raise ValueError("invalid expected duration")
  else:
   if reason_code not in no_trade_codes:raise ValueError("invalid NO_TRADE reason_code")
   s="NONE";sid="none";sl_price=0.0;tp_price=0.0;protection=0.0;trailing=0.0;duration=0.0
  return {"decision":d,"side":s,"strategy_id":sid,"confidence":c,"regime":reg,"sl_price":sl_price,"tp_price":tp_price,"protection_pct":protection,"trailing_gap_pct":trailing,"expected_duration_minutes":duration,
   "research_required":bool(o.get("research_required",False)),"research_query":str(o.get("research_query",""))[:160],
   "reason_code":reason_code[:80],"reason":str(o.get("reason",""))[:160]}

 def _sync(self,snap):
  body=json.dumps({"model":self.model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":json.dumps(snap,separators=(",",":"))}],"temperature":0.0,"top_p":1.0,"max_tokens":512,"stream":False}).encode()
  req=urllib.request.Request(URL,data=body,headers={"Authorization":"Bearer "+self.key,"Content-Type":"application/json"})
  start=time.monotonic()
  with urllib.request.urlopen(req,timeout=self.timeout) as r:raw=json.loads(r.read().decode())
  return self._parse(raw["choices"][0]["message"].get("content") or ""),int((time.monotonic()-start)*1000),raw.get("usage",{})

 @staticmethod
 def _snapshot_key(snap):
  # One decision per symbol/bar state. Live bid/ask/tick micro-noise is deliberately
  # excluded so the scanner cannot hammer the provider on every polling cycle.
  def last_bar(tf):
   rows=snap.get(tf) or []
   return int(rows[-1].get("t",0)) if rows else 0
  return (last_bar("m1"),last_bar("m5"),last_bar("m15"),last_bar("h1"))

 def _no_trade(self,code,reason,**extra):
  out={"decision":"NO_TRADE","confidence":100,"reason_code":code,"reason":reason}
  out.update(extra);return out

 async def decide(self,symbol,snap,consume_signal=True):
  if not self.key:
   out=self._no_trade("AI_UNAVAILABLE","NVIDIA_API_KEY missing")
   await self.db.log("AI_NATIVE_UNAVAILABLE",symbol,**out)
   return out

  now=time.monotonic();key=self._snapshot_key(snap);cache_key=(symbol,key);cached=self._cache.get(symbol)
  if cached and cached[0]==key:
   out=dict(cached[1]);out["cached"]=True
   if out.get("decision")!="SIGNAL":return out
   if cache_key in self._consumed_signal_keys:
    return self._no_trade("AI_SIGNAL_ALREADY_CONSUMED","Cached scalp signal already consumed; wait for a fresh market snapshot")
   if consume_signal:self._consumed_signal_keys.add(cache_key)
   return out
  since=now-self._last_symbol_call.get(symbol,0.0)
  if since<self.min_interval:
   return self._no_trade("AI_RATE_LIMIT_LOCAL","Per-symbol AI cooldown",retry_after_seconds=round(self.min_interval-since,1))
  if now<self._backoff_until:
   return self._no_trade("AI_PROVIDER_BACKOFF","Provider backoff active",retry_after_seconds=round(self._backoff_until-now,1))

  async with self._global_lock:
   now=time.monotonic()
   if now<self._backoff_until:
    return self._no_trade("AI_PROVIDER_BACKOFF","Provider backoff active",retry_after_seconds=round(self._backoff_until-now,1))
   wait=self.global_interval-(now-self._last_global_call)
   if wait>0:await asyncio.sleep(wait)
   self._last_global_call=time.monotonic()
   self._last_symbol_call[symbol]=self._last_global_call
   try:
    o,lat,u=await asyncio.wait_for(asyncio.to_thread(self._sync,snap),timeout=self.timeout+1)
    o.update({"model":self.model,"latency_ms":lat,"cached":False})
    if o["decision"]=="SIGNAL" and o["confidence"]<self.min_conf:
     o.update({"decision":"NO_TRADE","reason_code":"AI_LOW_CONFIDENCE","reason":"AI-native confidence below threshold"})
    self._cache[symbol]=(key,dict(o))
    if o.get("decision")=="SIGNAL" and consume_signal:self._consumed_signal_keys.add(cache_key)
    await self.db.log("AI_NATIVE_DECISION",symbol,**o,usage=u)
    return o
   except urllib.error.HTTPError as ex:
    if ex.code==429:
     raw=ex.headers.get("Retry-After","") if ex.headers else ""
     try:delay=float(raw)
     except Exception:delay=min(self.max_backoff,max(15.0,self.min_interval*2))
     delay=max(5.0,min(self.max_backoff,delay))
     self._backoff_until=time.monotonic()+delay
     await self.db.log("AI_NATIVE_429",symbol,retry_after_seconds=delay)
     return self._no_trade("AI_PROVIDER_429","Provider rate limit",retry_after_seconds=delay)
    await self.db.log("AI_NATIVE_ERROR",symbol,error=repr(ex))
    return self._no_trade("AI_HTTP_ERROR",str(ex)[:160])
   except Exception as ex:
    await self.db.log("AI_NATIVE_ERROR",symbol,error=repr(ex))
    return self._no_trade("AI_ERROR",str(ex)[:160])

 def to_signal(self,o,snap):
  if o.get("decision")!="SIGNAL":return None,Regime.NO_TRADE
  side=Side.BUY if o["side"]=="BUY" else Side.SELL
  point=max(float(snap.get("point",0) or 0),1e-12)
  entry=float(snap.get("ask") if side==Side.BUY else snap.get("bid"))
  sl_points=abs(entry-float(o["sl_price"]))/point
  reg=o.get("regime","UNKNOWN");reg=Regime[reg] if reg in Regime.__members__ else Regime.NO_TRADE
  return Signal(side,o["strategy_id"],o["confidence"]/100.0,sl_points,o.get("reason","AI-native signal")),reg
