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
  window=max(1,min(int(window),200))
  try:
   reset_ts=float(await self.get('strategy_performance_reset_ts',0) or 0)
  except (TypeError,ValueError):
   reset_ts=0.0
  sql='''WITH opens AS (
   SELECT
    id,ts,symbol,
    json_extract(details,'$.strategy') AS strategy,
    LEAD(ts) OVER (PARTITION BY symbol ORDER BY ts) AS next_open_ts
   FROM audit
   WHERE event='OPEN'
     AND ts>=?
     AND json_extract(details,'$.strategy') IS NOT NULL
  ),
  paired AS (
   SELECT
    o.strategy,o.ts,
    (
     SELECT c.id
     FROM audit c
     WHERE c.symbol=o.symbol
       AND c.ts>o.ts
       AND c.event IN ('TP','SL','POSITION_CLOSED')
       AND (o.next_open_ts IS NULL OR c.ts<o.next_open_ts)
     ORDER BY c.ts ASC
     LIMIT 1
    ) AS close_id
   FROM opens o
  ),
  scored AS (
   SELECT
    p.strategy,p.ts,
    CASE
     WHEN c.event='TP' THEN 3.0
     WHEN CAST(COALESCE(json_extract(c.details,'$.pnl'),0) AS REAL)>0 THEN 1.0
     WHEN CAST(COALESCE(json_extract(c.details,'$.pnl'),0) AS REAL)=0 THEN 0.0
     ELSE -1.0
    END AS points,
    ROW_NUMBER() OVER (PARTITION BY p.strategy ORDER BY p.ts DESC) AS rn
   FROM paired p
   JOIN audit c ON c.id=p.close_id
   WHERE p.strategy IS NOT NULL
  )
  SELECT
   strategy,
   COUNT(*) AS trades,
   COALESCE(SUM(points),0) AS points,
   COALESCE(AVG(points),0) AS avg_points
  FROM scored
  WHERE rn<=?
  GROUP BY strategy'''
  out={}
  async with aiosqlite.connect(self.path) as d:
   async with d.execute(sql,(reset_ts,window)) as cur:
    async for strategy,trades,points,avg_points in cur:
     out[str(strategy)]={
      'trades':int(trades or 0),
      'points':float(points or 0),
      'avg_points':float(avg_points or 0),
      'window':window,
     }
  return out
