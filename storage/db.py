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

 CLOSE_EVENTS=('TP','SL','PROTECTED_EXIT','TRAILING_EXIT','BREAKEVEN_EXIT',
               'TP1_PARTIAL_EXIT','MAX_DURATION_EXIT','POSITION_CLOSED','STOP_EXIT')

 async def closed_trade_metrics(self,login=None,window=200):
  """Aggregate realized performance by strategy x symbol x regime.

  Rationale: strategy validation is per combination, not per strategy only.
  MAE/MFE come from ``manage()`` bookkeeping on the closing audit row.
  """
  window=max(1,min(int(window),2000))
  try: login=int(login) if login is not None else None
  except (TypeError,ValueError): login=None
  rows=[]
  d=await self._connect()
  try:
   async with d.execute(
    """SELECT id,ts,event,symbol,details FROM audit
       WHERE event IN ('OPEN','TP','SL','PROTECTED_EXIT','TRAILING_EXIT','BREAKEVEN_EXIT',
                       'TP1_PARTIAL_EXIT','MAX_DURATION_EXIT','POSITION_CLOSED','STOP_EXIT')
       ORDER BY id DESC LIMIT ?""",
    (min(60000,max(2000,window*20)),)
   ) as cur:
    async for row_id,ts,event,symbol,details in cur:
     try: parsed=json.loads(details or '{}')
     except (TypeError,json.JSONDecodeError): parsed={}
     rows.append((int(row_id),float(ts or 0),str(event or ''),str(symbol or ''),parsed))
  finally:
   await d.close()
  opens={}
  groups={}
  for row_id,ts,event,symbol,details in reversed(rows):
   ticket=details.get('ticket')
   try: ticket=int(ticket)
   except (TypeError,ValueError): ticket=0
   if event=='OPEN' and ticket:
    tagged=details.get('account_login')
    try: tagged=int(tagged) if tagged is not None else None
    except (TypeError,ValueError): tagged=None
    if login is None or tagged==login:
     opens[ticket]=(symbol,details)
    continue
   if event not in self.CLOSE_EVENTS:
    continue
   # Only rows that carry a realized P/L represent a closed trade.  Auxiliary
   # rows such as MAX_DURATION_EXIT or STOP_EXIT are bookkeeping and must not
   # be counted as an extra zero-P/L trade for the same ticket.
   if details.get('pnl') is None:
    continue
   if event=='STOP_EXIT':
    if login is not None:
     close_tag=details.get('account_login')
     try: close_tag=int(close_tag) if close_tag is not None else None
     except (TypeError,ValueError): close_tag=None
     if close_tag is not None and close_tag!=login:
      continue
   opened=opens.get(ticket)
   if login is not None and opened is None:
    continue
   open_details=(opened[1] if opened else {})
   strategy=str(open_details.get('strategy') or details.get('strategy') or 'unknown')
   regime=str(open_details.get('regime') or details.get('regime') or 'UNKNOWN').upper()
   symbol=str(symbol or (opened[0] if opened else ''))
   pnl=float(details.get('pnl') or 0)
   key=(strategy,symbol,regime)
   g=groups.setdefault(key,{'trades':0,'wins':0,'losses':0,'gross_win':0.0,
                            'gross_loss':0.0,'net':0.0,'r_sum':0.0,'r_count':0,
                            'mfe':[],'mae':[]})
   g['trades']+=1
   if pnl>0:
    g['wins']+=1;g['gross_win']+=pnl
   elif pnl<0:
    g['losses']+=1;g['gross_loss']+=abs(pnl)
   g['net']+=pnl
   try:
    r=details.get('r_multiple')
    if r is not None:
     g['r_sum']+=float(r);g['r_count']+=1
   except (TypeError,ValueError): pass
   try:
    mfe=details.get('mfe_r');mae=details.get('mae_r')
    if mfe is not None: g['mfe'].append(float(mfe))
    if mae is not None: g['mae'].append(float(mae))
   except (TypeError,ValueError): pass
  out=[]
  for (strategy,symbol,regime),g in groups.items():
   trades=g['trades']
   pf=(g['gross_win']/g['gross_loss']) if g['gross_loss']>0 else (999.0 if g['gross_win']>0 else 0.0)
   out.append({
    'strategy':strategy,'symbol':symbol,'regime':regime,
    'trades':trades,'wins':g['wins'],'losses':g['losses'],
    'win_rate':(g['wins']/trades*100.0) if trades else 0.0,
    'net':g['net'],'profit_factor':pf,
    'expectancy':(g['net']/trades) if trades else 0.0,
    'avg_r':(g['r_sum']/g['r_count']) if g['r_count'] else None,
    'avg_win':(g['gross_win']/g['wins']) if g['wins'] else 0.0,
    'avg_loss':(g['gross_loss']/g['losses']) if g['losses'] else 0.0,
    'avg_mfe_r':(sum(g['mfe'])/len(g['mfe'])) if g['mfe'] else None,
    'avg_mae_r':(sum(g['mae'])/len(g['mae'])) if g['mae'] else None,
   })
  out.sort(key=lambda row:(row['net'],row['trades']),reverse=True)
  return out[:window]
