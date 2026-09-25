import asyncio
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE_URL="https://integrate.api.nvidia.com/v1/chat/completions"

# Primary model selected after MTbot contract/stress testing.
FAST_MODEL="google/diffusiongemma-26b-a4b-it"

# Keep the existing deep model available only for low-confidence escalation.
DEEP_MODEL="nvidia/nemotron-3-super-120b-a12b"

VALID_DECISIONS={"ALLOW","REJECT"}
VALID_REGIMES={"TREND","RANGE","VOLATILE","MIXED","UNKNOWN"}

SYSTEM="""You are the primary entry-quality judge for a very short-term MT5 scalping bot.

Return ONLY one compact JSON object:
{"decision":"ALLOW|REJECT","confidence":0,"regime":"TREND|RANGE|VOLATILE|MIXED|UNKNOWN","reason_code":"TOKEN","reason":"short reason"}

Rules:
- decision MUST be ALLOW or REJECT.
- confidence MUST be integer 0..100.
- Be conservative.
- ALLOW only when the proposed entry has sufficient alignment NOW.
- Penalize M15/H1 conflict.
- Penalize abnormal spread.
- Penalize late/chasing/extended entries.
- Penalize weak momentum.
- Penalize strategy/regime mismatch.
- Reject unclear/risky setups.
- Never change side or strategy.
- Do not suggest volume, risk, SL, TP or leverage.
- No markdown.
- No explanation outside JSON."""


class AIAdvisor:
 def __init__(self,db):
  self.db=db
  self.api_key=os.getenv("NVIDIA_API_KEY","").strip()

  self.fast_model=os.getenv(
   "MTBOT_AI_FAST_MODEL",FAST_MODEL
  ).strip() or FAST_MODEL

  self.deep_model=os.getenv(
   "MTBOT_AI_DEEP_MODEL",DEEP_MODEL
  ).strip() or DEEP_MODEL

  self.fast_timeout=float(
   os.getenv("MTBOT_AI_FAST_TIMEOUT","10.0")
  )

  self.deep_timeout=float(
   os.getenv("MTBOT_AI_DEEP_TIMEOUT","10.0")
  )

  self.fast_accept=int(
   os.getenv("MTBOT_AI_FAST_ACCEPT_CONF","75")
  )

  self.deep_accept=int(
   os.getenv("MTBOT_AI_DEEP_ACCEPT_CONF","65")
  )

  # One retry only for transient NVIDIA endpoint failures.
  self.retry_delay=float(
   os.getenv("MTBOT_AI_RETRY_DELAY","2.0")
  )


 @property
 def enabled(self):
  return bool(self.api_key)


 @staticmethod
 def _bars(rates,n=8):
  if rates is None:
   return []

  out=[]

  for r in rates[-n:]:
   out.append({
    "t":int(r["time"]),
    "o":float(r["open"]),
    "h":float(r["high"]),
    "l":float(r["low"]),
    "c":float(r["close"]),
    "v":float(r["tick_volume"])
      if "tick_volume" in rates.dtype.names else 0
   })

  return out


 def snapshot(
  self,symbol,sig,reg,tick,info,
  m1,m5,meta,strategy_performance
 ):
  point=float(info.point)

  spread=(
   (float(tick.ask)-float(tick.bid))/point
   if point else 0
  )

  return {
   "symbol":symbol,
   "side":sig.side.value,
   "strategy":sig.strategy,
   "strategy_confidence":round(
    float(sig.confidence)*100,2
   ),
   "strategy_sl_points":round(
    float(sig.sl_points),2
   ),
   "regime":getattr(reg,"value",str(reg)),
   "bid":float(tick.bid),
   "ask":float(tick.ask),
   "spread_points":round(spread,2),
   "candidates":
    meta.get("strategy_selection",[]) or [],
   "strategy_performance":
    strategy_performance or {},
   "m1_last8":self._bars(m1,8),
   "m5_last8":self._bars(m5,8),
  }


 @staticmethod
 def _validate_response(obj):
  if not isinstance(obj,dict):
   return None

  decision=str(
   obj.get("decision","")
  ).strip().upper()

  regime=str(
   obj.get("regime","")
  ).strip().upper()

  confidence=obj.get("confidence")

  # bool is a subclass of int in Python.
  if isinstance(confidence,bool):
   return None

  if not isinstance(confidence,int):
   return None

  if decision not in VALID_DECISIONS:
   return None

  if regime not in VALID_REGIMES:
   return None

  if not 0 <= confidence <= 100:
   return None

  reason_code=str(
   obj.get("reason_code","")
  ).strip()

  reason=str(
   obj.get("reason","")
  ).strip()

  if not reason_code or not reason:
   return None

  return {
   "decision":decision,
   "confidence":confidence,
   "regime":regime,
   "reason_code":reason_code[:80],
   "reason":reason[:160],
  }


 @classmethod
 def _parse_response(cls,raw):
  """
  Conservative parser.

  It may repair JSON punctuation around the known schema,
  but it never invents a trading decision, confidence,
  regime, reason code, or reason.
  """

  if not isinstance(raw,str):
   raise ValueError("AI response is not text")

  text=raw.strip()

  if not text:
   raise ValueError("empty AI response")

  # Remove markdown fences only.
  if text.startswith("```"):
   text=re.sub(
    r'^```(?:json)?\s*',
    '',
    text,
    flags=re.I
   )

   text=re.sub(
    r'\s*```$',
    '',
    text
   ).strip()

  # First: strict JSON.
  try:
   obj=cls._validate_response(
    json.loads(text)
   )

   if obj:
    return obj,"STRICT"

  except (json.JSONDecodeError,TypeError,ValueError):
   pass

  repaired=text

  # Known malformed pattern:
  # decision":"ALLOW",...
  if re.match(
   r'^\s*decision"\s*:',
   repaired,
   re.I
  ):
   repaired='{"'+repaired

  # Known malformed pattern:
  # "decision":"ALLOW",...
  elif re.match(
   r'^\s*"decision"\s*:',
   repaired,
   re.I
  ):
   repaired="{"+repaired

  # Known malformed pattern:
  # decision:...
  elif re.match(
   r'^\s*decision\s*:',
   repaired,
   re.I
  ):
   pos=repaired.lower().find("decision")

   repaired=(
    '{"decision"'
    +repaired[pos+len("decision"):]
   )

  if (
   repaired.startswith("{")
   and not repaired.rstrip().endswith("}")
  ):
   repaired=repaired.rstrip()+"}"

  # Repair ONLY punctuation around known schema keys.
  known_keys=(
   "decision",
   "confidence",
   "regime",
   "reason_code",
   "reason",
  )

  for field in known_keys:

   repaired=re.sub(
    rf'(?P<prefix>[{{,])\s*{field}"\s*:',
    lambda m,f=field:
     m.group("prefix")+'"'+f+'":',
    repaired,
    flags=re.I
   )

   repaired=re.sub(
    rf'(?P<prefix>[{{,])\s*{field}\s*:',
    lambda m,f=field:
     m.group("prefix")+'"'+f+'":',
    repaired,
    flags=re.I
   )

  # Quote only known enum values.
  decisions="|".join(
   sorted(
    VALID_DECISIONS,
    key=len,
    reverse=True
   )
  )

  regimes="|".join(
   sorted(
    VALID_REGIMES,
    key=len,
    reverse=True
   )
  )

  repaired=re.sub(
   rf'("decision"\s*:\s*)'
   rf'({decisions})(?=\s*[,}}])',
   lambda m:
    m.group(1)+'"'
    +m.group(2).upper()+'"',
   repaired,
   flags=re.I
  )

  repaired=re.sub(
   rf'("regime"\s*:\s*)'
   rf'({regimes})(?=\s*[,}}])',
   lambda m:
    m.group(1)+'"'
    +m.group(2).upper()+'"',
   repaired,
   flags=re.I
  )

  # Try repaired JSON.
  try:
   obj=cls._validate_response(
    json.loads(repaired)
   )

   if obj:
    return obj,"REPAIRED"

  except (json.JSONDecodeError,TypeError,ValueError):
   pass

  # Last conservative option:
  # extract one complete {...} object.
  start=repaired.find("{")
  end=repaired.rfind("}")

  if start >= 0 and end > start:
   candidate=repaired[start:end+1]

   try:
    obj=cls._validate_response(
     json.loads(candidate)
    )

    if obj:
     return obj,"EXTRACTED"

   except (json.JSONDecodeError,TypeError,ValueError):
    pass

  raise ValueError("invalid AI JSON/schema")


 def _request_once(self,model,snapshot,timeout):
  payload=json.dumps({
   "model":model,
   "messages":[
    {
     "role":"system",
     "content":SYSTEM
    },
    {
     "role":"user",
     "content":json.dumps(
      snapshot,
      separators=(",",":")
     )
    }
   ],
   "temperature":0.0,
   "top_p":1.0,
   "max_tokens":512,
   "stream":False,
  }).encode()

  req=urllib.request.Request(
   BASE_URL,
   data=payload,
   headers={
    "Authorization":
     f"Bearer {self.api_key}",
    "Content-Type":
     "application/json"
   }
  )

  with urllib.request.urlopen(
   req,
   timeout=timeout
  ) as r:
   return json.loads(
    r.read().decode()
   )


 def _call_sync(
  self,
  model,
  snapshot,
  timeout
 ):
  started=time.monotonic()

  last_error=None

  # At most 2 attempts total.
  for attempt in (1,2):
   try:
    raw=self._request_once(
     model,
     snapshot,
     timeout
    )

    choice=raw["choices"][0]["message"]

    # Never parse reasoning_content.
    text=(
     choice.get("content") or ""
    ).strip()

    obj,parser_mode=self._parse_response(
     text
    )

    latency_ms=int(
     (time.monotonic()-started)*1000
    )

    obj["parser_mode"]=parser_mode
    obj["attempt"]=attempt

    return (
     obj,
     latency_ms,
     raw.get("usage",{})
    )

   except urllib.error.HTTPError as ex:
    last_error=ex

    status=int(
     getattr(ex,"code",0) or 0
    )

    # Retry only transient HTTP failures.
    if (
     attempt == 1
     and (
      status == 429
      or 500 <= status <= 599
     )
    ):
     time.sleep(
      self.retry_delay
     )
     continue

    raise

   except Exception as ex:
    last_error=ex
    raise

  raise last_error or RuntimeError(
   "AI request failed"
  )


 async def _call(
  self,
  model,
  snapshot,
  timeout
 ):
  # Allow room for one retry + retry delay.
  outer_timeout=(
   timeout*2
   +self.retry_delay
   +1.0
  )

  return await asyncio.wait_for(
   asyncio.to_thread(
    self._call_sync,
    model,
    snapshot,
    timeout
   ),
   timeout=outer_timeout
  )


 async def decide(
  self,
  symbol,
  snapshot
 ):
  if not self.enabled:
   await self.db.log(
    "AI_REJECT",
    symbol,
    reason="NVIDIA_API_KEY_MISSING"
   )

   return {
    "decision":"REJECT",
    "confidence":100,
    "reason_code":"AI_UNAVAILABLE",
    "reason":"NVIDIA_API_KEY missing",
    "model":None
   }

  prompt_hash=hashlib.sha256(
   json.dumps(
    snapshot,
    sort_keys=True,
    separators=(",",":")
   ).encode()
  ).hexdigest()[:16]

  started=time.monotonic()

  try:
   # DiffusionGemma primary.
   fast,latency,usage=await self._call(
    self.fast_model,
    snapshot,
    self.fast_timeout
   )

   fast.update({
    "model":self.fast_model,
    "latency_ms":latency,
    "prompt_hash":prompt_hash
   })

   await self.db.log(
    "AI_FAST_DECISION",
    symbol,
    **fast,
    usage=usage
   )

   # High-confidence primary decisions are final.
   if fast["confidence"] >= self.fast_accept:
    return fast

   # Low confidence escalates to deep model.
   deep,latency2,usage2=await self._call(
    self.deep_model,
    snapshot,
    self.deep_timeout
   )

   deep.update({
    "model":self.deep_model,
    "latency_ms":latency2,
    "prompt_hash":prompt_hash,
    "escalated_from":fast
   })

   await self.db.log(
    "AI_DEEP_DECISION",
    symbol,
    **deep,
    usage=usage2
   )

   # Deep uncertainty fails closed.
   if deep["confidence"] < self.deep_accept:
    deep["decision"]="REJECT"
    deep["reason_code"]="AI_LOW_CONFIDENCE"
    deep["reason"]="Deep AI confidence below acceptance threshold"

   return deep

  except Exception as ex:
   await self.db.log(
    "AI_ERROR",
    symbol,
    error=repr(ex),
    prompt_hash=prompt_hash,
    elapsed_ms=int(
     (time.monotonic()-started)*1000
    )
   )

   # Fail closed.
   return {
    "decision":"REJECT",
    "confidence":100,
    "reason_code":"AI_ERROR",
    "reason":str(ex)[:160],
    "model":None,
    "prompt_hash":prompt_hash
   }
