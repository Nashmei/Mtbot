import asyncio,json,re,time,urllib.parse,urllib.request
from datetime import datetime,timezone

GDELT="https://api.gdeltproject.org/api/v2/doc/doc"
TRUSTED_DOMAINS=("reuters.com","apnews.com","federalreserve.gov","ecb.europa.eu","bankofengland.co.uk","bls.gov","bea.gov")
HIGH_IMPACT=("interest rate","rate decision","cpi","inflation","nonfarm","non-farm","nfp","payroll","fomc","fed chair","ecb president","boe","gdp","pce","employment","unemployment")

class WebResearch:
 def __init__(self,db):
  self.db=db

 def _sync(self,query):
  q=re.sub(r"[^A-Za-z0-9 ._:/+-]"," ",str(query or ""))[:140].strip()
  if not q:return {"ok":False,"articles":[],"high_impact_recent":False,"query":""}
  domains=" OR ".join("domain:"+x for x in TRUSTED_DOMAINS)
  params=urllib.parse.urlencode({"query":"("+q+") ("+domains+")","mode":"ArtList","maxrecords":"8","format":"json","sort":"HybridRel"})
  req=urllib.request.Request(GDELT+"?"+params,headers={"User-Agent":"MTbot-AIResearch/1.0"})
  with urllib.request.urlopen(req,timeout=5) as r:data=json.loads(r.read().decode("utf-8","replace"))
  now=time.time();articles=[]
  for a in data.get("articles",[])[:8]:
   title=str(a.get("title",""))[:180];url=str(a.get("url",""))[:500];domain=str(a.get("domain","")).lower()
   if domain and not any(domain==d or domain.endswith("."+d) for d in TRUSTED_DOMAINS):continue
   seen=str(a.get("seendate",""));age=None
   try:
    dt=datetime.strptime(seen[:14],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc);age=max(0,(now-dt.timestamp())/60)
   except Exception:pass
   articles.append({"title":title,"domain":domain,"url":url,"seen":seen,"age_minutes":round(age,1) if age is not None else None})
  recent=any((x["age_minutes"] is not None and x["age_minutes"]<=30 and any(k in x["title"].lower() for k in HIGH_IMPACT)) for x in articles)
  return {"ok":True,"query":q,"articles":articles,"high_impact_recent":recent,"source":"GDELT trusted-domain search"}

 async def search(self,symbol,query):
  started=time.monotonic()
  try:
   out=await asyncio.wait_for(asyncio.to_thread(self._sync,query),timeout=6)
   await self.db.log("AI_WEB_RESEARCH",symbol,query=out.get("query"),articles=out.get("articles"),high_impact_recent=out.get("high_impact_recent"),latency_ms=int((time.monotonic()-started)*1000))
   return out
  except Exception as ex:
   await self.db.log("AI_WEB_RESEARCH_ERROR",symbol,query=str(query)[:160],error=repr(ex))
   return {"ok":False,"query":str(query)[:160],"articles":[],"high_impact_recent":False,"error":str(ex)[:160]}
