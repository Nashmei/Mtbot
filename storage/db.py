import aiosqlite, json, time
from pathlib import Path
class DB:
 def __init__(self,path): self.path=path
 async def init(self):
  Path(self.path).parent.mkdir(parents=True,exist_ok=True)
  async with aiosqlite.connect(self.path) as d:
   await d.executescript('''CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,ts REAL,event TEXT,symbol TEXT,details TEXT); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);'''); await d.commit()
 async def log(self,event,symbol='',**details):
  async with aiosqlite.connect(self.path) as d:
   await d.execute('INSERT INTO audit(ts,event,symbol,details) VALUES(?,?,?,?)',(time.time(),event,symbol,json.dumps(details,ensure_ascii=False,default=str))); await d.commit()
 async def set(self,k,v):
  async with aiosqlite.connect(self.path) as d: await d.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(k,str(v))); await d.commit()
 async def get(self,k,default=None):
  async with aiosqlite.connect(self.path) as d:
   async with d.execute('SELECT value FROM settings WHERE key=?',(k,)) as c:
    r=await c.fetchone(); return r[0] if r else default

 async def reset_strategy_performance(self,at_ts=None):
  reset_ts=float(time.time() if at_ts is None else at_ts)
  await self.set('strategy_performance_reset_ts',reset_ts)
  await self.log('STRATEGY_PERFORMANCE_RESET',reset_ts=reset_ts)
  return reset_ts

 async def strategy_performance(self,window=50):
  """Recent realized performance by AI strategy, paired by MT5 ticket."""
  window=max(1,min(int(window),200))
  try: reset_ts=float(await self.get('strategy_performance_reset_ts',0) or 0)
  except (TypeError,ValueError): reset_ts=0.0
  sql="""WITH opens AS (
   SELECT ts,
          CAST(json_extract(details,'$.ticket') AS TEXT) AS ticket,
          json_extract(details,'$.strategy') AS strategy
   FROM audit
   WHERE event='OPEN' AND ts>=?
     AND json_extract(details,'$.ticket') IS NOT NULL
     AND json_extract(details,'$.strategy') IS NOT NULL
  ),
  closes AS (
   SELECT id,ts,event,
          CAST(json_extract(details,'$.ticket') AS TEXT) AS ticket,
          CAST(COALESCE(json_extract(details,'$.pnl'),0) AS REAL) AS pnl
   FROM audit
   WHERE event IN ('TP','SL','PROTECTED_EXIT','TRAILING_EXIT','BREAKEVEN_EXIT','POSITION_CLOSED')
     AND json_extract(details,'$.ticket') IS NOT NULL
     AND json_extract(details,'$.pnl') IS NOT NULL
  ),
  paired AS (
   SELECT o.strategy,o.ts,c.event,c.pnl,
          ROW_NUMBER() OVER (PARTITION BY o.ticket ORDER BY c.ts ASC,c.id ASC) AS close_rn
   FROM opens o JOIN closes c ON c.ticket=o.ticket AND c.ts>=o.ts
  ),
  scored AS (
   SELECT strategy,ts,
          CASE WHEN pnl>0 THEN 1.0 WHEN pnl=0 THEN 0.0 ELSE -1.0 END AS points,
          ROW_NUMBER() OVER (PARTITION BY strategy ORDER BY ts DESC) AS rn
   FROM paired WHERE close_rn=1
  )
  SELECT strategy,COUNT(*),COALESCE(SUM(points),0),COALESCE(AVG(points),0)
  FROM scored WHERE rn<=? GROUP BY strategy"""
  out={}
  async with aiosqlite.connect(self.path) as d:
   async with d.execute(sql,(reset_ts,window)) as cur:
    async for strategy,trades,points,avg_points in cur:
     out[str(strategy)]={'trades':int(trades or 0),'points':float(points or 0),'avg_points':float(avg_points or 0),'window':window}
  return out
