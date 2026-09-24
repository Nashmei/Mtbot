import aiosqlite, json, time
from pathlib import Path
class DB:
 def __init__(self,path): self.path=path
 async def init(self):
  Path(self.path).parent.mkdir(parents=True,exist_ok=True)
  async with aiosqlite.connect(self.path) as d:
   await d.executescript('''CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,ts REAL,event TEXT,symbol TEXT,details TEXT); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS push_devices(token TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,preferences TEXT NOT NULL DEFAULT '{}',updated_at REAL NOT NULL);'''); await d.commit()
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


 async def recent_audit(self,limit=100):
  limit=max(1,min(int(limit),500))
  rows=[]
  async with aiosqlite.connect(self.path) as d:
   async with d.execute(
    'SELECT id,ts,event,symbol,details FROM audit ORDER BY id DESC LIMIT ?',
    (limit,)
   ) as cur:
    async for row_id,ts,event,symbol,details in cur:
     try:
      parsed=json.loads(details or '{}')
     except (TypeError,json.JSONDecodeError):
      parsed={'raw':str(details or '')}
     rows.append({
      'id':int(row_id),
      'ts':float(ts or 0),
      'event':str(event or ''),
      'symbol':str(symbol or ''),
      'details':parsed if isinstance(parsed,dict) else {'value':parsed},
     })
  return rows


 async def closed_trades(self,limit=100):
  limit=max(1,min(int(limit),500))
  rows=await self.recent_audit(min(5000,max(500,limit*20)))
  opens={}
  closed=[]
  for row in reversed(rows):
   details=row.get('details') or {}
   ticket=details.get('ticket')
   try: ticket=int(ticket)
   except (TypeError,ValueError): ticket=0
   if row.get('event')=='OPEN' and ticket:
    opens[ticket]=row
    continue
   if row.get('event') not in ('TP','SL','POSITION_CLOSED'):
    continue
   opened=opens.get(ticket,{}) if ticket else {}
   open_details=opened.get('details') or {}
   closed.append({
    'id':int(row.get('id') or 0),
    'ticket':ticket,
    'symbol':str(row.get('symbol') or opened.get('symbol') or ''),
    'side':str(open_details.get('side') or ''),
    'strategy':str(open_details.get('strategy') or ''),
    'opened_at':float(opened.get('ts') or 0),
    'closed_at':float(row.get('ts') or 0),
    'entry':float(open_details.get('entry') or 0),
    'exit':float(details.get('exit_price') or 0),
    'sl':float(open_details.get('sl') or 0),
    'tp':float(open_details.get('tp') or 0),
    'volume':float(open_details.get('volume') or 0),
    'pnl':float(details.get('pnl') or 0),
    'result':str(row.get('event') or ''),
    'reason':str(details.get('reason') or ''),
   })
  return list(reversed(closed[-limit:]))

 async def upsert_push_device(self,token,enabled=True,preferences=None):
  token=str(token or '').strip()
  if not token:return
  payload=json.dumps(preferences or {},ensure_ascii=False,default=str)
  async with aiosqlite.connect(self.path) as d:
   await d.execute(
    'INSERT INTO push_devices(token,enabled,preferences,updated_at) VALUES(?,?,?,?) '
    'ON CONFLICT(token) DO UPDATE SET enabled=excluded.enabled,preferences=excluded.preferences,updated_at=excluded.updated_at',
    (token,1 if enabled else 0,payload,time.time())
   )
   await d.commit()

 async def active_push_devices(self):
  out=[]
  async with aiosqlite.connect(self.path) as d:
   async with d.execute('SELECT token,preferences FROM push_devices WHERE enabled=1') as cur:
    async for token,preferences in cur:
     try:prefs=json.loads(preferences or '{}')
     except Exception:prefs={}
     out.append({'token':str(token),'preferences':prefs if isinstance(prefs,dict) else {}})
  return out
