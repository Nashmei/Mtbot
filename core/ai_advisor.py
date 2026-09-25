import asyncio, hashlib, json, os, time, urllib.request

BASE_URL="https://integrate.api.nvidia.com/v1/chat/completions"
FAST_MODEL="nvidia/nemotron-3.5-lightning-30b-a3b"
DEEP_MODEL="nvidia/nemotron-3-super-120b-a12b"

SYSTEM="""You are the primary entry-quality judge for a very short-term MT5 scalping bot.
Return ONLY one compact JSON object with keys:
decision: ALLOW or REJECT
confidence: integer 0..100
regime: TREND, RANGE, VOLATILE, MIXED, or UNKNOWN
reason_code: short uppercase token
reason: <=160 characters
Judge only whether the proposed side/strategy has enough market alignment to enter now.
Be conservative. Penalize late/chasing entries, conflicting M15/H1 bias, weak momentum,
abnormal spread, exhausted candles, and strategy/regime mismatch.
Do not suggest volume, risk, SL, TP, leverage, or another trade. No markdown."""

class AIAdvisor:
 def __init__(self,db):
  self.db=db
  self.api_key=os.getenv("NVIDIA_API_KEY","").strip()
  self.fast_model=os.getenv("MTBOT_AI_FAST_MODEL",FAST_MODEL).strip() or FAST_MODEL
  self.deep_model=os.getenv("MTBOT_AI_DEEP_MODEL",DEEP_MODEL).strip() or DEEP_MODEL
  self.fast_timeout=float(os.getenv("MTBOT_AI_FAST_TIMEOUT","3.0"))
  self.deep_timeout=float(os.getenv("MTBOT_AI_DEEP_TIMEOUT","5.0"))
  self.fast_accept=int(os.getenv("MTBOT_AI_FAST_ACCEPT_CONF","75"))
  self.deep_accept=int(os.getenv("MTBOT_AI_DEEP_ACCEPT_CONF","65"))

 @property
 def enabled(self): return bool(self.api_key)

 @staticmethod
 def _bars(rates,n=8):
  if rates is None:return []
  out=[]
  for r in rates[-n:]:
   out.append({"t":int(r["time"]),"o":float(r["open"]),"h":float(r["high"]),"l":float(r["low"]),"c":float(r["close"]),"v":float(r["tick_volume"]) if "tick_volume" in rates.dtype.names else 0})
  return out

 def snapshot(self,symbol,sig,reg,tick,info,m1,m5,meta,strategy_performance):
  point=float(info.point)
  spread=(float(tick.ask)-float(tick.bid))/point if point else 0
  return {
   "symbol":symbol,"side":sig.side.value,"strategy":sig.strategy,
   "strategy_confidence":round(float(sig.confidence)*100,2),
   "strategy_sl_points":round(float(sig.sl_points),2),
   "regime":getattr(reg,"value",str(reg)),
   "bid":float(tick.bid),"ask":float(tick.ask),"spread_points":round(spread,2),
   "candidates":meta.get("strategy_selection",[]) or [],
   "strategy_performance":strategy_performance or {},
   "m1_last8":self._bars(m1,8),"m5_last8":self._bars(m5,8),
  }

 def _call_sync(self,model,snapshot,timeout):
  payload=json.dumps({
   "model":model,
   "messages":[{"role":"system","content":SYSTEM},{"role":"user","content":json.dumps(snapshot,separators=(",",":"))}],
   "temperature":0.0,"top_p":1.0,"max_tokens":220,"stream":False,
  }).encode()
  req=urllib.request.Request(BASE_URL,data=payload,headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"})
  started=time.monotonic()
  with urllib.request.urlopen(req,timeout=timeout) as r:
   raw=json.loads(r.read().decode())
  latency_ms=int((time.monotonic()-started)*1000)
  choice=raw["choices"][0]["message"]
  text=(choice.get("content") or "").strip()
  if text.startswith("```"):
   text=text.replace("```json","").replace("```","").strip()
  obj=json.loads(text)
  decision=str(obj.get("decision","")).upper()
  confidence=max(0,min(100,int(obj.get("confidence",0))))
  if decision not in ("ALLOW","REJECT"):raise ValueError("invalid AI decision")
  obj["decision"]=decision;obj["confidence"]=confidence
  return obj,latency_ms,raw.get("usage",{})

 async def _call(self,model,snapshot,timeout):
  return await asyncio.wait_for(asyncio.to_thread(self._call_sync,model,snapshot,timeout),timeout=timeout+0.5)

 async def decide(self,symbol,snapshot):
  if not self.enabled:
   await self.db.log("AI_REJECT",symbol,reason="NVIDIA_API_KEY_MISSING")
   return {"decision":"REJECT","confidence":100,"reason_code":"AI_UNAVAILABLE","reason":"NVIDIA_API_KEY missing","model":None}
  prompt_hash=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:16]
  started=time.monotonic()
  try:
   fast,latency,usage=await self._call(self.fast_model,snapshot,self.fast_timeout)
   fast.update({"model":self.fast_model,"latency_ms":latency,"prompt_hash":prompt_hash})
   await self.db.log("AI_FAST_DECISION",symbol,**fast,usage=usage)
   # High-confidence Lightning decisions are final. Ambiguous calls escalate.
   if fast["confidence"]>=self.fast_accept:
    return fast
   deep,latency2,usage2=await self._call(self.deep_model,snapshot,self.deep_timeout)
   deep.update({"model":self.deep_model,"latency_ms":latency2,"prompt_hash":prompt_hash,"escalated_from":fast})
   await self.db.log("AI_DEEP_DECISION",symbol,**deep,usage=usage2)
   # Deep model must itself be sufficiently certain; otherwise fail closed.
   if deep["confidence"]<self.deep_accept:
    deep["decision"]="REJECT";deep["reason_code"]="AI_LOW_CONFIDENCE"
   return deep
  except Exception as ex:
   await self.db.log("AI_ERROR",symbol,error=repr(ex),prompt_hash=prompt_hash,elapsed_ms=int((time.monotonic()-started)*1000))
   return {"decision":"REJECT","confidence":100,"reason_code":"AI_ERROR","reason":str(ex)[:160],"model":None,"prompt_hash":prompt_hash}
