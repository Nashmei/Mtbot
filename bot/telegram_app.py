from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import Application,CommandHandler,CallbackQueryHandler,MessageHandler,ContextTypes,filters
from telegram.error import BadRequest,RetryAfter
from core.config import settings
from bot import v2_views
class TelegramUI:
 def __init__(self,engine,db): self.e=engine;self.db=db;self.login_state={};self.input_state={};self.message_ids=set();self.menu_message_id=None;self.menu_chat_id=None;self.menu_expiry_task=None
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
  elif key=='protection':
   v=float(value)
   if not math.isfinite(v) or not 5<=v<=90: raise ValueError()
   self.e.protection_pct=v; db_key='protection_pct'; msg=f'✅ الحماية: {v:g}%'
  elif key=='maxduration':
   v=float(value)
   if not math.isfinite(v) or not 3<=v<=240: raise ValueError()
   self.e.max_trade_minutes=v; db_key='max_trade_minutes'; msg=f'✅ حد مدة الصفقة: {v:g} دقيقة'
  elif key=='rr':
   v=float(value)
   if not math.isfinite(v) or not .5<=v<=10: raise ValueError()
   self.e.rr=v; db_key='rr'; msg=f'✅ R:R = 1:{v:g}'
  elif key=='maxpos':
   v=int(value)
   if str(v)!=str(value).strip() or not 1<=v<=10: raise ValueError()
   self.e.max_positions=v; db_key='max_positions'; msg=f'✅ حد المراكز: {v}'
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
  elif key in ('ai_rr','ai_sl','ai_tp','ai_protection','ai_trailing','ai_duration'):
   v=float(value)
   if not math.isfinite(v) or v<0: raise ValueError()
   specs={
    'ai_rr':('ai_rr_override','ai_rr_override',0.5,5.0,'R:R'),
    'ai_sl':('ai_sl_points_override','ai_sl_points_override',1.0,100000.0,'SL points'),
    'ai_tp':('ai_tp_points_override','ai_tp_points_override',1.0,100000.0,'TP points'),
    'ai_protection':('ai_protection_override','ai_protection_override',15.0,80.0,'Protection %'),
    'ai_trailing':('ai_trailing_override','ai_trailing_override',2.0,25.0,'Trailing %'),
    'ai_duration':('ai_duration_override','ai_duration_override',2.0,10.0,'Duration min'),
   }
   attr,db_key,lo,hi,label=specs[key]
   if v!=0 and not lo<=v<=hi: raise ValueError()
   setattr(self.e,attr,v)
   msg=f'✅ {label}: '+('AI' if v==0 else f'{v:g}')
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
   [InlineKeyboardButton('📊 التداول',callback_data='trade_menu'),InlineKeyboardButton('🧠 AI',callback_data='ai_center')],
   [InlineKeyboardButton('📈 الأداء',callback_data='performance'),InlineKeyboardButton('💼 الصفقات',callback_data='positions')],
   [InlineKeyboardButton('💱 الأسواق',callback_data='analysis_menu'),InlineKeyboardButton('⚙️ الإعدادات',callback_data='settings_menu')],
   [InlineKeyboardButton('🩺 صحة النظام',callback_data='health'),InlineKeyboardButton('👤 الحساب',callback_data='account_menu')]
  ])
 def trade_kb(self): return InlineKeyboardMarkup([[InlineKeyboardButton('▶️ تشغيل المحرك',callback_data='start'),InlineKeyboardButton('⏹ إيقاف',callback_data='stop')],[InlineKeyboardButton('📊 الحالة',callback_data='status'),InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]])
 def analysis_kb(self): return InlineKeyboardMarkup([[InlineKeyboardButton('🧠 آخر تحليل AI',callback_data='ai_center')],[InlineKeyboardButton('💱 الأزواج',callback_data='symbols'),InlineKeyboardButton('🔥 الأنشط',callback_data='active')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]])
 def settings_kb(self): return InlineKeyboardMarkup([
  [InlineKeyboardButton('⚠️ المخاطرة',callback_data='risk'),InlineKeyboardButton('🎯 الثقة',callback_data='confidence')],
  [InlineKeyboardButton('🧠 تحكم الصفقة AI/يدوي',callback_data='ai_controls')],
  [InlineKeyboardButton('📂 حد المراكز',callback_data='maxpos'),InlineKeyboardButton('❌ حد الخسائر',callback_data='maxloss')],
  [InlineKeyboardButton('📉 حد Equity اليومي',callback_data='dailyloss')],
  [InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]
 ])
 def ai_controls_kb(self): return InlineKeyboardMarkup([
  [InlineKeyboardButton('⚖️ R:R',callback_data='ai_rr'),InlineKeyboardButton('🛑 SL points',callback_data='ai_sl')],
  [InlineKeyboardButton('🎯 TP points',callback_data='ai_tp'),InlineKeyboardButton('🛡 Protection',callback_data='ai_protection')],
  [InlineKeyboardButton('📐 Trailing',callback_data='ai_trailing'),InlineKeyboardButton('⏱ Duration',callback_data='ai_duration')],
  [InlineKeyboardButton('♻️ الكل AI = 0',callback_data='ai_reset')],
  [InlineKeyboardButton('↩️ الإعدادات',callback_data='settings_menu')]
 ])
 def account_kb(self): return InlineKeyboardMarkup([[InlineKeyboardButton('🔐 ربط MT5',callback_data='mt5login'),InlineKeyboardButton('📈 الإحصائيات',callback_data='accountstats')],[InlineKeyboardButton('🧪 فحص الجاهزية',callback_data='readiness')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]])
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
  if x=='ai_center':
   return await self._edit(q,await v2_views.ai_center(self.e,self.db),InlineKeyboardMarkup([[InlineKeyboardButton('⚙️ تحكم AI/يدوي',callback_data='ai_controls')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='performance':
   return await self._edit(q,await v2_views.performance(self.db),InlineKeyboardMarkup([[InlineKeyboardButton('🕐 آخر 24 ساعة',callback_data='performance24')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='performance24':
   return await self._edit(q,await v2_views.performance(self.db,24),InlineKeyboardMarkup([[InlineKeyboardButton('📚 السجل الحالي',callback_data='performance')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='positions':
   return await self._edit(q,await v2_views.positions(self.e),InlineKeyboardMarkup([[InlineKeyboardButton('♻️ تحديث',callback_data='positions')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='health':
   return await self._edit(q,await v2_views.health(self.e,self.db),InlineKeyboardMarkup([[InlineKeyboardButton('♻️ تحديث',callback_data='health')],[InlineKeyboardButton('↩️ الرئيسية',callback_data='dashboard')]]))
  if x=='ai_controls':
   def z(v,suffix=''): return '🤖 AI' if float(v or 0)==0 else f'{v:g}{suffix}'
   msg=('🧠 تحكم الصفقة AI / يدوي\n━━━━━━━━━━━━━━\n0 = AI يدير القيمة تلقائياً\n\n'
        f'⚖️ R:R: {z(self.e.ai_rr_override)}\n🛑 SL: {z(self.e.ai_sl_points_override," pt")}\n'
        f'🎯 TP: {z(self.e.ai_tp_points_override," pt")}\n🛡 Protection: {z(self.e.ai_protection_override,"%")}\n'
        f'📐 Trailing: {z(self.e.ai_trailing_override,"%")}\n⏱ Duration: {z(self.e.ai_duration_override," min")}\n\n'
        'الأولوية: TP اليدوي يتقدم على R:R اليدوي.')
   return await self._edit(q,msg,self.ai_controls_kb())
  if x=='ai_reset':
   for attr,key in [('ai_rr_override','ai_rr_override'),('ai_sl_points_override','ai_sl_points_override'),('ai_tp_points_override','ai_tp_points_override'),('ai_protection_override','ai_protection_override'),('ai_trailing_override','ai_trailing_override'),('ai_duration_override','ai_duration_override')]:
    setattr(self.e,attr,0.0);await self.e.save_setting(key,0.0)
   return await self._edit(q,'✅ تم إرجاع R:R / SL / TP / Protection / Trailing / Duration إلى تحكم AI.',self.ai_controls_kb())
  if x=='analyze': return await self._edit(q,await v2_views.ai_center(self.e,self.db),self.analysis_kb())
  if x=='dashboard': return await self._edit(q,await v2_views.dashboard(self.e,self.db),self.kb())
  if x=='trade_menu': return await self._edit(q,'🤖 التداول وإدارة المحرك',self.trade_kb())
  if x=='analysis_menu': return await self._edit(q,'🔎 التحليل والأسواق',self.analysis_kb())
  if x=='settings_menu':
   daily=f'{self.e.daily_loss_limit_pct:g}%'+(' (معطل)' if self.e.daily_loss_limit_pct<=0 else '')
   losses=str(self.e.max_consecutive_losses)+(' (معطل)' if self.e.max_consecutive_losses==0 else '')
   msg=(f'⚙️ الإعدادات\n━━━━━━━━━━━━━━\n'
        f'⚠️ المخاطرة الثابتة: {self.e.risk_pct:g}%\n🎯 حد الثقة: {self.e.min_confidence:g}%\n'
        f'📂 حد المراكز: {self.e.max_positions}\n❌ حد الخسائر: {losses}\n📉 حد Equity اليومي: {daily}\n\n'
        '🧠 R:R / SL / TP / Protection / Trailing / Duration\nيمكن ترك كل قيمة 0 ليحددها AI أو وضع قيمة يدوية.')
   return await self._edit(q,msg,self.settings_kb())
  if x=='account_menu': return await self._edit(q,'👤 حساب MT5 والإحصائيات',self.account_kb())
  if x=='mt5login':
   if self.e.running:
    return await self._edit(q,'⏹ أوقف المحرك قبل تغيير حساب MT5.',self.account_kb())
   self.login_state[q.from_user.id]={'step':'block'}; msg='🔐 تسجيل دخول MT5 التجريبي\n\nأرسل Server و Login و Password في رسالة واحدة.\nاستخدم /cancel للإلغاء.'
  elif x=='start':
   if not self.e.gw.account(): msg='❌ حساب MT5 غير متصل. استخدم 🔐 حساب MT5 أولاً.'
   else:
    await self._edit(q,'⏳ جاري تشغيل المحرك.',self.trade_kb(),arm=False)
    started=await self.e.start()
    msg='🟢 تم تشغيل البوت على الحساب التجريبي.' if started else '⚠️ لم يبدأ المحرك. راجع رسالة السبب وفحص الجاهزية.'
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
         f'🧠 R:R override: {self.e.ai_rr_override:g} (0=AI)\n'
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
     if ticks is None or len(ticks)<80:
      lines.append(f'\n💱 {symbol}\n⚠️ بيانات ticks غير كافية.')
      continue
     tick_ts=float(ticks['time_msc'][-1])/1000.0 if 'time_msc' in ticks.dtype.names else float(ticks['time'][-1])
     if abs(time.time()-tick_ts)>settings.max_tick_age_seconds:
      lines.append(f'\n💱 {symbol}\n⚠️ آخر سعر قديم؛ لا توجد إشارة صالحة الآن.')
      continue
     reg,sig,meta=self.e.an.analyze(ticks,i.point,self.e.gw.rates_m5(symbol,200),symbol=symbol,rates_m15=self.e.gw.rates_m15(symbol,200),rates_h1=self.e.gw.rates_h1(symbol,200),rates_m1=self.e.gw.rates_m1(symbol,200),strategy_performance=self.e.strategy_performance,min_confidence=self.e.min_confidence)
     if sig:
      direction='شراء 🟢' if sig.side.value=='BUY' else 'بيع 🔴'
      lines.append(f'\n💱 {symbol}\n📌 الإشارة: {direction}\n🧠 الاستراتيجية: {sig.strategy}\n🎯 قوة الإشارة: {sig.confidence*100:.0f}%\n📊 السوق: {reg.value}')
     else:
      reason=str(meta.get('decision','waiting'))
      labels={'waiting_live_momentum':'انتظار زخم لحظي','waiting_momentum':'انتظار تأكيد الزخم','volatile_no_direction':'حركة قوية بلا اتجاه','direction_not_confirmed':'الاتجاه غير مؤكد'}
      lines.append(f'\n💱 {symbol}\n⚪ لا توجد فرصة حالياً\n📊 السوق: {reg.value}\n🔎 السبب: {labels.get(reason,reason)}')
      checks=meta.get('gold_checks') if reason=='gold_wait_confirmation' else meta.get('trend_checks')
      if checks:
       check_labels={'gap':'Micro gap','momentum':'الزخم','acceleration':'التسارع','pullback':'Pullback','htf':'M15/H1','direction':'الاتجاه اللحظي','expansion_range':'مدى التوسع','range_floor':'الحد الأدنى للمدى'}
       detail=['   '+('✅' if ok else '❌')+' '+check_labels.get(name,name) for name,ok in checks.items()]
       lines[-1]+='\n🧩 شروط الدخول:\n'+'\n'.join(detail)
       values=meta.get('gold_values') if reason=='gold_wait_confirmation' else meta.get('trend_values')
       if values:
        bias_name=lambda v: 'BUY' if v>0 else ('SELL' if v<0 else 'NEUTRAL')
        if reason=='gold_wait_confirmation':
         lines[-1]+=(f"\n📐 القيم: Gap {values['micro_gap']}/{values['gap_min']} | Momentum {values['momentum']} (|{values['momentum_abs']}|/{values['momentum_min']}) | Accel {values['acceleration']} (|{values['acceleration_abs']}|/{values['acceleration_min']})\n"
                     f"   Range {values['tick_range']}/{values['expansion_range_min']} | Floor {values['expansion_range_floor']} | Score {values['confirmation_score']}/{values['confirmation_required']}\n"
                     f"   Score {values['confirmation_score']}/{values['confirmation_required']} | Side {values['side']} | M15 {bias_name(values['m15_bias'])} | H1 {bias_name(values['h1_bias'])}")
        else:
         lines[-1]+=(f"\n📐 القيم: Gap {values['micro_gap']}/{values['momentum_min']} | Momentum {values['momentum']} (|{values['momentum_abs']}|/{values['momentum_min']}) | Accel {values['acceleration']} (|{values['acceleration_abs']}|/{values['acceleration_min']})\n"
                     f"   Pullback distance {values['pullback_distance_points']}pt | tolerance {values['pullback_tolerance_points']}pt | Live {values['live']} | EMA20 {values['ema20_m5']}\n"
                     f"   Side {values['side']} | M15 {bias_name(values['m15_bias'])} | H1 {bias_name(values['h1_bias'])}")
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
   rows.append([InlineKeyboardButton('↩️ القائمة الرئيسية',callback_data='status')])
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
   
  elif x in ('ai_rr','ai_sl','ai_tp','ai_protection','ai_trailing','ai_duration'):
   keymap={'ai_rr':'ai_rr','ai_sl':'ai_sl','ai_tp':'ai_tp','ai_protection':'ai_protection','ai_trailing':'ai_trailing','ai_duration':'ai_duration'}
   self.input_state[q.from_user.id]=keymap[x]
   labels={'ai_rr':'R:R (0=AI، يدوي 0.5-5)','ai_sl':'SL points (0=AI)','ai_tp':'TP points (0=AI)','ai_protection':'Protection % (0=AI، يدوي 15-80)','ai_trailing':'Trailing % (0=AI، يدوي 2-25)','ai_duration':'Duration minutes (0=AI، يدوي 2-10)'}
   msg='🧠 '+labels[x]+'\nأرسل الرقم فقط.'
  elif x=='risk':
   self.input_state[q.from_user.id]='risk'
   msg='⚠️ أرسل نسبة المخاطرة فقط\nمثال: 15\nالمسموح: 0.25 إلى 50'
  elif x=='confidence':
   self.input_state[q.from_user.id]='confidence'
   msg=f'🎯 الثقة الحالية: {self.e.min_confidence:g}%\nأرسل النسبة فقط\nمثال: 75\nالمسموح: 50 إلى 95'
  elif x=='protection':
   self.input_state[q.from_user.id]='protection'
   msg='🛡️ أرسل نسبة بدء الحماية فقط\nمثال: 20\nالمسموح: 5 إلى 90'
  elif x=='maxduration':
   self.input_state[q.from_user.id]='maxduration'
   msg=f'⏱ الحد الحالي: {self.e.max_trade_minutes:g} دقيقة\nأرسل عدد الدقائق\nالمسموح: 3 إلى 240'
  elif x=='maxpos':
   self.input_state[q.from_user.id]='maxpos'
   msg='📂 أرسل أقصى عدد مراكز فقط\nمثال: 5\nالمسموح: 1 إلى 10'
  elif x=='maxloss':
   self.input_state[q.from_user.id]='maxloss'
   msg='❌ أرسل حد الخسائر فقط\nمثال: 3\n0 = تعطيل الحد\nالمسموح: 0 إلى 20'
  elif x=='dailyloss':
   self.input_state[q.from_user.id]='dailyloss'
   msg=f'📉 حد Equity اليومي الحالي: {self.e.daily_loss_limit_pct:g}%\nأرسل النسبة فقط\n0 = تعطيل الحد\nالمسموح: 0 إلى 100'
  elif x=='rr':
   self.input_state[q.from_user.id]='rr'
   msg='⚖️ أرسل الرقم فقط\nمثال: 3 يعني 1:3\nالمسموح: 0.5 إلى 10'
  elif x=='live': msg='🔒 التداول الحقيقي مقفل في نسخة الأمان الحالية.'
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
    if key in ('risk','confidence','protection','maxduration','rr','maxpos','maxloss','dailyloss','ai_rr','ai_sl','ai_tp','ai_protection','ai_trailing','ai_duration'):
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
  if st.get('step')=='block':
   import re,asyncio
   fields={}
   for line in value.splitlines():
    m=re.match(r'^\s*(server|login|password)\s*:\s*(.*?)\s*$',line,re.I)
    if m:fields[m.group(1).lower()]=m.group(2)
   login=fields.get('login','').strip();server=fields.get('server','').strip();secret=fields.get('password','')
   if not login.isdigit() or not server or not secret:return await u.message.reply_text('❌ البيانات ناقصة. أرسل Server و Login و Password في رسالة واحدة.')
   self.login_state.pop(uid,None)
   try:await u.message.delete()
   except Exception:pass
   wait=await c.bot.send_message(u.effective_chat.id,'⏳ جاري تشغيل MT5 وتسجيل الدخول.')
   self.menu_chat_id=u.effective_chat.id;self.menu_message_id=wait.message_id
   task=asyncio.create_task(asyncio.to_thread(self.e.gw.login,int(login),secret,server));dots=1
   while not task.done():
    try:await wait.edit_text('⏳ جاري تشغيل MT5 وتسجيل الدخول'+'.'*dots)
    except Exception:pass
    dots=1 if dots>=4 else dots+1
    try:await asyncio.wait_for(asyncio.shield(task),timeout=1.2)
    except asyncio.TimeoutError:pass
   ok,err,a=await task
   if ok:
    from pathlib import Path
    import json,os
    cred_file=Path.home()/'.mt5bot_credentials.json';cred_file.write_text(json.dumps({'login':int(login),'server':server,'password':secret}));os.chmod(cred_file,0o600)
    # New/different account gets its own defaults or its previously saved profile.
    await self.e.load_settings(login=int(login),migrate_legacy=False)
    await self.db.log('MT5_LOGIN_SUCCESS',login=int(login),server=server);secret=None
    await wait.edit_text(f'👤 حساب MT5\n━━━━━━━━━━━━━━\n✅ تم الاتصال بنجاح\n🆔 الحساب: {a.login}\n🌐 الخادم: {a.server}\n🔒 الوضع: تجريبي محمي',reply_markup=self.account_kb())
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

 async def protection_prompt(self,u,c):
  await self.ask_value(u,'protection','🛡️ أرسل نسبة بدء الحماية فقط\nمثال: 20\nالمسموح: 5 إلى 90')

 async def rr_prompt(self,u,c):
  await self.ask_value(u,'rr','⚖️ أرسل R:R فقط\nمثال: 3 يعني 1:3\nالمسموح: 0.5 إلى 10')

 async def maxpos_prompt(self,u,c):
  await self.ask_value(u,'maxpos','📂 أرسل أقصى عدد مراكز فقط\nمثال: 5\nالمسموح: 1 إلى 10')

 async def maxloss_prompt(self,u,c):
  await self.ask_value(u,'maxloss','❌ أرسل حد الخسائر المتتالية فقط\n0 = معطل\nالمسموح: 0 إلى 20')

 async def maxduration_prompt(self,u,c):
  await self.ask_value(u,'maxduration','⏱ أرسل حد مدة الصفقة بالدقائق\nالمسموح: 3 إلى 240')

 async def dailyloss_prompt(self,u,c):
  await self.ask_value(u,'dailyloss','📉 أرسل حد انخفاض Equity اليومي\n0 = معطل\nالمسموح: 0 إلى 100')

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
  if self.allowed(u.effective_user) and c.args: self.e.symbol=c.args[0].upper();await self.e.save_setting('symbols','[\"'+self.e.symbol+'\"]');await u.message.reply_text(f'الرمز ← {self.e.symbol}',reply_markup=self.kb())
 async def rr(self,u,c):
  if self.allowed(u.effective_user) and c.args:
   try:v=float(c.args[0])
   except ValueError:return await u.message.reply_text('مثال: /rr 3')
   if not .5<=v<=10:return await u.message.reply_text('يجب أن تكون النسبة بين 0.5 و10')
   self.e.rr=v;await self.e.save_setting('rr',v);await u.message.reply_text(f'العائد/المخاطرة ← 1:{v:g}',reply_markup=self.kb())
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

 async def protection(self,u,c):
  try:
   v=float(c.args[0]); assert 5<=v<=90
   self.e.protection_pct=v; await self.e.save_setting('protection_pct',v)
   await u.message.reply_text(f'✅ الحماية تبدأ عند: {v:g}%')
  except: await u.message.reply_text('استخدم /protection 45 (من 5 إلى 90)')

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
  a.add_handler(CommandHandler('rr',self.rr_prompt))
  a.add_handler(CommandHandler('risk',self.risk_prompt))
  a.add_handler(CommandHandler('confidence',self.confidence_prompt))
  a.add_handler(CommandHandler('protection',self.protection_prompt))
  a.add_handler(CommandHandler('maxpos',self.maxpos_prompt))
  a.add_handler(CommandHandler('maxloss',self.maxloss_prompt))
  a.add_handler(CommandHandler('maxduration',self.maxduration_prompt))
  a.add_handler(CommandHandler('dailyloss',self.dailyloss_prompt))
  a.add_handler(CallbackQueryHandler(self.cb))
  a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,self.text))
  return a
