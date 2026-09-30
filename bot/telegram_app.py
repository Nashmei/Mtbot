from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import Application,CommandHandler,CallbackQueryHandler,MessageHandler,ContextTypes,filters
from telegram.error import BadRequest,RetryAfter
from core.config import settings
from bot import v2_views
class TelegramUI:
 def __init__(self,engine,db): self.e=engine;self.db=db;self.login_state={};self.input_state={};self.message_ids=set();self.menu_message_id=None;self.menu_chat_id=None;self.menu_expiry_task=None;self._mt5_login_task=None
 def allowed(self,u): return bool(u and u.id==settings.telegram_allowed_user_id)
 async def _apply_setting(self,key,value):
  import math
  if key=='risk':
   v=float(value)
   if not math.isfinite(v) or not 0.25<=v<=50: raise ValueError()
   self.e.risk_pct=v; db_key='risk_pct'; msg=f'✅ المخاطرة: {v:g}%'
  elif key=='confidence':
   v=float(value)
   if not math.isfinite(v) or not 50<=v<=95: raise ValueError()
   self.e.min_confidence=v; db_key='min_confidence'; msg=f'✅ الثقة: {v:g}%'
  elif key=='minentry':
   v=float(value)
   if not math.isfinite(v) or not 50<=v<=95: raise ValueError()
   self.e.min_entry_confidence=v; db_key='min_entry_confidence'; msg=f'✅ حد دخول الاستراتيجية: {v:g}%'
  elif key=='maxpos':
   v=int(value)
   if str(v)!=str(value).strip() or not 1<=v<=10: raise ValueError()
   self.e.max_positions=v; db_key='max_positions'; msg=f'✅ حد المراكز: {v}'
  elif key=='maxdaily':
   v=int(value)
   if str(v)!=str(value).strip() or not 0<=v<=200: raise ValueError()
   self.e.max_daily_trades=v; db_key='max_daily_trades'; msg=f'✅ حد الصفقات اليومي: {v}'+(' (معطل)' if v==0 else '')
  elif key=='maxcorr':
   v=int(value)
   if str(v)!=str(value).strip() or not 0<=v<=10: raise ValueError()
   self.e.max_correlated_positions=v; db_key='max_correlated_positions'; msg=f'✅ حد المراكز المترابطة: {v}'+(' (معطل)' if v==0 else '')
  elif key=='maxloss':
   v=int(value)
   if str(v)!=str(value).strip() or not 0<=v<=20: raise ValueError()
   self.e.max_consecutive_losses=v; self.e.loss_limit_notified=False
   db_key='max_consecutive_losses'; msg=f'✅ حد الخسائر: {v}'+(' (معطل)' if v==0 else '')
  elif key=='dailyloss':
   v=float(value)
   if not math.isfinite(v) or not 0<=v<=100: raise ValueError()
   self.e.daily_loss_limit_pct=v; self.e.daily_loss_notified=False
   db_key='daily_loss_limit_pct'; msg=f'✅ حد Equity اليومي: {v:g}%'+(' (معطل)' if v==0 else '')
  elif key=='sessionprofit':
   v=float(value)
   if not math.isfinite(v) or not 0<=v<=1000000000: raise ValueError()
   self.e.session_profit_limit=v; db_key='session_profit_limit'; msg=f'✅ حد ربح الجلسة: ${v:g}'+(' (معطل)' if v==0 else '')
  else:
   raise ValueError()
  if hasattr(self.e,'save_setting'):
   await self.e.save_setting(db_key,v)
  else:
   await self.db.set(db_key,v)
  return msg
 def _cancel_menu_expiry(self):
  if self.menu_expiry_task and not self.menu_expiry_task.done(): self.menu_expiry_task.cancel()
  self.menu_expiry_task=None
 def _arm_menu_expiry(self,bot,chat_id,message_id,delay=30):
  import asyncio
  self._cancel_menu_expiry()
  async def expire():
   try:
    await asyncio.sleep(delay)
    await bot.delete_message(chat_id=chat_id,message_id=message_id)
    if self.menu_message_id==message_id:self.menu_message_id=None;self.menu_chat_id=None
   except (asyncio.CancelledError,BadRequest): pass
   except Exception: pass
  self.menu_expiry_task=asyncio.create_task(expire())
 async def _edit(self,q,text,reply_markup=None,arm=False):
  try:
   await q.edit_message_text(text,reply_markup=reply_markup)
  except BadRequest as ex:
   if 'message is not modified' not in str(ex).lower(): return
  except RetryAfter as ex:
   import asyncio
   await asyncio.sleep(float(ex.retry_after)+.2)
   try: await q.edit_message_text(text,reply_markup=reply_markup)
   except Exception: return
  self.menu_chat_id=q.message.chat_id;self.menu_message_id=q.message.message_id
  if arm:self._arm_menu_expiry(q.get_bot(),self.menu_chat_id,self.menu_message_id)
 async def _run_progress(self,q,label,work,reply_markup=None):
  import asyncio
  self._cancel_menu_expiry()
  task=asyncio.create_task(asyncio.to_thread(work))
  dots=1
  while not task.done():
   await self._edit(q,label+('.'*dots),reply_markup=reply_markup,arm=False)
   dots=1 if dots>=4 else dots+1
   try: await asyncio.wait_for(asyncio.shield(task),timeout=1.2)
   except asyncio.TimeoutError: pass
  return await task
 async def _menu(self,bot,chat_id,text,reply_markup):
  self._cancel_menu_expiry()
  if self.menu_message_id and self.menu_chat_id==chat_id:
   try:
    await bot.edit_message_text(chat_id=chat_id,message_id=self.menu_message_id,text=text,reply_markup=reply_markup)
    return
   except Exception: pass
  m=await bot.send_message(chat_id=chat_id,text=text,reply_markup=reply_markup)
  self.menu_chat_id=chat_id;self.menu_message_id=m.message_id
 def kb(self):
  return InlineKeyboardMarkup([
   [InlineKeyboardButton('♻️ تحديث',callback_data='dashboard')],
   [InlineKeyboardButton('📊 التداول',callback_data='trade_menu'),InlineKeyboardButton('🧩 الاستراتيجيات',callback_data='strategy_center')],
   [InlineKeyboardButton('📈 الأداء',callback_data='performance'),InlineKeyboardButton('💼 الصفقات',callback_data='positions')],
   [InlineKeyboardButton('💱 الأسواق',callback_data='analysis_menu'),InlineKeyboardButton('⚙️ الإعدادات',callback_data='settings_menu')],
   [InlineKeyboardButton('🩺 صحة النظام',callback_data='health'),InlineKeyboardButton('👤 الحساب',callback_data='account_menu')]
  ])
 def trade_kb(self): return InlineKeyboardMarkup([[InlineKeyboardButton('▶️ تشغيل المحرك',callback_data='start'),InlineKeyboardButton('⏹ إيقاف',callback_data='stop')],[InlineKeyboardButton('📊 الحالة',callback_data='status'),InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]])
 def analysis_kb(self): return InlineKeyboardMarkup([[InlineKeyboardButton('🔎 تحليل الأزواج',callback_data='analyze')],[InlineKeyboardButton('💱 الأزواج',callback_data='symbols'),InlineKeyboardButton('🔥 الأنشط',callback_data='active')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]])
 def strategy_kb(self): return InlineKeyboardMarkup([
  [InlineKeyboardButton('♻️ تحديث',callback_data='strategy_center')],
  [InlineKeyboardButton('📚 الكتالوج',callback_data='strategy_catalog')],
  [InlineKeyboardButton('🚦 المصفوفة',callback_data='strategy_combos')],
  [InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]
 ])
 def settings_kb(self): return InlineKeyboardMarkup([
  [InlineKeyboardButton('⚠️ المخاطرة',callback_data='risk'),InlineKeyboardButton('🎯 الثقة',callback_data='confidence')],
  [InlineKeyboardButton('🎚️ حد دخول الاستراتيجية',callback_data='minentry')],
  [InlineKeyboardButton('📂 حد المراكز',callback_data='maxpos'),InlineKeyboardButton('❌ حد الخسائر',callback_data='maxloss')],
  [InlineKeyboardButton('📉 حد Equity اليومي',callback_data='dailyloss'),InlineKeyboardButton('📅 حد الصفقات اليومي',callback_data='maxdaily')],
  [InlineKeyboardButton('🔗 المراكز المترابطة',callback_data='maxcorr'),InlineKeyboardButton('🎯 حد ربح الجلسة',callback_data='sessionprofit')],
  [InlineKeyboardButton('🚦 مصفوفة الأداء',callback_data='strategy_combos')],
  [InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]
 ])
 def symbols_kb(self): return InlineKeyboardMarkup([
  [InlineKeyboardButton('🔥 الأنشط الآن',callback_data='active')],
  [InlineKeyboardButton('🔎 تحليل الأزواج',callback_data='analyze')],
  [InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]
 ])
 def account_kb(self):
  a=self.e.gw.account()
  rows=[[InlineKeyboardButton('🔐 ربط MT5',callback_data='mt5login'),InlineKeyboardButton('📈 الإحصائيات',callback_data='accountstats')],[InlineKeyboardButton('🧪 فحص الجاهزية',callback_data='readiness')]]
  if a:
   import MetaTrader5 as mt5
   if getattr(a,'trade_mode',None)==getattr(mt5,'ACCOUNT_TRADE_MODE_REAL',2):
    label='🔒 قفل تداول Real' if self.e.real_trading_enabled else '🔓 تفعيل تداول Real'
    rows.append([InlineKeyboardButton(label,callback_data='real_lock')])
  rows.append([InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')])
  return InlineKeyboardMarkup(rows)
 async def start(self,u,c):
  if not self.allowed(u.effective_user) or u.effective_chat.id!=settings.telegram_allowed_user_id:return
  a=self.e.gw.account(); state=f'MT5: ✅ {a.login} / {a.server}' if a else 'MT5: ❌ غير مسجل الدخول\nاستخدم 🔐 حساب MT5'
  await self._menu(c.bot,u.effective_chat.id,await v2_views.dashboard(self.e,self.db),self.kb())
 async def cb(self,u,c):
  q=u.callback_query
  if not self.allowed(q.from_user) or u.effective_chat.id!=settings.telegram_allowed_user_id:return
  try: await q.answer()
  except BadRequest: pass
  self._cancel_menu_expiry()
  self.menu_chat_id=q.message.chat_id
  self.menu_message_id=q.message.message_id
  x=q.data
  if x=='strategy_center':
   return await self._edit(q,await v2_views.strategy_center(self.e,self.db),self.strategy_kb())
  if x=='strategy_catalog':
   catalog=self.e.strategy_catalog()
   lines=['📚 كتالوج الاستراتيجيات','━━━━━━━━━━━━━━']
   buttons=[]
   for r in catalog:
    lines.append(f'{r["id"]} | {r["family"]} | {r["tier"]}')
   for r in catalog:
    buttons.append([InlineKeyboardButton(f'{r["tier"]} • {r["id"]}',callback_data=f'strategy:{r["id"]}')])
   buttons.append([InlineKeyboardButton('♻️ تحديث',callback_data='strategy_center')])
   buttons.append([InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')])
   return await self._edit(q,'\n'.join(lines),InlineKeyboardMarkup(buttons))
  if x=='strategy_combos':
   rows=await self.e.strategy_report(window=500)
   lines=['🚦 مصفوفة الاستراتيجية × الرمز × النظام','━━━━━━━━━━━━━━']
   if not rows:
    lines.append('لا توجد صفقات مغلقة بعد.')
   for r in rows[:20]:
    flag='🟢' if r.get('combo_enabled') else '🔴'
    lines.append(f'{flag} {r["strategy"]} | {r["symbol"]} | {r["regime"]} • '
                 f'{r["trades"]}t WR{r["win_rate"]:.0f}% PF{r["profit_factor"]:.2f} '
                 f'exp{r["expectancy"]:.2f} ({r.get("combo_verdict")})')
   return await self._edit(q,'\n'.join(lines),InlineKeyboardMarkup([[InlineKeyboardButton('♻️ تحديث',callback_data='strategy_center')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x.startswith('strategy:'):
   return await self._edit(q,await v2_views.strategy_detail(self.e,self.db,x.split(':',1)[1]),self.strategy_kb())
  if x=='performance':
   return await self._edit(q,await v2_views.performance(self.db),InlineKeyboardMarkup([[InlineKeyboardButton('🕐 آخر 24 ساعة',callback_data='performance24')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='performance24':
   return await self._edit(q,await v2_views.performance(self.db,24),InlineKeyboardMarkup([[InlineKeyboardButton('📚 السجل الحالي',callback_data='performance')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='positions':
   return await self._edit(q,await v2_views.positions(self.e),InlineKeyboardMarkup([[InlineKeyboardButton('♻️ تحديث',callback_data='positions')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='health':
   return await self._edit(q,await v2_views.health(self.e,self.db),InlineKeyboardMarkup([[InlineKeyboardButton('♻️ تحديث',callback_data='health')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='dashboard': return await self._edit(q,await v2_views.dashboard(self.e,self.db),self.kb())
  if x=='trade_menu': return await self._edit(q,'🤖 التداول وإدارة المحرك',self.trade_kb())
  if x=='analysis_menu': return await self._edit(q,'🔎 التحليل والأسواق',self.analysis_kb())
  if x=='settings_menu':
   daily=f'{self.e.daily_loss_limit_pct:g}%'+(' (معطل)' if self.e.daily_loss_limit_pct<=0 else '')
   losses=str(self.e.max_consecutive_losses)+(' (معطل)' if self.e.max_consecutive_losses==0 else '')
   msg=(f'⚙️ الإعدادات\n━━━━━━━━━━━━━━\n'
        f'⚠️ المخاطرة الثابتة: {self.e.risk_pct:g}%\n🎯 حد الثقة: {self.e.min_confidence:g}%\n'
        f'🎚️ حد دخول الاستراتيجية: {self.e.min_entry_confidence:g}%\n'
        f'📂 حد المراكز: {self.e.max_positions}\n❌ حد الخسائر: {losses}\n📉 حد Equity اليومي: {daily}\n'
        f'📅 حد الصفقات اليومي: {self.e.max_daily_trades}\n'
        f'🔗 حد المراكز المترابطة: {self.e.max_correlated_positions}\n'
        f'🎯 حد ربح الجلسة: ${self.e.session_profit_limit:g}\n\n'
        '🧩 الدخول والخروج مملوكان للاستراتيجية (SL/TP هيكلي)، والمحرك يحسب اللوت من المخاطرة المحددة.')
   return await self._edit(q,msg,self.settings_kb())
  if x=='account_menu': return await self._edit(q,'👤 حساب MT5 والإحصائيات',self.account_kb())
  if x=='mt5login':
   if self.e.running:
    return await self._edit(q,'⏹ أوقف المحرك قبل تغيير حساب MT5.',self.account_kb())
   self.login_state[q.from_user.id]={'step':'block'}; msg='🔐 تسجيل دخول MT5 — Demo / Real\n\nأرسل Server و Login و Password في رسالة واحدة.\nيمكن أن تكون في سطر واحد أو عدة أسطر.\nاستخدم /cancel للإلغاء.'
  elif x=='start':
   if not self.e.gw.account(): msg='❌ حساب MT5 غير متصل. استخدم 🔐 حساب MT5 أولاً.'
   else:
    await self._edit(q,'⏳ جاري تشغيل المحرك.',self.trade_kb(),arm=False)
    started=await self.e.start()
    if started:
     import MetaTrader5 as mt5
     a=self.e.gw.account(); mode='Demo' if getattr(a,'trade_mode',None)==mt5.ACCOUNT_TRADE_MODE_DEMO else 'Real'
     msg=f'🟢 تم تشغيل البوت على حساب {mode}.'
    else: msg='⚠️ لم يبدأ المحرك. راجع رسالة السبب وفحص الجاهزية.'
  elif x=='stop':
   await self._edit(q,'⏳ جاري إيقاف المحرك.',self.trade_kb(),arm=False)
   await self.e.stop(); msg='⏹ تم إيقاف البوت.'
  elif x=='readiness':
   st=self.e.gw.algo_status()
   msg=('🧪 فحص الجاهزية\n━━━━━━━━━━━━━━\n'+('✅ MT5 متصل\n' if st['connected'] else '❌ MT5 غير متصل\n')+('✅ الحساب يسمح بالتداول\n' if st['account_trade_allowed'] else '❌ الحساب يمنع التداول\n')+('✅ التداول الآلي مسموح للحساب\n' if st['trade_expert'] else '❌ التداول الآلي ممنوع للحساب\n')+('✅ Algo Trading مفعّل' if st['trade_allowed'] else '❌ Algo Trading غير مفعّل'))
  elif x=='status':
   a=self.e.gw.account(); head=f'MT5: ✅ {a.login} / {a.server}\n' if a else 'MT5: ❌ غير مسجل الدخول\n'; msg=head+await self.e.status()
  elif x=='accountstats':
   a=self.e.gw.account()
   if not a:
    msg='❌ حساب MT5 غير متصل.'
   else:
    positions=self.e.gw.positions() or ()
    floating=sum(float(getattr(p,'profit',0) or 0) for p in positions)
    msg=(f'📈 إحصائيات الحساب\n\n'
         f'💰 الرصيد: ${a.balance:.2f}\n'
         f'💵 Equity: ${a.equity:.2f}\n'
         f'📊 الربح/الخسارة العائمة: ${floating:+.2f}\n'
         f'📂 الصفقات المفتوحة: {len(positions)}\n'
         f'🔒 المارجن المستخدم: ${a.margin:.2f}\n'
         f'💳 المارجن الحر: ${a.margin_free:.2f}\n'
         f'⚠️ المخاطرة المحددة: {self.e.risk_pct:g}%\n'
         f'🎯 حد الدخول: {max(self.e.min_confidence,self.e.min_entry_confidence):g}%\n'
         f'❌ خسائر متتالية: {self.e.consecutive_losses}/{self.e.max_consecutive_losses}')
  elif x=='analyze':
   await self._edit(q,'⏳ جاري تحميل بيانات السوق وتحليل الأزواج.',self.analysis_kb(),arm=False)
   if not self.e.gw.account(): msg='❌ سجّل الدخول إلى MT5 أولاً.'
   else:
    lines=['🔎 تحليل الأزواج المختارة']
    for symbol in self.e.symbols:
     i=self.e.gw.info(symbol)
     if not i: continue
     import time
     ticks=self.e.gw.ticks(symbol)
     if ticks is None or len(ticks)<40:
      lines.append(f'\n💱 {symbol}\n⚠️ بيانات ticks غير كافية.')
      continue
     tick_ts=float(ticks['time_msc'][-1])/1000.0 if 'time_msc' in ticks.dtype.names else float(ticks['time'][-1])
     live_tick=self.e.gw.tick(symbol)
     live_epoch=float(getattr(live_tick,'time_msc',0) or 0)/1000.0 if live_tick else 0.0
     if live_epoch<=0 and live_tick: live_epoch=float(getattr(live_tick,'time',0) or 0)
     if live_epoch<=0 or abs(live_epoch-tick_ts)>settings.max_tick_age_seconds:
      lines.append(f'\n💱 {symbol}\n⚠️ آخر سعر قديم؛ لا توجد إشارة صالحة الآن.')
      continue
     decision,error=await self.e.analyze_symbol(symbol)
     if error:
      lines.append(f'\n💱 {symbol}\n⚠️ التحليل غير متاح: {error}')
     elif decision.get('decision')=='SIGNAL':
      direction='شراء 🟢' if decision.get('side')=='BUY' else 'بيع 🔴'
      lines.append(f"\n💱 {symbol}\n📌 الإشارة: {direction}\n🧠 الاستراتيجية: {decision.get('strategy_id')}\n🎯 قوة الإشارة: {decision.get('confidence',0):.0f}%\n📊 السوق: {decision.get('regime','UNKNOWN')}\n🔎 السبب: {decision.get('reason','')}")
     else:
      lines.append(f"\n💱 {symbol}\n⚪ لا توجد فرصة حالياً\n📊 السوق: {decision.get('regime','NO_TRADE')}\n🔎 السبب: {decision.get('reason_code') or decision.get('reason','NO_TRADE')}")
    msg='\n'.join(lines)
  elif x=='symbols':
   names=self.e.gw.ranked_symbol_names()
   popular=names[:12]
   rows=[[InlineKeyboardButton('🔥 الأنشط الآن',callback_data='active')]]
   for n in popular:
    info=self.e.gw.info(n); t=self.e.gw.tick(n)
    pts=(t.ask-t.bid)/info.point if t and info and info.point and t.ask>0 and t.bid>0 else 0
    icon='🥇' if 'XAU' in n.upper() else '💵'
    rows.append([InlineKeyboardButton(f'{"☑️" if n in self.e.symbols else "⬜"} {n} • {pts:.1f} pts',callback_data=f'sym:{n}')])
   rows.append([InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')])
   await self._edit(q,'💱 الرموز المتاحة من MT5:',reply_markup=InlineKeyboardMarkup(rows))
   return
  elif x=='active':
   # Fast full-market scan: use the latest MT5 tick only. Avoid downloading
   # tick history for every symbol, which blocks Telegram on large servers.
   import time
   names=list(dict.fromkeys(z.name for z in self.e.gw.available_symbols()))
   ranked=[]
   now=time.time()
   for n in names:
    info=self.e.gw.info(n); t=self.e.gw.tick(n)
    if not info or not t or not info.point or t.bid<=0 or t.ask<=0: continue
    spread=(t.ask-t.bid)/info.point
    if spread<=0: continue
    tick_time=float(getattr(t,'time_msc',0) or 0)/1000.0
    if not tick_time: tick_time=float(getattr(t,'time',0) or 0)
    age=max(0.0,now-tick_time) if tick_time else 999999.0
    # Ignore stale/closed instruments. Rank fresh symbols by current tick
    # activity (volume when supplied) adjusted for spread.
    if age>120: continue
    volume=float(getattr(t,'volume_real',0) or getattr(t,'volume',0) or 0)
    score=(1.0+volume)/spread
    ranked.append((score,n,spread,age,volume))
   ranked.sort(reverse=True)
   rows=[]
   for score,n,spread,age,volume in ranked[:10]:
    icon='🥇' if 'XAU' in n.upper() else '🔥'
    rows.append([InlineKeyboardButton(f'{icon} {n} • spread {spread:.1f} • {age:.0f}s',callback_data=f'sym:{n}')])
   rows.append([InlineKeyboardButton('↩️ الرموز',callback_data='symbols')])
   text=f'🔥 الأنشط الآن — كامل سوق MT5\\nتم فحص {len(names)} رمزاً بسرعة، وعرض أعلى 10 من الرموز ذات الأسعار اللحظية الحديثة.\\nالترتيب نشاط لحظي/سبريد وليس توقعاً للربحية.'
   if not ranked: text='⚠️ لا توجد أسعار لحظية حديثة كافية حالياً.'
   await self._edit(q,text,reply_markup=InlineKeyboardMarkup(rows))
   return
  elif x.startswith('sym:'):
   symbol=x.split(':',1)[1]
   if symbol in self.e.symbols:
    self.e.symbols.remove(symbol)
   else:
    self.e.symbols.append(symbol)
   self.e.symbol=self.e.symbols[0] if self.e.symbols else 'EURUSD'
   import json
   await self.e.save_setting('symbols',json.dumps(self.e.symbols))
   msg=f'✅ الأزواج المختارة: {", ".join(self.e.symbols) if self.e.symbols else "لا يوجد"}'
   await self._edit(q,msg,reply_markup=(self.account_kb() if x in ('mt5login','readiness','accountstats') else self.trade_kb() if x in ('start','stop','status') else self.analysis_kb() if x=='analyze' else self.settings_kb()))
   return
   
  elif x=='risk':
   self.input_state[q.from_user.id]='risk'
   msg='⚠️ أرسل نسبة المخاطرة فقط\nمثال: 15\nالمسموح: 0.25 إلى 50'
  elif x=='confidence':
   self.input_state[q.from_user.id]='confidence'
   msg=f'🎯 الثقة الحالية: {self.e.min_confidence:g}%\nأرسل النسبة فقط\nمثال: 75\nالمسموح: 50 إلى 95'
  elif x=='minentry':
   self.input_state[q.from_user.id]='minentry'
   msg=f'🎚️ حد دخول الاستراتيجية الحالي: {self.e.min_entry_confidence:g}%\nأرسل النسبة فقط\nمثال: 65\nالمسموح: 50 إلى 95'
  elif x=='maxpos':
   self.input_state[q.from_user.id]='maxpos'
   msg='📂 أرسل أقصى عدد مراكز فقط\nمثال: 5\nالمسموح: 1 إلى 10'
  elif x=='maxloss':
   self.input_state[q.from_user.id]='maxloss'
   msg='❌ أرسل حد الخسائر فقط\nمثال: 3\n0 = تعطيل الحد\nالمسموح: 0 إلى 20'
  elif x=='dailyloss':
   self.input_state[q.from_user.id]='dailyloss'
   msg=f'📉 حد Equity اليومي الحالي: {self.e.daily_loss_limit_pct:g}%\nأرسل النسبة فقط\n0 = تعطيل الحد\nالمسموح: 0 إلى 100'
  elif x=='maxdaily':
   self.input_state[q.from_user.id]='maxdaily'
   msg=f'📅 حد الصفقات اليومي الحالي: {self.e.max_daily_trades}\nأرسل الرقم فقط\n0 = معطل\nالمسموح: 0 إلى 200'
  elif x=='maxcorr':
   self.input_state[q.from_user.id]='maxcorr'
   msg=f'🔗 حد المراكز المترابطة الحالي: {self.e.max_correlated_positions}\nأرسل الرقم فقط\n0 = معطل\nالمسموح: 0 إلى 10'
  elif x=='sessionprofit':
   self.input_state[q.from_user.id]='sessionprofit'
   msg=f'🎯 حد ربح الجلسة الحالي: ${self.e.session_profit_limit:g}\nأرسل المبلغ فقط\n0 = معطل'
  elif x=='real_lock':
   import MetaTrader5 as mt5
   a=self.e.gw.account()
   if not a or getattr(a,'trade_mode',None)!=getattr(mt5,'ACCOUNT_TRADE_MODE_REAL',2):
    msg='⚠️ هذا الخيار يعمل فقط مع حساب MT5 Real.'
   elif self.e.running:
    msg='⏹ أوقف المحرك أولاً قبل تغيير قفل تداول Real.'
   elif self.e.real_trading_enabled:
    self.e.real_trading_enabled=False
    await self.e.save_setting('real_trading_enabled',0)
    await self.db.log('REAL_TRADING_LOCK_CHANGED',login=int(a.login),enabled=False,source='telegram')
    msg='🔒 تم قفل تداول Real لهذا الحساب.'
   else:
    self.login_state[q.from_user.id]={'step':'confirm_real'}
    msg='⚠️ تأكيد تداول حقيقي\n━━━━━━━━━━━━━━\nهذا الحساب Real وسيتم استخدام أموال حقيقية عند تشغيل المحرك.\nللتفعيل اكتب بالضبط: تفعيل REAL\nأو /cancel للإلغاء.'
  elif x=='live': msg='ℹ️ إدارة Real موجودة داخل 👤 الحساب عند الاتصال بحساب حقيقي.'
  else: msg='⚠️ هذا الزر غير مفعّل بعد.'
  await self._edit(q,msg,reply_markup=self.kb())
 async def cancel(self,u,c):
  if not self.allowed(u.effective_user) or u.effective_chat.id!=settings.telegram_allowed_user_id:return
  self.login_state.pop(u.effective_user.id,None)
  self.input_state.pop(u.effective_user.id,None)
  await u.message.reply_text('✅ أُلغيت العملية الحالية. بقي حساب MT5 محفوظاً ومتصلًا.',reply_markup=self.kb())
 async def text(self,u,c):
  if not self.allowed(u.effective_user) or not u.message or u.effective_chat.id!=settings.telegram_allowed_user_id:return
  uid=u.effective_user.id
  value=(u.message.text or '').strip()
  self.message_ids.add((u.effective_chat.id,u.message.message_id))

  key=self.input_state.pop(uid,None)
  if key:
   try:
    import math
    if key in ('risk','confidence','minentry','maxpos','maxdaily','maxcorr','maxloss','dailyloss','sessionprofit'):
     msg=await self._apply_setting(key,value)
    elif key=='symbol':
     self.e.symbol=value.upper(); self.e.symbols=[self.e.symbol]; import json; await self.e.save_setting('symbols',json.dumps(self.e.symbols)); msg=f'✅ الرمز: {self.e.symbol}'
    elif key=='symbols':
     import json
     wanted=value.upper().split()
     available=[z.name for z in self.e.gw.available_symbols()]
     selected=[]
     for wanted_symbol in wanted:
      exact=[n for n in available if n.upper()==wanted_symbol]
      matches=exact or [n for n in available if wanted_symbol in n.upper()]
      if matches and matches[0] not in selected: selected.append(matches[0])
     if not selected: raise ValueError()
     self.e.symbols=selected; self.e.symbol=selected[0]
     await self.e.save_setting('symbols',json.dumps(selected)); msg='✅ الأزواج: '+', '.join(selected)
    else:
     return
    m=await u.message.reply_text(msg,reply_markup=self.kb())
    self.message_ids.add((u.effective_chat.id,m.message_id))
   except (ValueError,TypeError):
    m=await u.message.reply_text('❌ قيمة غير صحيحة. اضغط الأمر وحاول مرة أخرى.',reply_markup=self.kb())
    self.message_ids.add((u.effective_chat.id,m.message_id))
   return

  st=self.login_state.get(uid)
  if not st:return
  if st.get('step')=='confirm_real':
   if value.strip().upper()!='تفعيل REAL':
    return await u.message.reply_text('❌ لم يتم التفعيل. اكتب بالضبط: تفعيل REAL أو استخدم /cancel.')
   import MetaTrader5 as mt5
   a=self.e.gw.account()
   if not a or getattr(a,'trade_mode',None)!=getattr(mt5,'ACCOUNT_TRADE_MODE_REAL',2):
    self.login_state.pop(uid,None)
    return await u.message.reply_text('⚠️ الحساب الحالي ليس Real. لم يتم تغيير القفل.')
   self.login_state.pop(uid,None)
   self.e.real_trading_enabled=True
   await self.e.save_setting('real_trading_enabled',1)
   await self.db.log('REAL_TRADING_LOCK_CHANGED',login=int(a.login),enabled=True,source='telegram')
   return await u.message.reply_text('🔓 تم تفعيل تداول Real لهذا الحساب. لن يبدأ التداول حتى تضغط تشغيل المحرك.',reply_markup=self.account_kb())
  if st.get('step')=='block':
   import re,asyncio
   fields={}
   labels={'server':'server','سيرفر':'server','الخادم':'server','خادم':'server','login':'login','لوقن':'login','دخول':'login','الحساب':'login','حساب':'login','password':'password','pass':'password','باسورد':'password','كلمة المرور':'password'}
   label_alt='|'.join(sorted((re.escape(k) for k in labels),key=len,reverse=True))
   pattern=re.compile(rf'(?is)(?<!\w)({label_alt})\s*[:=\-]?\s*(.*?)(?=\s+(?:{label_alt})\s*[:=\-]?|$)')
   for m in pattern.finditer(value):
    key=labels[m.group(1).lower().strip()];val=m.group(2).strip()
    if val:fields[key]=val
   if len(fields)<3:
    lines=[x.strip() for x in value.splitlines() if x.strip()]
    if len(lines)==3 and not fields:
     numeric=[(i,x) for i,x in enumerate(lines) if x.isdigit()]
     if len(numeric)==1:
      li,login=numeric[0]
      remaining=[(i,x) for i,x in enumerate(lines) if i!=li]
      server_item=next(((i,x) for i,x in remaining if any(c.isalpha() for c in x) and ('-' in x or 'demo' in x.lower() or 'real' in x.lower())),remaining[0])
      server=server_item[1]
      secret=next(x for i,x in remaining if i!=server_item[0])
      fields={'server':server,'login':login,'password':secret}
   login=fields.get('login','').strip();server=fields.get('server','').strip();secret=fields.get('password','').strip()
   if not login.isdigit() or not server or not secret:return await u.message.reply_text('❌ ما قدرت أحدد Server و Login و Password بأمان. أرسلها بأي ترتيب مع أسمائها، أو 3 أسطر: Server ثم Login ثم Password.')
   self.login_state.pop(uid,None)
   try:await u.message.delete()
   except Exception:pass
   wait=await c.bot.send_message(u.effective_chat.id,'⏳ جاري تشغيل MT5 وتسجيل الدخول.')
   self.menu_chat_id=u.effective_chat.id;self.menu_message_id=wait.message_id
   if self._mt5_login_task and not self._mt5_login_task.done():
    secret=None
    return await wait.edit_text('⏳ توجد محاولة تسجيل دخول MT5 جارية بالفعل. انتظر انتهاءها قبل محاولة أخرى.')
   started_at=asyncio.get_running_loop().time()
   task=asyncio.create_task(asyncio.to_thread(self.e.gw.login,int(login),secret,server));self._mt5_login_task=task;dots=1
   while not task.done() and asyncio.get_running_loop().time()-started_at<30:
    try:await wait.edit_text('⏳ جاري تشغيل MT5 وتسجيل الدخول'+'.'*dots)
    except Exception:pass
    dots=1 if dots>=4 else dots+1
    try:await asyncio.wait_for(asyncio.shield(task),timeout=1.2)
    except asyncio.TimeoutError:pass
   if not task.done():
    secret=None
    self.login_state.pop(uid,None)
    await self.db.log('MT5_LOGIN_TIMEOUT',login=int(login),server=server)
    return await wait.edit_text('⏱ انتهت مهلة اتصال MT5 بعد 30 ثانية. Terminal لا يستطيع الوصول إلى خادم الوسيط بهذه النسخة حاليًا. لم يتم حفظ بيانات الدخول.')
   ok,err,a=await task
   self._mt5_login_task=None
   if ok:
    from pathlib import Path
    import json,os
    cred_file=Path.home()/'.mt5bot_credentials.json';cred_file.write_text(json.dumps({'login':int(login),'server':server,'password':secret}));os.chmod(cred_file,0o600)
    # New/different account gets its own defaults or its previously saved profile.
    await self.e.load_settings(login=int(login),migrate_legacy=False)
    await self.db.log('MT5_LOGIN_SUCCESS',login=int(login),server=server);secret=None
    import MetaTrader5 as mt5
    is_demo=getattr(a,'trade_mode',None)==mt5.ACCOUNT_TRADE_MODE_DEMO
    mode='Demo 🧪' if is_demo else 'Real 💵'
    lock='' if is_demo else '\n🔒 تداول Real: مقفل افتراضيًا'
    await wait.edit_text(f'👤 حساب MT5\n━━━━━━━━━━━━━━\n✅ تم الاتصال بنجاح\n🆔 الحساب: {a.login}\n🌐 الخادم: {a.server}\n📌 النوع: {mode}{lock}',reply_markup=self.account_kb())
   else:
    secret=None;await self.db.log('MT5_LOGIN_FAILED',login=int(login),server=server,error=str(err))
    await wait.edit_text(f'👤 حساب MT5\n━━━━━━━━━━━━━━\n❌ فشل تسجيل الدخول\n📡 الخطأ: {err}\n🔐 لم يتم حفظ بيانات الدخول.',reply_markup=self.account_kb())
   self._arm_menu_expiry(c.bot,wait.chat_id,wait.message_id)
 async def ask_value(self,u,key,prompt):
  if not self.allowed(u.effective_user) or u.effective_chat.id!=settings.telegram_allowed_user_id:return
  self.input_state[u.effective_user.id]=key
  m=await u.message.reply_text(prompt)
  self.message_ids.add((u.effective_chat.id,m.message_id))
  self.message_ids.add((u.effective_chat.id,u.message.message_id))

 async def risk_prompt(self,u,c):
  await self.ask_value(u,'risk','⚠️ أرسل نسبة المخاطرة فقط\nمثال: 15\nالمسموح: 0.25 إلى 50')

 async def confidence_prompt(self,u,c):
  await self.ask_value(u,'confidence','🎯 أرسل نسبة الثقة فقط\nمثال: 75\nالمسموح: 50 إلى 95')

 async def minentry_prompt(self,u,c):
  await self.ask_value(u,'minentry','🎚️ أرسل حد دخول الاستراتيجية فقط\nمثال: 65\nالمسموح: 50 إلى 95')

 async def maxpos_prompt(self,u,c):
  await self.ask_value(u,'maxpos','📂 أرسل أقصى عدد مراكز فقط\nمثال: 5\nالمسموح: 1 إلى 10')

 async def maxloss_prompt(self,u,c):
  await self.ask_value(u,'maxloss','❌ أرسل حد الخسائر المتتالية فقط\n0 = معطل\nالمسموح: 0 إلى 20')


 async def dailyloss_prompt(self,u,c):
  await self.ask_value(u,'dailyloss','📉 أرسل حد انخفاض Equity اليومي\n0 = معطل\nالمسموح: 0 إلى 100')

 async def maxdaily_prompt(self,u,c):
  await self.ask_value(u,'maxdaily','📅 أرسل حد الصفقات اليومي\n0 = معطل\nالمسموح: 0 إلى 200')

 async def maxcorr_prompt(self,u,c):
  await self.ask_value(u,'maxcorr','🔗 أرسل حد المراكز المترابطة\n0 = معطل\nالمسموح: 0 إلى 10')

 async def sessionprofit_prompt(self,u,c):
  await self.ask_value(u,'sessionprofit','🎯 أرسل حد ربح الجلسة بالدولار\n0 = معطل')

 async def symbol_prompt(self,u,c):
  await self.ask_value(u,'symbol','💱 أرسل رمز واحد فقط\nمثال: EURUSD')

 async def symbols_prompt(self,u,c):
  await self.ask_value(u,'symbols','📊 أرسل الرموز مفصولة بمسافة\nمثال:\nEURUSD GBPUSD XAUUSD')

 async def clean(self,u,c):
  if not self.allowed(u.effective_user) or u.effective_chat.id!=settings.telegram_allowed_user_id:return
  chat=u.effective_chat.id
  self.message_ids.add((chat,u.message.message_id))
  deleted=0
  for cid,mid in sorted(list(self.message_ids),key=lambda x:x[1],reverse=True):
   if cid!=chat: continue
   try:
    await c.bot.delete_message(chat_id=cid,message_id=mid)
    deleted+=1
   except Exception:
    pass
  self.message_ids={x for x in self.message_ids if x[0]!=chat}
  m=await c.bot.send_message(chat,'🧹 تم تنظيف الرسائل المسجلة.\n\n'+await self.e.status(),reply_markup=self.kb())
  self.message_ids.add((chat,m.message_id))

 async def symbol(self,u,c):
  if self.allowed(u.effective_user) and c.args:
   import json
   wanted=c.args[0].upper()
   available=[z.name for z in self.e.gw.available_symbols()]
   exact=[n for n in available if n.upper()==wanted]
   matches=exact or [n for n in available if wanted in n.upper()]
   if not matches:return await u.message.reply_text('❌ لم يتم العثور على الرمز.',reply_markup=self.kb())
   self.e.symbol=matches[0];self.e.symbols=[self.e.symbol]
   await self.e.save_setting('symbols',json.dumps(self.e.symbols))
   await u.message.reply_text(f'الرمز ← {self.e.symbol}',reply_markup=self.kb())
 async def risk(self,u,c):
  try:
   v=float(c.args[0]); assert 0.25<=v<=50
   self.e.risk_pct=v; await self.e.save_setting('risk_pct',v)
   await u.message.reply_text(f'✅ المخاطرة: {v:g}%')
  except: await u.message.reply_text('استخدم /risk 0.25 (من 0.25 إلى 50)')

 async def confidence(self,u,c):
  if not self.allowed(u.effective_user): return
  try:
   v=float(c.args[0]); assert 50<=v<=95
   self.e.min_confidence=v
   await self.e.save_setting('min_confidence',v)
   await u.message.reply_text(f'✅ الحد الأدنى للثقة: {v:g}%')
  except:
   await u.message.reply_text('استخدم /confidence 75 (من 50 إلى 95)')

 async def minentry(self,u,c):
  try:
   msg=await self._apply_setting('minentry',c.args[0])
   await u.message.reply_text(msg)
  except (ValueError,TypeError,IndexError): await u.message.reply_text('استخدم /minentry 65 (من 50 إلى 95)')

 async def maxpos(self,u,c):
  try:
   v=int(c.args[0]); assert 1<=v<=10
   self.e.max_positions=v; await self.e.save_setting('max_positions',v)
   await u.message.reply_text(f'✅ حد المراكز: {v}')
  except: await u.message.reply_text('استخدم /maxpos 3 (من 1 إلى 10)')

 async def maxloss(self,u,c):
  try:
   v=int(c.args[0]); assert 0<=v<=20
   self.e.max_consecutive_losses=v; self.e.loss_limit_notified=False
   await self.e.save_setting('max_consecutive_losses',v)
   await u.message.reply_text(f'✅ حد الخسائر المتتالية: {v}')
  except: await u.message.reply_text('استخدم /maxloss 0 (0 = معطل، من 1 إلى 20 = حد الخسائر)')

 async def symbols(self,u,c):
  import json
  wanted=[x.upper() for x in c.args]
  if not wanted:
   await u.message.reply_text('استخدم: /symbols EURUSD GBPUSD XAUUSD')
   return
  available=[z.name for z in self.e.gw.available_symbols()]
  selected=[]
  for key in wanted:
   exact=[n for n in available if n.upper()==key]
   matches=exact or [n for n in available if key in n.upper()]
   if matches and matches[0] not in selected:
    selected.append(matches[0])
  if not selected:
   await u.message.reply_text('❌ لم يتم العثور على الرموز.')
   return
  self.e.symbols=selected
  self.e.symbol=selected[0]
  await self.e.save_setting('symbols',json.dumps(selected))
  await u.message.reply_text('✅ الأزواج المختارة: '+', '.join(selected))

 def app(self):
  a=Application.builder().token(settings.telegram_bot_token).build()
  a.add_handler(CommandHandler('start',self.start))
  a.add_handler(CommandHandler('cancel',self.cancel))
  a.add_handler(CommandHandler('clean',self.clean))
  a.add_handler(CommandHandler('symbol',self.symbol_prompt))
  a.add_handler(CommandHandler('symbols',self.symbols_prompt))
  a.add_handler(CommandHandler('risk',self.risk_prompt))
  a.add_handler(CommandHandler('confidence',self.confidence_prompt))
  a.add_handler(CommandHandler('minentry',self.minentry_prompt))
  a.add_handler(CommandHandler('maxpos',self.maxpos_prompt))
  a.add_handler(CommandHandler('maxloss',self.maxloss_prompt))
  a.add_handler(CommandHandler('dailyloss',self.dailyloss_prompt))
  a.add_handler(CommandHandler('maxdaily',self.maxdaily_prompt))
  a.add_handler(CommandHandler('maxcorr',self.maxcorr_prompt))
  a.add_handler(CommandHandler('sessionprofit',self.sessionprofit_prompt))
  a.add_handler(CallbackQueryHandler(self.cb))
  a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,self.text))
  return a
