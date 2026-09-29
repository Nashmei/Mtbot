import asyncio, json, re, time, urllib.parse, urllib.request
from datetime import datetime, timezone

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
TRUSTED_DOMAINS = (
    "reuters.com", "apnews.com", "federalreserve.gov", "ecb.europa.eu",
    "bankofengland.co.uk", "bls.gov", "bea.gov",
)

HIGH_IMPACT = {
    "interest rate": 3, "rate decision": 3, "fomc": 3, "fed chair": 3,
    "ecb president": 3, "boe": 2, "cpi": 3, "inflation": 2, "pce": 2,
    "nonfarm": 3, "non-farm": 3, "nfp": 3, "payroll": 3,
    "gdp": 2, "employment": 1, "unemployment": 2,
}


def _severity(title):
    low = title.lower()
    score = sum(w for k, w in HIGH_IMPACT.items() if k in low)
    if score >= 3:
        return "HIGH"
    if score >= 2:
        return "MEDIUM"
    if score >= 1:
        return "LOW"
    return "NONE"


class WebResearch:
    def __init__(self, db):
        self.db = db

    def _sync(self, query):
        q = re.sub(r"[^A-Za-z0-9 ._:/+-]", " ", str(query or ""))[:140].strip()
        if not q:
            return {"ok": False, "articles": [], "high_impact_recent": False,
                    "max_severity": "NONE", "query": ""}

        domains = " OR ".join("domain:" + x for x in TRUSTED_DOMAINS)
        params = urllib.parse.urlencode({
            "query": "(" + q + ") (" + domains + ")",
            "mode": "ArtList",
            "maxrecords": "8",
            "format": "json",
            "sort": "HybridRel",
        })
        req = urllib.request.Request(
            GDELT + "?" + params,
            headers={"User-Agent": "MTbot-AIResearch/2.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))

        now = time.time()
        articles = []
        max_sev = "NONE"
        recent_high = False
        for a in data.get("articles", [])[:8]:
            title = str(a.get("title", ""))[:180]
            url = str(a.get("url", ""))[:500]
            domain = str(a.get("domain", "")).lower()
            if domain and not any(domain == d or domain.endswith("." + d)
                                  for d in TRUSTED_DOMAINS):
                continue
            age = None
            seen = str(a.get("seendate", ""))
            try:
                dt = datetime.strptime(seen[:14], "%Y%m%d%H%M%S").replace(
                    tzinfo=timezone.utc
                )
                age = max(0, (now - dt.timestamp()) / 60)
            except Exception:
                pass
            sev = _severity(title)
            if sev == "HIGH" and max_sev != "HIGH":
                max_sev = "HIGH"
            elif sev == "MEDIUM" and max_sev not in ("HIGH",):
                max_sev = "MEDIUM"
            elif sev == "LOW" and max_sev == "NONE":
                max_sev = "LOW"
            if age is not None and age <= 45 and sev == "HIGH":
                recent_high = True
            articles.append({
                "title": title, "domain": domain, "url": url, "seen": seen,
                "age_minutes": round(age, 1) if age is not None else None,
                "severity": sev,
            })

        return {
            "ok": True,
            "query": q,
            "articles": articles,
            "high_impact_recent": recent_high,
            "max_severity": max_sev,
            "source": "GDELT trusted-domain search",
        }

    async def search(self, symbol, query):
        started = time.monotonic()
        try:
            out = await asyncio.wait_for(
                asyncio.to_thread(self._sync, query), timeout=6
            )
            await self.db.log(
                "AI_WEB_RESEARCH", symbol,
                query=out.get("query"),
                articles=out.get("articles"),
                high_impact_recent=out.get("high_impact_recent"),
                max_severity=out.get("max_severity"),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return out
        except Exception as ex:
            await self.db.log(
                "AI_WEB_RESEARCH_ERROR", symbol,
                query=str(query)[:160], error=repr(ex),
            )
            return {"ok": False, "query": str(query)[:160], "articles": [],
                    "high_impact_recent": False, "max_severity": "NONE",
                    "error": str(ex)[:160]}
