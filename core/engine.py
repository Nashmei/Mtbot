import asyncio,time,traceback
import MetaTrader5 as mt5
from .config import settings
from .models import TradeState,Side
from .analyzer import Analyzer
from .risk import Risk
from .ai_advisor import AIAdvisor
from .ai_native_selector import AINativeSelector
from .web_research import WebResearch

def _signal_key(strategy,side_value,signal_bar):
 return (strategy,side_value,signal_bar)

class Engine:
 def __init__(self,gw,db,notify):
  self.gw=gw
  self.db=db
  self.notify=notify
  self.running=False

  # إعدادات قابلة للتحكم من Telegram
  self.symbols=['EURUSD']
  self.symbol='EURUSD'
  self.rr=3.0
  self.risk_pct=0.25
  self.min_confidence=75.0
  self.protection_pct=45.0
  self.trailing_gap_pct=5.0
  self.max_trade_minutes=10.0
  self.reentry_cooldown_seconds=120.0
  self.last_close_by_symbol={}
  self.blocked_signal_by_symbol={}
  self.max_positions=1
  self.max_consecutive_losses=3
  self.daily_loss_limit_pct=2.0
  self.consecutive_losses=0
  self.loss_limit_notified=False
  self.daily_loss_notified=False

  # كل ticket له TradeState مستقل
  self.trades={}
  self.trade=None

  # حالة مستقلة لكل رمز
  self.last_entry_by_symbol={}
  self.last_analysis_by_symbol={}
  self.scan_index=0

  self.last_entry=0
  self.last_analysis_key=None
  self.an=Analyzer()
  self.risk=Risk()
  self.ai=AIAdvisor(db)
  # AI branch experiment: legacy strategy signal generation is bypassed.
  self.ai_native=AINativeSelector(db)
  self.web_research=WebResearch(db)
  self.ai_native_only=True
  self.reject_log_at={}
  self.reject_log_interval=60.0
  self.trade_alert_meta={}
  self.execution_notice_once=set()
  self.scan_count=0
  self.last_cycle_seconds=0.0
  self.last_cycle_at=0.0
  self.last_cycle_log_at=0.0
  self.strategy_performance={}
  self.strategy_performance_window=50
  self.strategy_performance_refreshed_at=0.0

  # الإعدادات المحفوظة تُحمّل لاحقاً داخل سياق async


 async def _account_key(self,key,login=None):
  if login is None and self.gw is not None:
   account=self.gw.account()
   raw_login=getattr(account,'login',None) if account else None
   login=int(raw_login) if raw_login is not None else None
  return f'account:{int(login)}:{key}' if login is not None else key

 async def save_setting(self,key,value):
  db_key=await self._account_key(key)
  if db_key is None:
   raise RuntimeError('MT5 account is not connected')
  await self.db.set(db_key,value)

 async def load_settings(self,login=None,migrate_legacy=False):
  # Every MT5 account owns an independent Telegram-managed profile.
  # A newly linked account starts from safe built-in defaults.
  if login is None and self.gw is not None:
   account=self.gw.account()
   raw_login=getattr(account,'login',None) if account else None
   login=int(raw_login) if raw_login is not None else None
  # Compatibility path for isolated/unit use without an MT5 account:
  # load legacy global DB settings, but never use this path for a connected account.
  if login is None:
   legacy={
    'rr':('rr',float),'risk_pct':('risk_pct',float),
    'min_confidence':('min_confidence',float),
    'protection_pct':('protection_pct',float),
    'trailing_gap_pct':('trailing_gap_pct',float),
    'max_trade_minutes':('max_trade_minutes',float),
    'max_positions':('max_positions',int),
    'max_consecutive_losses':('max_consecutive_losses',int),
    'daily_loss_limit_pct':('daily_loss_limit_pct',float),
    'consecutive_losses':('consecutive_losses',int),
   }
   for key,(attr,cast) in legacy.items():
    raw=await self.db.get(key)
    if raw is None:
     continue
    try:
     value=cast(raw)
     if key=='consecutive_losses': value=max(0,value)
     setattr(self,attr,value)
    except (TypeError,ValueError):
     pass
   await self._refresh_strategy_performance(force=True)
   return

  defaults={
   'rr':3.0,'risk_pct':0.25,'min_confidence':75.0,
   'protection_pct':45.0,'trailing_gap_pct':5.0,
   'max_trade_minutes':10.0,'max_positions':1,
   'max_consecutive_losses':3,'daily_loss_limit_pct':2.0,
   'consecutive_losses':0,
  }
  attrs={
   'rr':'rr','risk_pct':'risk_pct','min_confidence':'min_confidence',
   'protection_pct':'protection_pct','trailing_gap_pct':'trailing_gap_pct',
   'max_trade_minutes':'max_trade_minutes','max_positions':'max_positions',
   'max_consecutive_losses':'max_consecutive_losses',
   'daily_loss_limit_pct':'daily_loss_limit_pct',
   'consecutive_losses':'consecutive_losses',
  }
  int_keys={'max_positions','max_consecutive_losses','consecutive_losses'}
  for key,default in defaults.items():
   scoped=await self._account_key(key,login)
   raw=await self.db.get(scoped)
   if raw is None and migrate_legacy:
    raw=await self.db.get(key)
    if raw is not None:
     await self.db.set(scoped,raw)
   if raw is None:
    raw=default
    await self.db.set(scoped,raw)
   try:
    value=int(raw) if key in int_keys else float(raw)
    if key=='consecutive_losses': value=max(0,value)
    setattr(self,attrs[key],value)
   except (TypeError,ValueError):
    setattr(self,attrs[key],default)

  symbols_key=await self._account_key('symbols',login)
  raw_symbols=await self.db.get(symbols_key)
  if raw_symbols is None and migrate_legacy:
   raw_symbols=await self.db.get('symbols')
   if raw_symbols is not None:
    await self.db.set(symbols_key,raw_symbols)
  if raw_symbols:
   try:
    import json
    saved=json.loads(raw_symbols)
    if isinstance(saved,list) and saved:
     self.symbols=[str(x) for x in saved]
     self.symbol=self.symbols[0]
   except Exception:
    pass
  else:
   self.symbols=['EURUSD'];self.symbol='EURUSD'
   import json
   await self.db.set(symbols_key,json.dumps(self.symbols))

  await self._refresh_strategy_performance(force=True)


 async def _refresh_strategy_performance(self,force=False):
  now=time.monotonic()
  if not force and now-self.strategy_performance_refreshed_at<60.0:
   return
  try:
   self.strategy_performance=await self.db.strategy_performance(self.strategy_performance_window)
   self.strategy_performance_refreshed_at=now
  except Exception as ex:
   self.strategy_performance_refreshed_at=now
   await self.db.log('STRATEGY_PERFORMANCE_ERROR',error=repr(ex))


 async def status(self):
  a=self.gw.account()
  if not a:
   return 'MT5 غير متصل'

  symbols=', '.join(self.symbols) if self.symbols else 'لا يوجد'
  state='🟢 يعمل' if self.running else '⚪ متوقف'

  return (
   f'{state} | 🔒 تجريبي\n'
   f'🔄 دورات المحرك: {self.scan_count} | آخر مدة: {self.last_cycle_seconds:.2f}ث\n'
   f'💱 الأزواج: {symbols}\n'
   f'📂 المراكز: {len(self.trades)} / {self.max_positions}\n'
   f'⚠️ المخاطرة: {self.risk_pct:g}% لكل صفقة\n'
   f'🛡 الحماية: {self.protection_pct:g}%\n'
   f'⏱ حد الصفقة: {self.max_trade_minutes:g} دقيقة\n'
   f'❌ الخسائر المتتالية: {self.consecutive_losses} / {self.max_consecutive_losses}\n'
   f'📉 حد Equity اليومي: {self.daily_loss_limit_pct:g}%'
   f'{" (معطل)" if self.daily_loss_limit_pct<=0 else ""}\n'
   f'⚖️ العائد/المخاطرة: 1:{self.rr:g}\n'
   f'💰 Equity: {a.equity:.2f} {a.currency}'
  )

 async def start(self):
  import math
  if not (math.isfinite(self.risk_pct) and 0<self.risk_pct<=50
          and math.isfinite(self.rr) and .5<=self.rr<=10
          and 1<=self.max_positions<=10
          and math.isfinite(self.daily_loss_limit_pct)
          and 0<=self.daily_loss_limit_pct<=100):
   await self.notify('⚠️ إعدادات المخاطرة أو العائد أو حد المراكز غير صالحة.')
   return False
  account=self.gw.account()
  if not account or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO:
   await self.notify('🔒 يلزم اتصال بحساب MT5 تجريبي قبل التشغيل.')
   return False
  if not await self._daily_entry_allowed(account):
   return False
  permissions=self.gw.algo_status()
  if not all(permissions.get(key) for key in ('connected','trade_allowed','account_trade_allowed','trade_expert')):
   await self.notify('⚠️ اتصال MT5 أو صلاحية Algo Trading غير جاهزة. افحص الجاهزية أولاً.')
   return False
  positions=self.gw.positions()
  if positions is None:
   await self.notify('⚠️ تعذر قراءة مراكز MT5؛ لن يبدأ المحرك.')
   return False
  old=[p for p in positions if getattr(p,'magic',0)==4009 and p.ticket not in self.trades]
  if old:
   await self.notify('⚠️ توجد صفقة قديمة للبوت غير متتبعة. أغلقها يدويًا قبل التشغيل.')
   return False

  if self.running:
   await self.notify('ℹ️ البوت يعمل بالفعل.')
   return True

  if not self.symbols:
   await self.notify('⚠️ اختر زوجاً واحداً على الأقل قبل التشغيل.')
   return False

  self.running=True
  self.last_analysis_by_symbol={}

  await self.db.log(
   'BOT_STARTED',
   symbols=self.symbols,
   risk_pct=self.risk_pct,
   protection_pct=self.protection_pct,
   max_positions=self.max_positions,
   max_consecutive_losses=self.max_consecutive_losses,
   daily_loss_limit_pct=self.daily_loss_limit_pct
  )

  await self.notify(
   f'▶️ تم تشغيل البوت\n'
   f'💱 مراقبة: {", ".join(self.symbols)}\n'
   f'📂 حد المراكز: {self.max_positions}\n'
   f'⚠️ المخاطرة: {self.risk_pct:g}%\n'
   f'🛡 الحماية: {self.protection_pct:g}%'
  )

  self.loop_task=asyncio.create_task(self.loop())
  return True

 async def stop(self):
  # منع أي دخول جديد فوراً
  self.running=False

  # دعم مؤقت للصفقة القديمة أثناء مرحلة التحويل
  managed=list(self.trades.values())
  if self.trade and self.trade.ticket not in self.trades:
   managed.append(self.trade)

  failed=0
  closed=0

  for t in managed:
   pos=self.gw.position_by_ticket(t.ticket)

   if not pos:
    self.trades.pop(t.ticket,None)
    if self.trade and self.trade.ticket==t.ticket:
     self.trade=None
    continue

   res=self.gw.close(pos)

   if res and res.retcode in (
    mt5.TRADE_RETCODE_DONE,
    mt5.TRADE_RETCODE_DONE_PARTIAL
   ):
    remaining=self.gw.position_by_ticket(t.ticket)
    if remaining:
     failed+=1
     await self.db.log('STOP_EXIT_PARTIAL',t.symbol,ticket=t.ticket,remaining_volume=remaining.volume)
     await self.notify(
      f'⚠️ إغلاق جزئي للمركز {t.ticket} ({t.symbol}). '
      f'المتبقي {remaining.volume:g} لوت؛ افحصه في MT5.'
     )
     continue
    closed+=1
    await asyncio.sleep(.3)

    exit_price=float(getattr(res,'price',0) or 0)
    pnl=None

    deals=self.gw.history_deals_by_position(t.ticket)
    for d in deals:
     if getattr(d,'entry',None) in (
      getattr(mt5,'DEAL_ENTRY_OUT',1),
      getattr(mt5,'DEAL_ENTRY_OUT_BY',3)
     ):
      exit_price=float(getattr(d,'price',exit_price) or exit_price)
      pnl=(float(getattr(d,'profit',0) or 0)
           +float(getattr(d,'swap',0) or 0)
           +float(getattr(d,'commission',0) or 0))

    await self.db.log(
     'STOP_EXIT',t.symbol,
     ticket=t.ticket,exit_price=exit_price,pnl=pnl
    )

    pnl_text=f'{pnl:.2f}' if pnl is not None else 'بانتظار سجل MT5'
    await self.notify(
     f'⏹ إغلاق بسبب إيقاف البوت — {t.symbol}\n'
     f'🎫 المركز: {t.ticket}\n'
     f'🚪 سعر الخروج: {exit_price}\n'
     f'💰 الربح/الخسارة: {pnl_text}'
    )

    self.trades.pop(t.ticket,None)
    if self.trade and self.trade.ticket==t.ticket:
     self.trade=None

   else:
    failed+=1
    await self.db.log(
     'STOP_EXIT_FAILED',t.symbol,
     ticket=t.ticket,result=str(res)
    )
    await self.notify(
     f'⚠️ فشل إغلاق المركز — {t.symbol}\n'
     f'🎫 المركز: {t.ticket}\n'
     f'📡 MT5: {getattr(res,"comment","لا توجد استجابة")}\n'
     f'⚠️ تحقق منه يدوياً في MT5.'
    )

  await self.db.log(
   'BOT_STOPPED',
   closed_positions=closed,
   failed_positions=failed
  )

  if failed:
   await self.notify(
    f'⏹ تم إيقاف فتح الصفقات الجديدة.\n'
    f'✅ أُغلق: {closed}\n'
    f'⚠️ تعذر إغلاق: {failed}'
   )
  else:
   await self.notify(f'⏹ تم إيقاف البوت | المراكز المغلقة: {closed}')

 async def loop(self):
  while self.running:
   started=time.monotonic()
   try:
    await self.step()
    self.scan_count+=1
    self.last_cycle_seconds=time.monotonic()-started
    self.last_cycle_at=time.time()
    if self.last_cycle_at-self.last_cycle_log_at>=60:
     self.last_cycle_log_at=self.last_cycle_at
     await self.db.log('ENGINE_CYCLE',duration_seconds=round(self.last_cycle_seconds,3),scan_count=self.scan_count)
   except Exception as e:
    self.running=False
    try: await self.db.log('ENGINE_ERROR',self.symbol,error=repr(e),traceback=traceback.format_exc())
    finally: await self.notify('🚨 توقف المحرك بسبب خطأ. افحص سجل ENGINE_ERROR ومراكز MT5 قبل إعادة التشغيل.')
    return
   await asyncio.sleep(settings.poll_interval_ms/1000)

 async def _daily_entry_allowed(self,account):
  """Persist a local-day equity baseline across bot restarts."""
  from datetime import date
  import math
  equity=float(getattr(account,'equity',0) or 0)
  if not math.isfinite(equity) or equity<=0:
   return False
  if self.daily_loss_limit_pct<=0:
   return True
  today=date.today().isoformat()
  day_key=await self._account_key('daily_equity_date')
  baseline_key=await self._account_key('daily_equity_baseline')
  saved_day=await self.db.get(day_key)
  baseline=float(await self.db.get(baseline_key,0) or 0)
  if saved_day!=today or not math.isfinite(baseline) or baseline<=0:
   baseline=equity
   await self.db.set(baseline_key,baseline)
   await self.db.set(day_key,today)
   self.daily_loss_notified=False
  allowed=equity>baseline*(1-self.daily_loss_limit_pct/100.0)
  if not allowed and not self.daily_loss_notified:
   self.daily_loss_notified=True
   await self.db.log('DAILY_EQUITY_LIMIT',equity=equity,baseline=baseline,limit_pct=self.daily_loss_limit_pct)
   await self.notify('🛑 توقف الدخول: حد انخفاض Equity اليومي. تستمر إدارة المراكز المفتوحة.')
  return allowed

 async def step(self):
  account=self.gw.account()
  if not account or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO:
   self.running=False; await self.notify('🔒 الحساب التجريبي فقط.'); return

  positions=self.gw.positions()
  if positions is None:
   self.running=False
   await self.notify('⚠️ تعذر التحقق من مراكز MT5؛ أُوقف الدخول حتى استعادة الاتصال.')
   return
  unknown=[p for p in positions if getattr(p,'magic',0)==4009 and p.ticket not in self.trades]
  if unknown:
   self.running=False
   await self.db.log('UNMANAGED_POSITION',tickets=[p.ticket for p in unknown])
   await self.notify('🚨 يوجد مركز للبوت غير متتبع. أُوقف المحرك؛ افحص المراكز في MT5.')
   return

  # إدارة كل الصفقات المفتوحة أولاً
  for t in list(self.trades.values()):
   info=self.gw.info(t.symbol); tick=self.gw.tick(t.symbol)
   if info and tick: await self.manage(t,tick,info)

  await self._refresh_strategy_performance()

  # لا دخول جديد عند بلوغ الحدود
  if not await self._daily_entry_allowed(account):
   return
  if len(self.trades)>=self.max_positions:return
  if self.max_consecutive_losses > 0 and self.consecutive_losses>=self.max_consecutive_losses:
   if not self.loss_limit_notified:
    self.loss_limit_notified=True
    await self.notify(f'🛑 توقف الدخول: {self.consecutive_losses} خسائر متتالية.')
   return
  self.loss_limit_notified=False
  if not self.symbols:return

  # فحص كل الرموز المختارة في كل دورة
  for symbol in list(self.symbols):
   if len(self.trades)>=self.max_positions:
    break
   await self._scan_symbol(symbol,account)

 async def _log_reject(self,event,symbol,**details):
  # Keep diagnostics useful without writing the same rejection every scan.
  key=(event,symbol,details.get('reason',''))
  now=time.time()
  if now-self.reject_log_at.get(key,0)<self.reject_log_interval:
   return
  self.reject_log_at[key]=now
  await self.db.log(event,symbol,**details)

 async def _scan_symbol(self,symbol,account):
   # صفقة واحدة كحد أقصى لكل رمز
   if any(t.symbol==symbol for t in self.trades.values()):return
   # No new entries around the daily rollover when spreads commonly widen.
   # Server is configured to Asia/Riyadh; use local server time intentionally.
   from datetime import datetime
   now_local=datetime.now()
   mins=now_local.hour*60+now_local.minute
   if mins>=23*60+45 or mins<30:
    await self._log_reject('TIME_REJECT',symbol,reason='DAILY_ROLLOVER_2345_0030')
    return
   # Avoid stacking the same USD directional exposure across correlated FX pairs.
   usd_group={'EURUSD','GBPUSD','AUDUSD','NZDUSD'}
   info=self.gw.info(symbol); tick=self.gw.tick(symbol)
   if not info or not tick or not info.point or tick.bid<=0 or tick.ask<=tick.bid:
    # A transient Wine/MT5 quote can briefly expose bid==ask. Retry once,
    # but never weaken the strict quote validity rule.
    await asyncio.sleep(.2)
    info=self.gw.info(symbol); tick=self.gw.tick(symbol)
    if not info or not tick or not info.point or tick.bid<=0 or tick.ask<=tick.bid:
     await self._log_reject(
      'SCAN_REJECT',symbol,reason='INVALID_MARKET_DATA',
      info=bool(info),tick=bool(tick),
      point=float(getattr(info,'point',0) or 0) if info else 0,
      bid=float(getattr(tick,'bid',0) or 0) if tick else 0,
      ask=float(getattr(tick,'ask',0) or 0) if tick else 0,
      last_error=repr(mt5.last_error()),
     )
     return

   # Under Wine/MT5, symbol_info_tick() can expose a terminal-local timestamp
   # (for example UTC+3) even though copy_ticks_range() returns Unix UTC.
   # Use the history tick stream as the authoritative freshness clock and keep
   # symbol_info_tick() only for the live bid/ask used by spread/execution.
   ticks=self.gw.ticks(symbol)
   if ticks is None or len(ticks)<80:
    await self._log_reject('SCAN_REJECT',symbol,reason='INSUFFICIENT_TICKS')
    return
   latest=float(ticks['time_msc'][-1])/1000.0 if 'time_msc' in ticks.dtype.names else float(ticks['time'][-1])
   tick_age=time.time()-latest
   if abs(tick_age)>settings.max_tick_age_seconds:
    await self._log_reject('SCAN_REJECT',symbol,reason='STALE_TICKS',age_seconds=tick_age)
    return

   ok,sp,avg,lim=self.risk.spread_ok(tick,info)
   if not ok:
    await self._log_reject('SPREAD_REJECT',symbol,spread=sp,average=avg,limit=lim)
    return
   m5=self.gw.rates_m5(symbol,200)
   m1=self.gw.rates_m1(symbol,300)
   m15=self.gw.rates_m15(symbol,200)
   h1=self.gw.rates_h1(symbol,200)
   if self.ai_native_only:
    # Experimental isolation: old executable strategies/ranker are disabled.
    # AI creates the bounded signal directly from raw market context.
    native_snapshot=self.ai_native.snapshot(
     symbol,tick,info,ticks,m1,m5,m15,h1,self.strategy_performance
    )
    native_decision=await self.ai_native.decide(symbol,native_snapshot)
    if native_decision.get('research_required'):
     research=await self.web_research.search(
      symbol,native_decision.get('research_query','')
     )
     if not research.get('ok'):
      await self._log_reject(
       'AI_RESEARCH_REJECT',symbol,reason='WEB_RESEARCH_FAILED'
      )
      return
     if research.get('high_impact_recent'):
      await self._log_reject(
       'AI_NEWS_GUARD_REJECT',symbol,reason='RECENT_HIGH_IMPACT_NEWS',
       articles=research.get('articles',[]),
      )
      return
     # Re-ask the AI once with fresh trusted-source research attached.
     native_snapshot=dict(native_snapshot)
     native_snapshot['news']=research
     native_decision=await self.ai_native.decide(symbol,native_snapshot)
     if native_decision.get('research_required'):
      await self._log_reject(
       'AI_RESEARCH_REJECT',symbol,reason='REPEATED_RESEARCH_REQUEST'
      )
      return
    sig,reg=self.ai_native.to_signal(native_decision,native_snapshot)
    meta={
     'decision':'ai_native',
     'strategy_selection':[],
     'opportunity_candidates':[],
     'opportunity_diagnostics':[],
     'market_mode':native_decision.get('regime','UNKNOWN'),
     'ai_native':native_decision,
    }
   else:
    reg,sig,meta=self.an.analyze(
     ticks,info.point,m5,symbol=symbol,
     rates_m15=m15,
     rates_h1=h1,
     rates_m1=m1,
     strategy_performance=self.strategy_performance,
     min_confidence=self.min_confidence,
    )
   for diagnostic in meta.get('opportunity_diagnostics',[]) or []:
    await self._log_reject(
     'OPPORTUNITY_DIAGNOSTIC',symbol,
     reason=diagnostic.get('strategy',''),
     **diagnostic,
    )
   selection=meta.get('strategy_selection',[]) or []
   if selection:
    winner=next((row for row in selection if row.get('selected')),None)
    await self._log_reject(
     'STRATEGY_SELECTION',symbol,
     reason=(winner or {}).get('strategy','none'),
     winner=(winner or {}).get('strategy'),
     winner_score=(winner or {}).get('final_score'),
     candidates=selection,
    )
   for candidate in meta.get('opportunity_candidates',[]) or []:
    await self._log_reject(
     'OPPORTUNITY_CANDIDATE',symbol,
     reason=candidate.get('strategy',''),
     strategy=candidate.get('strategy'),
     side=candidate.get('side'),
     confidence=candidate.get('confidence'),
     selected=bool(candidate.get('selected')),
     preempted_by=candidate.get('preempted_by'),
     final_signal=sig.strategy if sig is not None else None,
    )
   if not sig:
    await self._log_reject('NO_SIGNAL',symbol,regime=reg.value)
    return
   # Temporary experiment isolation: keep MACD enabled elsewhere, but do not
   # allow it to open XAUUSD positions while protection management is measured.
   if symbol.upper()=='XAUUSD' and sig.strategy=='macd_momentum':
    await self._log_reject(
     'STRATEGY_SYMBOL_REJECT',symbol,
     reason='EXPERIMENT_MACD_XAUUSD_SUSPENDED',
     strategy=sig.strategy,side=sig.side.value,
    )
    return
   confidence_score=float(sig.confidence)*100.0
   # Telegram confidence setting is a real hard entry filter.
   if confidence_score < self.min_confidence:
    await self._log_reject('CONFIDENCE_REJECT',symbol,strategy=sig.strategy,confidence=confidence_score,min_confidence=self.min_confidence)
    return
   # In AI-native-only mode the selector above is already the entry decision.
   # Keep the old advisor gate only for the legacy fallback mode.
   ai_decision=meta.get('ai_native')
   if not self.ai_native_only:
    ai_snapshot=self.ai.snapshot(symbol,sig,reg,tick,info,m1,m5,meta,self.strategy_performance)
    ai_decision=await self.ai.decide(symbol,ai_snapshot)
   await self.db.log(
    'AI_GATE',symbol,
    strategy=sig.strategy,side=sig.side.value,
    decision=ai_decision.get('decision'),
    ai_confidence=ai_decision.get('confidence'),
    ai_model=ai_decision.get('model'),
    reason_code=ai_decision.get('reason_code'),
    reason=ai_decision.get('reason'),
    latency_ms=ai_decision.get('latency_ms'),
    prompt_hash=ai_decision.get('prompt_hash'),
   )
   if (self.ai_native_only and ai_decision.get('decision')!='SIGNAL') or (not self.ai_native_only and ai_decision.get('decision')!='ALLOW'):
    await self._log_reject(
     'AI_ENTRY_REJECT',symbol,
     reason=ai_decision.get('reason_code','AI_REJECT'),
     strategy=sig.strategy,side=sig.side.value,
     ai_confidence=ai_decision.get('confidence'),
     ai_model=ai_decision.get('model'),
    )
    return
   if symbol in usd_group:
    conflicts=[
     t for t in self.trades.values()
     if t.symbol in usd_group and t.side==sig.side
    ]
    if conflicts:
     await self._log_reject(
      'CORRELATION_REJECT',symbol,side=sig.side.value,strategy=sig.strategy,
      conflicting_positions=[
       {'ticket':t.ticket,'symbol':t.symbol,'side':t.side.value,'strategy':t.strategy}
       for t in conflicts
      ],
      rule='same_direction_usd_group',
     )
     return

   # بعد الإغلاق: مهلة قصيرة، ثم يجب أن تتجدد الإشارة قبل تكرار نفس الاستراتيجية/الاتجاه.
   signal_bar=int(m1['time'][-1]) if m1 is not None and len(m1) and sig.strategy=='ema_cross_scalp' else (int(m5['time'][-1]) if m5 is not None and len(m5) else int(ticks['time'][-1]//60*60))
   signal_key=_signal_key(sig.strategy,sig.side.value,signal_bar)
   last_close=self.last_close_by_symbol.get(symbol,0)
   if last_close and time.time()-last_close<self.reentry_cooldown_seconds:
    await self._log_reject('REENTRY_REJECT',symbol,reason='COOLDOWN',strategy=sig.strategy,side=sig.side.value)
    return
   if self.blocked_signal_by_symbol.get(symbol)==signal_key:
    await self._log_reject('REENTRY_REJECT',symbol,reason='SAME_SIGNAL_BLOCKED',strategy=sig.strategy,side=sig.side.value)
    return

   last=self.last_entry_by_symbol.get(symbol,0)
   if time.time()-last<3:
    await self._log_reject('REENTRY_REJECT',symbol,reason='ENTRY_THROTTLE',strategy=sig.strategy,side=sig.side.value)
    return

   # Analysis and history calls may take time. Size and price the order from
   # a fresh quote, not from the quote captured before analysis.
   tick=self.gw.tick(symbol)
   if not tick or tick.bid<=0 or tick.ask<=tick.bid:
    await self._log_reject(
     'SCAN_REJECT',symbol,reason='INVALID_ENTRY_QUOTE',
     bid=float(getattr(tick,'bid',0) or 0) if tick else 0,
     ask=float(getattr(tick,'ask',0) or 0) if tick else 0,
    )
    return
   # Re-check freshness from copy_ticks_range(), whose timestamps are Unix UTC
   # on this Wine/MT5 setup. Do not compare the terminal-local live quote time.
   entry_ticks=self.gw.ticks(symbol,80)
   if entry_ticks is None or len(entry_ticks)<80:
    await self._log_reject('SCAN_REJECT',symbol,reason='INSUFFICIENT_ENTRY_TICKS')
    return
   entry_latest=(float(entry_ticks['time_msc'][-1])/1000.0
                 if 'time_msc' in entry_ticks.dtype.names
                 else float(entry_ticks['time'][-1]))
   entry_age=time.time()-entry_latest
   if abs(entry_age)>settings.max_tick_age_seconds:
    await self._log_reject('SCAN_REJECT',symbol,reason='STALE_ENTRY_QUOTE',age_seconds=entry_age)
    return
   spread_ok,_,_,_=self.risk.spread_ok(tick,info)
   if not spread_ok:
    await self._log_reject('SPREAD_REJECT',symbol,reason='ENTRY_SPREAD')
    return

   # مسافة SL: الاستراتيجية + الحد الأدنى الذي يفرضه الوسيط
   broker_stop_points=max(
    float(getattr(info,'trade_stops_level',0) or 0),
    float(getattr(info,'trade_freeze_level',0) or 0)
   )
   sl_points=max(
    float(sig.sl_points),
    float(settings.min_sl_points),
    broker_stop_points+2.0
   )

   price=tick.ask if sig.side==Side.BUY else tick.bid
   typ=mt5.ORDER_TYPE_BUY if sig.side==Side.BUY else mt5.ORDER_TYPE_SELL

   # MT5 يتحقق من الوقف مقابل جهة الإغلاق:
   # BUY يغلق على Bid و SELL يغلق على Ask.
   # نضيف السبريد + هامش نقطتين حتى لا يكون الوقف داخل السعر الحالي.
   spread_points=max(0.0,(float(tick.ask)-float(tick.bid))/float(info.point))
   valid_distance_points=max(
    sl_points,
    broker_stop_points+2.0,
    spread_points+2.0
   )
   d=valid_distance_points*info.point

   if sig.side==Side.BUY:
    sl=float(tick.bid)-d
    tp=price+abs(price-sl)*self.rr
   else:
    sl=float(tick.ask)+d
    tp=price-abs(sl-price)*self.rr

   sl=round(sl,int(info.digits))
   tp=round(tp,int(info.digits))

   # المخاطرة النقدية المستهدفة من Equity
   risk_cash=float(account.equity)*(self.risk_pct/100.0)

   # خسارة 1 لوت عند الوصول إلى SL - MT5 يحسبها حسب خصائص كل سوق
   loss_1lot=mt5.order_calc_profit(typ,symbol,1.0,price,sl)
   if loss_1lot is None or abs(loss_1lot)<=0:
    await self.notify(f'❌ تعذر حساب المخاطرة — {symbol}')
    return
   loss_1lot=abs(float(loss_1lot))

   vmin=float(info.volume_min)
   vmax=float(info.volume_max)
   vstep=float(info.volume_step)

   # إذا أقل لوت يتجاوز الحد المالي، لا ندخل
   min_risk=loss_1lot*vmin
   if min_risk > risk_cash+0.01:
    await self.notify(
     f'⛔ لم تنفذ {symbol}\n'
     f'أقل لوت يخاطر بـ ${min_risk:.2f}\n'
     f'حدك المسموح: ${risk_cash:.2f} ({self.risk_pct:g}%)'
    )
    return

   # أكبر لوت لا يتجاوز مبلغ المخاطرة
   import math
   raw_vol=risk_cash/loss_1lot
   steps=math.floor((raw_vol-vmin)/vstep+1e-9)
   vol=vmin+max(0,steps)*vstep
   vol=min(vol,vmax)

   # The Telegram risk setting is the per-trade target. Never silently reduce
   # the risk-sized volume because of margin constraints.
   margin_required=mt5.order_calc_margin(typ,symbol,vol,price)
   free_margin=float(getattr(account,'margin_free',0) or 0)
   if margin_required is None or float(margin_required) > free_margin*0.95:
    await self._log_reject(
     'MARGIN_REJECT',symbol,reason='PLANNED_RISK_VOLUME_UNAVAILABLE',
     planned_volume=vol,margin_required=margin_required,free_margin=free_margin,
     risk_cash=risk_cash,risk_pct=self.risk_pct,
    )
    return

   # Preserve the existing 200% projected margin-level safety floor, but reject
   # the trade rather than changing its requested risk.
   current_margin=float(getattr(account,'margin',0) or 0)
   equity=float(getattr(account,'equity',0) or 0)
   projected_margin=current_margin+float(margin_required)
   projected_level=(equity/projected_margin*100.0) if projected_margin>0 else float('inf')
   if projected_level < 200.0:
    await self._log_reject(
     'MARGIN_REJECT',symbol,reason='PLANNED_VOLUME_BELOW_200_PERCENT_MARGIN_LEVEL',
     planned_volume=vol,margin_required=margin_required,free_margin=free_margin,
     projected_margin_level_pct=projected_level,risk_cash=risk_cash,risk_pct=self.risk_pct,
    )
    return
   # تثبيت الحجم على خطوة الوسيط وإعادة التحقق النهائي
   steps=math.floor((vol-vmin)/vstep+1e-9)
   vol=vmin+max(0,steps)*vstep
   vol=max(vmin,min(vmax,vol))

   actual_risk=loss_1lot*vol

   # حاجز أمان: لا يسمح بتجاوز المخاطرة المحددة
   while actual_risk > risk_cash+0.01 and vol-vstep >= vmin-1e-9:
    vol=round(vol-vstep,8)
    actual_risk=loss_1lot*vol

   if actual_risk > risk_cash+0.01:
    await self.notify(
     f'⛔ لم تنفذ {symbol}\n'
     f'المخاطرة المحسوبة ${actual_risk:.2f} تتجاوز حدك ${risk_cash:.2f}'
    )
    return

   actual_risk_pct=(actual_risk/float(account.equity)*100.0) if account.equity else 0.0

   existing=self.gw.positions(symbol)
   if existing is None:
    await self._log_reject('ORDER_REJECT',symbol,reason='POSITIONS_UNAVAILABLE')
    return
   before={p.ticket for p in existing}
   filling=self.gw.filling_for(info)
   if filling is None:
    await self._log_reject('ORDER_REJECT',symbol,reason='NO_SUPPORTED_FILLING_MODE')
    return
   req={'action':mt5.TRADE_ACTION_DEAL,'symbol':symbol,'volume':vol,'type':typ,
        'price':price,'sl':sl,'tp':tp,'deviation':settings.max_slippage_points,
        'magic':4009,'comment':f'TGSCALP:{sig.strategy}',
        'type_time':mt5.ORDER_TIME_GTC,'type_filling':filling}

   chk=self.gw.order_check(req)
   if not chk:
    notice_key=('order_check_none',symbol,signal_key)
    if notice_key not in self.execution_notice_once:
     self.execution_notice_once.add(notice_key)
     await self.notify(f'❌ لم تنفذ {symbol}\norder_check لم يرجع نتيجة\nMT5: {mt5.last_error()}')
    await self._log_reject(
     'ORDER_CHECK_REJECT',symbol,reason='NO_RESULT',volume=vol,
     last_error=repr(mt5.last_error()),
     order_type=int(typ),price=float(price),sl=float(sl),tp=float(tp),
     filling=int(filling),deviation=int(settings.max_slippage_points),
    )
    return

   # Never rescue insufficient margin by shrinking volume: configured risk is the target.
   no_money=getattr(mt5,'TRADE_RETCODE_NO_MONEY',10019)
   if chk.retcode==no_money:
    await self._log_reject(
     'MARGIN_REJECT',symbol,reason='ORDER_CHECK_NO_MONEY',
     planned_volume=vol,risk_cash=risk_cash,risk_pct=self.risk_pct,
     retcode=chk.retcode,comment=getattr(chk,'comment','No money'),
    )
    return

   if chk.retcode!=0:
    await self._log_reject(
     'ORDER_CHECK_REJECT',symbol,reason='BROKER_REJECT',
     retcode=chk.retcode,comment=getattr(chk,'comment','غير معروف'),
     volume=vol,risk_pct=actual_risk_pct,
    )
    notice_key=('order_check_reject',symbol,signal_key,chk.retcode)
    if notice_key not in self.execution_notice_once:
     self.execution_notice_once.add(notice_key)
     await self.notify(
      f'❌ رفض فحص الصفقة — {symbol}\n'
      f'الكود: {chk.retcode}\n'
      f'السبب: {getattr(chk,"comment","غير معروف")}\n'
      f'اللوت: {vol:g} | المخاطرة: {actual_risk_pct:.2f}%'
     )
    return

   res=self.gw.send(req)
   self.last_entry_by_symbol[symbol]=time.time()
   if not res or res.retcode not in (mt5.TRADE_RETCODE_DONE,mt5.TRADE_RETCODE_DONE_PARTIAL):
    await self.notify(
     f'❌ فشل تنفيذ الصفقة — {symbol}\n'
     f'الكود: {getattr(res,"retcode","لا يوجد")}\n'
     f'السبب: {getattr(res,"comment",mt5.last_error())}\n'
     f'اللوت: {vol:g} | المخاطرة: {actual_risk_pct:.2f}%'
    )
    return

   # MT5 قد يتأخر في إظهار المركز بعد نجاح التنفيذ. Verify directly for
   # up to 15 seconds before declaring the successfully-sent trade unmanaged.
   pos=None
   for _ in range(150):
    await asyncio.sleep(.1)
    pos=self.gw.find_new_bot_position(symbol,before)
    if pos:
     break

   if not pos:
    self.running=False
    await self.db.log(
     'POSITION_LINK_FAILED',symbol,result=str(res),
     order=getattr(res,'order',None),deal=getattr(res,'deal',None),
     retcode=getattr(res,'retcode',None),last_error=repr(mt5.last_error()),
     before_tickets=sorted(before),
    )
    try:
     await self.notify(
      f'🚨 نُفذت صفقة {symbol} لكن تعذر ربطها آلياً. '
      f'أُوقف المحرك بالكامل؛ افحص المركز في MT5 قبل إعادة التشغيل.'
     )
    except Exception:
     pass
    return

   # نعتمد القيم الفعلية التي سجلها MT5 بعد التنفيذ
   fill=float(pos.price_open or res.price or price)
   actual_sl=float(pos.sl or sl)
   actual_tp=float(pos.tp or tp)
   initial_r=abs(fill-actual_sl)

   if initial_r<=0:
    await self._handle_invalid_initial_r(symbol,pos,fill,actual_sl)
    return

   t=TradeState(
    pos.ticket,symbol,sig.side,fill,actual_sl,actual_tp,initial_r,time.time(),
    strategy=sig.strategy,regime=reg.value,confidence=sig.confidence,
    reason=sig.reason,volume=float(pos.volume or vol),signal_bar=signal_bar,
    protection_pct=self.protection_pct,trailing_gap_pct=self.trailing_gap_pct
   )

   sl=actual_sl
   tp=actual_tp
   vol=float(pos.volume or vol)

   # Recalculate risk and R:R from the broker-confirmed fill/SL/TP/volume.
   # Pre-send values can drift slightly because the actual fill may differ.
   post_fill_loss=mt5.order_calc_profit(typ,symbol,vol,fill,actual_sl)
   post_fill_reward=mt5.order_calc_profit(typ,symbol,vol,fill,actual_tp)
   if post_fill_loss is not None and abs(float(post_fill_loss))>0:
    actual_risk=abs(float(post_fill_loss))
   else:
    # Defensive fallback: preserve the already-validated pre-send estimate.
    actual_risk=float(actual_risk)
   actual_risk_pct=(actual_risk/float(account.equity)*100.0) if account.equity else 0.0
   actual_rr=(
    abs(float(post_fill_reward))/actual_risk
    if post_fill_reward is not None and actual_risk>0
    else self.rr
   )
   if not math.isfinite(actual_rr) or actual_rr<=0:
    actual_rr=self.rr

   risk_drift_cash=actual_risk-risk_cash
   risk_drift_pct=((actual_risk/risk_cash)-1.0)*100.0 if risk_cash>0 else 0.0
   if abs(risk_drift_cash)>0.01:
    await self.db.log(
     'POST_FILL_RISK_DRIFT',symbol,
     ticket=pos.ticket,planned_risk_cash=risk_cash,actual_risk_cash=actual_risk,
     drift_cash=risk_drift_cash,drift_pct=risk_drift_pct,
     planned_entry=price,actual_entry=fill,actual_sl=actual_sl,
     volume=vol,actual_rr=actual_rr,
    )

   # Broker-confirmed risk must remain within +/-5% of the Telegram target.
   if abs(risk_drift_pct)>5.0:
    await self.db.log(
     'POST_FILL_RISK_DRIFT_REJECT',symbol,
     ticket=pos.ticket,planned_risk_cash=risk_cash,actual_risk_cash=actual_risk,
     drift_cash=risk_drift_cash,drift_pct=risk_drift_pct,
     planned_entry=price,actual_entry=fill,actual_sl=actual_sl,
     volume=vol,risk_pct=self.risk_pct,
    )
    await self._handle_invalid_initial_r(symbol,pos,fill,actual_sl)
    return

   self.trades[pos.ticket]=t
   self.trade_alert_meta[pos.ticket]={
    'risk_cash':actual_risk,
    'risk_pct':actual_risk_pct,
    'rr_actual':actual_rr,
    'planned_risk_cash':risk_cash,
    'risk_drift_cash':risk_drift_cash,
    'risk_drift_pct':risk_drift_pct,
   }
   self.execution_notice_once.discard(('margin_min',symbol))
   # A successful trade resets this symbol to the normal spread baseline.
   self.risk.reset_spread_relaxation(symbol)

   await self.db.log(
    'OPEN',symbol,ticket=pos.ticket,entry=fill,sl=sl,tp=tp,volume=vol,
    side=sig.side.value,strategy=sig.strategy,regime=reg.value,
    confidence=float(sig.confidence),reason=sig.reason,
    risk_cash=actual_risk,risk_pct=actual_risk_pct,rr_actual=actual_rr,
   )
   asyncio.create_task(self._send_trade_chart(t,actual_risk,actual_risk_pct))

 async def _handle_invalid_initial_r(self,symbol,pos,fill,actual_sl):
  await self.db.log(
   'INVALID_INITIAL_R',symbol,
   ticket=pos.ticket,entry=fill,sl=actual_sl
  )
  close_res=self.gw.close(pos)
  close_ok=(
   close_res is not None
   and getattr(close_res,'retcode',None) in (
    mt5.TRADE_RETCODE_DONE,mt5.TRADE_RETCODE_DONE_PARTIAL
   )
  )
  remaining=self.gw.position_by_ticket(pos.ticket)
  await self.db.log(
   'INVALID_INITIAL_R_EXIT',symbol,
   ticket=pos.ticket,
   retcode=getattr(close_res,'retcode',None),
   comment=getattr(close_res,'comment','') if close_res is not None else ''
  )
  if not close_ok or remaining is not None:
   self.running=False
   await self.notify(
    f'🚨 {symbol}: المركز {pos.ticket} لديه R ابتدائية غير صالحة '
    'ولم يُغلق بالكامل؛ تم إيقاف المحرك لمنع مركز غير متتبع.'
   )
   return False
  await self.notify(
   f'⚠️ {symbol}: أُغلقت الصفقة {pos.ticket} حمايةً لأن '
   'المسافة الابتدائية إلى وقف الخسارة غير صالحة.'
  )
  return True

 async def _trade_caption(self,t,current_price=None,pnl=None,closed=False):
  info=self.gw.info(t.symbol)
  digits=int(getattr(info,'digits',5) or 5)
  side='شراء 🟢' if t.side==Side.BUY else 'بيع 🔴'
  meta=self.trade_alert_meta.get(t.ticket,{})
  risk_cash=float(meta.get('risk_cash',0) or 0)
  risk_pct=float(meta.get('risk_pct',self.risk_pct) or self.risk_pct)
  rr_actual=float(meta.get('rr_actual',self.rr) or self.rr)

  if closed:
   if pnl is None:
    live_line='🏁 النتيجة: مغلقة'
   elif pnl>=0:
    live_line=f'🏁 النتيجة: +${pnl:.2f} 🟢'
   else:
    live_line=f'🏁 النتيجة: ${pnl:.2f} 🔴'
  else:
   pos=self.gw.position_by_ticket(t.ticket)
   live_pnl=float(getattr(pos,'profit',0) or 0) if pos else 0.0
   pnl_icon='🟢' if live_pnl>=0 else '🔴'
   pnl_sign='+' if live_pnl>0 else ''
   price_text=f'{current_price:.{digits}f}' if current_price is not None else 'جاري التحديث'
   live_line=f'💹 السعر الآن: {price_text} ({pnl_sign}${live_pnl:.2f} {pnl_icon})'

  return (
   f'📊 {t.symbol} — {side}\n'
   f'🧠 الاستراتيجية: {t.strategy}\n'
   f'🎯 الثقة: {t.confidence*100:.0f}%\n'
   f'🎫 الصفقة: {t.ticket}\n'
   f'📦 اللوت: {t.volume:g}\n'
   f'⚠️ المخاطرة: {risk_pct:.2f}% (${risk_cash:.2f})\n'
   f'➡️ الدخول: {t.entry:.{digits}f}\n'
   f'🛑 الوقف: {t.sl:.{digits}f}\n'
   f'💰 الهدف: {t.tp:.{digits}f}\n'
   f'{live_line}\n'
   f'🛡 الحماية: {t.protection_pct:g}% | ⚖️ R:R 1:{rr_actual:.2f}'
  )

 async def _send_trade_chart(self,t,actual_risk,actual_risk_pct):
  try:
   import os,tempfile
   import matplotlib
   matplotlib.use('Agg')
   import matplotlib.pyplot as plt
   import matplotlib.dates as mdates
   from datetime import datetime

   rates=self.gw.rates_m5(t.symbol,18)
   if rates is None or len(rates)<10:
    await self.db.log('TRADE_CHART_FAILED',t.symbol,ticket=t.ticket,error='not enough M5 candles')
    return

   xs=[datetime.fromtimestamp(int(r['time'])) for r in rates]
   opens=[float(r['open']) for r in rates]
   highs=[float(r['high']) for r in rates]
   lows=[float(r['low']) for r in rates]
   closes=[float(r['close']) for r in rates]
   xnum=mdates.date2num(xs)

   fig,ax=plt.subplots(figsize=(11,6.2),dpi=130)
   width=(5/(24*60))*0.68
   for x,o,h,l,cl in zip(xnum,opens,highs,lows,closes):
    up=cl>=o
    color='#16a34a' if up else '#dc2626'
    ax.vlines(x,l,h,color=color,linewidth=1)
    body_low=min(o,cl)
    body_h=max(abs(cl-o),max(highs)*1e-7)
    ax.add_patch(plt.Rectangle((x-width/2,body_low),width,body_h,facecolor=color,edgecolor=color,linewidth=.8))

   ax.axhline(t.entry,color='#2563eb',linewidth=1.5,label=f'ENTRY {t.entry:g}')
   ax.axhline(t.sl,color='#dc2626',linewidth=1.3,linestyle='--',label=f'SL {t.sl:g}')
   ax.axhline(t.tp,color='#16a34a',linewidth=1.3,linestyle='--',label=f'TP {t.tp:g}')
   protection=t.entry+(t.tp-t.entry)*(t.protection_pct/100.0)
   ax.axhline(protection,color='#f59e0b',linewidth=1.2,linestyle=':',label=f'PROTECTION {t.protection_pct:g}%')

   side='BUY' if t.side==Side.BUY else 'SELL'
   # MT5 rates and position time are Unix timestamps. Use the broker position
   # timestamp directly so the marker is not affected by the Linux timezone.
   pos=self.gw.position_by_ticket(t.ticket)
   mt5_open_ts=int(getattr(pos,'time',0) or 0) if pos else 0
   if not mt5_open_ts:
    mt5_open_ts=int(t.opened_at)
   rate_ts=[int(r['time']) for r in rates]
   entry_idx=min(range(len(rate_ts)),key=lambda i:abs(rate_ts[i]-mt5_open_ts))
   entry_x=xnum[entry_idx]
   marker='^' if side=='BUY' else 'v'
   ax.scatter([entry_x],[t.entry],marker=marker,s=150,color='#111827',zorder=6)
   ax.annotate(f'{side} ENTRY',xy=(entry_x,t.entry),xytext=(0,18 if side=='BUY' else -28),
    textcoords='offset points',ha='center',fontsize=9,fontweight='bold',
    arrowprops=dict(arrowstyle='->',linewidth=1))

   ax.axvline(entry_x,color='#6b7280',linewidth=1,linestyle=':',alpha=.7)
   ax.set_title(f'{t.symbol}  {side}  |  {t.strategy}  |  Confidence {t.confidence*100:.0f}%')
   ax.set_ylabel('Price')
   ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
   ax.grid(alpha=.18)
   ax.legend(loc='best',fontsize=8)
   fig.autofmt_xdate()
   fig.tight_layout()

   fd,path=tempfile.mkstemp(prefix=f'mtbot_{t.symbol}_',suffix='.png')
   os.close(fd)
   fig.savefig(path,bbox_inches='tight')
   plt.close(fig)

   tick=self.gw.tick(t.symbol)
   current=float(tick.bid if t.side==Side.BUY else tick.ask) if tick else t.entry
   caption=await self._trade_caption(t,current_price=current)
   await self.notify(caption,photo_path=path,caption=caption,trade_ticket=t.ticket,pin=True)
   await self.db.log('TRADE_CHART_SENT',t.symbol,ticket=t.ticket)
  except Exception as ex:
   await self.db.log('TRADE_CHART_FAILED',t.symbol,ticket=t.ticket,error=str(ex))

 async def live_dashboard(self):
  lines=[]

  for t in self.trades.values():
   pos=self.gw.position_by_ticket(t.ticket)
   tick=self.gw.tick(t.symbol)
   if not pos or not tick:
    continue

   price=float(tick.bid if t.side==Side.BUY else tick.ask)
   pnl=float(getattr(pos,'profit',0) or 0)

   tp_distance=abs(float(t.tp)-float(t.entry))
   sl_distance=abs(float(t.entry)-float(t.sl))

   favorable=(price-float(t.entry)) if t.side==Side.BUY else (float(t.entry)-price)
   adverse=(float(t.entry)-price) if t.side==Side.BUY else (price-float(t.entry))

   tp_pct=max(0.0,min(100.0,(favorable/tp_distance)*100.0)) if tp_distance else 0.0
   sl_pct=max(0.0,min(100.0,(adverse/sl_distance)*100.0)) if sl_distance else 0.0

   money=f'+{pnl:.2f}$ 🟢' if pnl>=0 else f'{pnl:.2f}$ 🔻'

   lines.append(f'➤ {t.symbol} | {money}')
   lines.append(f'           𖤹 ~ TP={tp_pct:.0f}% | SL={sl_pct:.0f}%')

  if not lines:
   lines.append('لا توجد صفقات مفتوحة')

  lines.append('↺ الصفقات المباشرة')
  await self.notify('\n'.join(lines))

 async def live_trade_panel(self,t,price,r,age,pos):
  a=self.gw.account()
  balance=float(getattr(a,'balance',0) or 0)
  currency=getattr(a,'currency','') or ''
  pnl=float(getattr(pos,'profit',0) or 0)

  info=self.gw.info(t.symbol)
  digits=int(getattr(info,'digits',5) or 5)

  side='شراء' if t.side==Side.BUY else 'بيع'
  sign='+' if pnl>0 else ''
  rsign='+' if r>0 else ''

  target_distance=abs(t.tp-t.entry)
  favorable=(price-t.entry) if t.side==Side.BUY else (t.entry-price)
  target_progress=max(0.0,(favorable/target_distance)*100) if target_distance>0 else 0.0

  if t.protection_45_active:
   protection='مفعلة 🔐 | تتبع الربح مستمر'
  else:
   protection=f'انتظار {t.protection_pct:g}% | التقدم: {target_progress:.0f}%'

  return (
   f'🤖 {t.symbol} | {side}\n'
   f'💰 الرصيد الحالي: {balance:.2f} {currency}\n'
   f'💵 الصفقة: {sign}{pnl:.2f} {currency} | {rsign}{r:.2f}R\n'
   f'💹 السعر: {price:.{digits}f}\n'
   f'🛑 الوقف: {t.sl:.{digits}f}\n'
   f'🎯 الهدف: {t.tp:.{digits}f}\n'
   f'🛡 {protection}'
  )

 async def manage(self,t,tick,info):
  price=tick.bid if t.side==Side.BUY else tick.ask
  # حد مدة الصفقة قابل للتحكم من Telegram.
  age=time.time()-t.opened_at
  max_age=max(60.0,float(self.max_trade_minutes)*60.0)
  if age>=max_age:
   pos=self.gw.position_by_ticket(t.ticket)
   if pos:
    res=self.gw.close(pos)
    if res and res.retcode in (mt5.TRADE_RETCODE_DONE,mt5.TRADE_RETCODE_DONE_PARTIAL):
     await self.db.log('MAX_DURATION_EXIT',t.symbol,ticket=t.ticket,age_seconds=age,max_trade_minutes=self.max_trade_minutes)
     return
    await self.db.log('MAX_DURATION_EXIT_FAILED',t.symbol,ticket=t.ticket,result=str(res))

  favorable=(price-t.entry) if t.side==Side.BUY else (t.entry-price)
  r=favorable/t.initial_r
  age=time.time()-t.opened_at
  pos=self.gw.position_by_ticket(t.ticket)

  # Position disappeared: determine the real MT5 close reason.
  if not pos:
   deals=self.gw.history_deals_by_position(t.ticket)
   exit_deals=[
    d for d in deals
    if getattr(d,'entry',None) in (getattr(mt5,'DEAL_ENTRY_OUT',1),getattr(mt5,'DEAL_ENTRY_OUT_BY',3))
   ]
   exit_deal=exit_deals[-1] if exit_deals else None

   if exit_deal:
    reason=getattr(exit_deal,'reason',None)
    # MT5 is the source of truth. Sum every closing fill/deal.
    pnl=sum(
     float(getattr(d,'profit',0) or 0)
     +float(getattr(d,'swap',0) or 0)
     +float(getattr(d,'commission',0) or 0)
     +float(getattr(d,'fee',0) or 0)
     for d in exit_deals
    )
    exit_price=float(getattr(exit_deal,'price',0) or 0)

    if reason==mt5.DEAL_REASON_TP:
     event='TP'
     result_reason='TP 🎯'
    elif reason==mt5.DEAL_REASON_SL:
     event='SL'
     result_reason='حماية ربح 🛡️' if pnl>0 and t.protection_45_active else 'SL 🛑'
    else:
     event='POSITION_CLOSED'
     result_reason='حماية ربح 🛡️' if pnl>0 and t.protection_45_active else 'إغلاق 🏁'

    if pnl < 0:
     self.consecutive_losses+=1
    elif pnl > 0:
     self.consecutive_losses=0
    await self.save_setting("consecutive_losses",self.consecutive_losses)

    await self.db.log(
     event,t.symbol,ticket=t.ticket,strategy=t.strategy,
     exit_price=exit_price,pnl=pnl,reason=reason
    )
    await self._refresh_strategy_performance(force=True)
    caption=await self._trade_caption(t,pnl=pnl,closed=True)
    await self.notify(caption,trade_ticket=t.ticket,trade_update=True,trade_result=pnl,trade_result_reason=result_reason)
   else:
    await self.db.log('POSITION_CLOSED',t.symbol,reason='history_not_found')
    caption=await self._trade_caption(t,pnl=None,closed=True)
    await self.notify(caption,trade_ticket=t.ticket,trade_update=True)

   self.last_close_by_symbol[t.symbol]=time.time()
   self.blocked_signal_by_symbol[t.symbol]=_signal_key(
    t.strategy,t.side.value,t.signal_bar
   )
   self.trades.pop(t.ticket,None)
   self.trade_alert_meta.pop(t.ticket,None)
   return

  # تحديث نفس رسالة صورة الصفقة بالسعر والوقف الحاليين.
  caption=await self._trade_caption(t,current_price=float(price))
  await self.notify(caption,trade_ticket=t.ticket,trade_update=True)

  # حماية الربح: نسبة التفعيل تبقى من إعداد Telegram.
  target_distance=abs(t.tp-t.entry)
  target_progress=(favorable/target_distance) if target_distance>0 else 0.0
  now=time.time()

  # عند التفعيل نعطي نقطة الدخول مساحة ضجيج ديناميكية مبنية على
  # وسيط السبريد الحديث (أو السبريد الحالي عند عدم توفر عينة).
  trigger=t.protection_pct/100.0
  if target_progress>=trigger and not t.protection_45_active:
   spread_points=max(0.0,(float(tick.ask)-float(tick.bid))/float(info.point))
   spread_history=list(self.risk.spreads.get(str(t.symbol),()) or ())
   if spread_history:
    ordered=sorted(float(x) for x in spread_history)
    mid=len(ordered)//2
    spread_reference=(
     ordered[mid] if len(ordered)%2
     else (ordered[mid-1]+ordered[mid])/2.0
    )
   else:
    spread_reference=spread_points
   buffer_points=max(3.0,spread_reference*1.5)
   protected_sl=(
    t.entry-buffer_points*info.point
    if t.side==Side.BUY
    else t.entry+buffer_points*info.point
   )
   protected_sl=round(protected_sl,int(info.digits))
   min_dist=max(int(getattr(info,'trade_stops_level',0) or 0),int(getattr(info,'trade_freeze_level',0) or 0))*info.point
   valid=(protected_sl <= tick.bid-min_dist) if t.side==Side.BUY else (protected_sl >= tick.ask+min_dist)
   res=self.gw.modify(pos.ticket,t.symbol,protected_sl,t.tp) if valid else None

   if res and res.retcode==mt5.TRADE_RETCODE_DONE:
    t.sl=protected_sl
    t.protection_45_active=True
    t.trailing=True
    t.best_favorable_price=price
    t.last_progress_at=now

    await self.db.log(
     'PROTECTION_ACTIVATED',t.symbol,
     ticket=t.ticket,sl=protected_sl,price=price,
     protection_pct=t.protection_pct,
     buffer_points=buffer_points,
     spread_reference_points=spread_reference,
     current_spread_points=spread_points,
     target_progress=target_progress,target_progress_pct=target_progress*100.0
    )

   else:
    await self.db.log(
     'PROTECTION_FAILED',t.symbol,ticket=t.ticket,result=str(res),
     retcode=getattr(res,'retcode',None),
     comment=getattr(res,'comment',None),
     last_error=repr(mt5.last_error()),
     requested_sl=protected_sl,current_sl=t.sl,tp=t.tp,
     protection_pct=t.protection_pct,
     buffer_points=buffer_points,
     spread_reference_points=spread_reference,
     current_spread_points=spread_points,
     bid=float(tick.bid),ask=float(tick.ask),
     stops_level=int(getattr(info,'trade_stops_level',0) or 0),
     freeze_level=int(getattr(info,'trade_freeze_level',0) or 0),
     valid_distance=bool(valid),
    )

  # بعد التفعيل: أفضل سعر جديد يحرك SL للأمام. لا يوجد إغلاق بسبب خمول زمني.
  if t.protection_45_active:
   progress=(
    (t.side==Side.BUY and price>t.best_favorable_price)
    or
    (t.side==Side.SELL and price<t.best_favorable_price)
   )

   if progress:
    t.best_favorable_price=price
    t.last_progress_at=now

    # فجوة التتبع = 5% من كامل مسافة الدخول إلى TP.
    gap=target_distance*(t.trailing_gap_pct/100.0)
    cand=(
     t.best_favorable_price-gap
     if t.side==Side.BUY
     else t.best_favorable_price+gap
    )

    # لا يرجع SL خلف مستوى الحماية الذي تم تفعيله فعلياً.
    # هذا يحافظ على BE buffer الديناميكي بدلاً من القفز إلى مستوى trigger.
    protection_level=t.sl

    if t.side==Side.BUY:
     cand=max(cand,protection_level)
    else:
     cand=min(cand,protection_level)

    min_dist=max(int(getattr(info,'trade_stops_level',0) or 0),int(getattr(info,'trade_freeze_level',0) or 0))*info.point
    valid=(cand <= tick.bid-min_dist) if t.side==Side.BUY else (cand >= tick.ask+min_dist)
    better=cand>t.sl if t.side==Side.BUY else cand<t.sl

    if better and valid:
     res=self.gw.modify(pos.ticket,t.symbol,cand,t.tp)

     if res and res.retcode==mt5.TRADE_RETCODE_DONE:
      oldsl=t.sl
      t.sl=cand
      trailing_progress=(
       ((t.best_favorable_price-t.entry)/target_distance)
       if t.side==Side.BUY
       else ((t.entry-t.best_favorable_price)/target_distance)
      ) if target_distance>0 else 0.0
      await self.db.log(
       'TRAILING_PROTECTION',t.symbol,
       ticket=t.ticket,old_sl=oldsl,new_sl=cand,
       best_price=t.best_favorable_price,
       target_progress=trailing_progress,
       target_progress_pct=trailing_progress*100.0
      )

