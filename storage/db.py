import asyncio, aiosqlite, json, time
from pathlib import Path
class DB:
 def __init__(self,path):
  self.path=path
  self._write_lock=asyncio.Lock()

 async def _connect(self):
  d=await aiosqlite.connect(self.path,timeout=5.0)
  await d.execute('PRAGMA busy_timeout=5000')
  await d.execute('PRAGMA foreign_keys=ON')
  return d

 async def init(self):
  Path(self.path).parent.mkdir(parents=True,exist_ok=True)
  d=await self._connect()
  try:
   await d.execute('PRAGMA journal_mode=WAL')
   await d.execute('PRAGMA synchronous=NORMAL')
   await d.executescript('''CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,ts REAL,event TEXT,symbol TEXT,details TEXT); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);''')
   await d.commit()
  finally:
   await d.close()

 async def log(self,event,symbol='',**details):
  async with self._write_lock:
   d=await self._connect()
   try:
    await d.execute('INSERT INTO audit(ts,event,symbol,details) VALUES(?,?,?,?)',(time.time(),event,symbol,json.dumps(details,ensure_ascii=False,default=str)))
    await d.commit()
   finally:
    await d.close()

 async def set(self,k,v):
  async with self._write_lock:
   d=await self._connect()
   try:
    await d.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(k,str(v)))
    await d.commit()
   finally:
    await d.close()

 async def get(self,k,default=None):
  d=await self._connect()
  try:
   async with d.execute('SELECT value FROM settings WHERE key=?',(k,)) as c:
    r=await c.fetchone(); return r[0] if r else default
  finally:
   await d.close()

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
  d=await self._connect()
  try:
   async with d.execute(sql,(reset_ts,window)) as cur:
    async for strategy,trades,points,avg_points in cur:
     out[str(strategy)]={'trades':int(trades or 0),'points':float(points or 0),'avg_points':float(avg_points or 0),'window':window}
  finally:
   await d.close()
  return out


 async def recent_audit(self,limit=100):
  limit=max(1,min(int(limit),500))
  rows=[]
  d=await self._connect()
  try:
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
  finally:
   await d.close()
  return rows


 async def closed_trades(self,limit=100,account_login=None):
  limit=max(1,min(int(limit),500))
  try: account_login=int(account_login) if account_login is not None else None
  except (TypeError,ValueError): account_login=None
  scan_limit=min(20000,max(1000,limit*8))
  rows=[]
  d=await self._connect()
  try:
   async with d.execute(
    """SELECT id,ts,event,symbol,details
       FROM audit
       WHERE event IN ('OPEN','TP','SL','PROTECTED_EXIT','TRAILING_EXIT','BREAKEVEN_EXIT','MAX_DURATION_EXIT','POSITION_CLOSED')
       ORDER BY id DESC LIMIT ?""",
    (scan_limit,)
   ) as cur:
    async for row_id,ts,event,symbol,details in cur:
     try:
      parsed=json.loads(details or '{}')
     except (TypeError,json.JSONDecodeError):
      parsed={'raw':str(details or '')}
     rows.append({
      'id':int(row_id),'ts':float(ts or 0),'event':str(event or ''),
      'symbol':str(symbol or ''),
      'details':parsed if isinstance(parsed,dict) else {'value':parsed},
     })
  finally:
   await d.close()
  opens={}
  closed=[]
  for row in reversed(rows):
   details=row.get('details') or {}
   ticket=details.get('ticket')
   try: ticket=int(ticket)
   except (TypeError,ValueError): ticket=0
   if row.get('event')=='OPEN' and ticket:
    tagged=details.get('account_login')
    try: tagged=int(tagged) if tagged is not None else None
    except (TypeError,ValueError): tagged=None
    # Account-scoped history is strict: never attribute legacy/untagged
    # rows to the currently logged-in MT5 account.
    if account_login is None or tagged==account_login:
     opens[ticket]=row
    continue
   if row.get('event') not in ('TP','SL','PROTECTED_EXIT','TRAILING_EXIT','BREAKEVEN_EXIT','MAX_DURATION_EXIT','POSITION_CLOSED'):
    continue
   close_tag=details.get('account_login')
   try: close_tag=int(close_tag) if close_tag is not None else None
   except (TypeError,ValueError): close_tag=None
   if account_login is not None and close_tag!=account_login:
    continue
   if account_login is not None and (not ticket or ticket not in opens):
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
