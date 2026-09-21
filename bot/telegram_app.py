from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import Application,CommandHandler,CallbackQueryHandler,MessageHandler,ContextTypes,filters
from telegram.error import BadRequest,RetryAfter
from core.config import settings
class TelegramUI:
 def __init__(self,engine,db): self.e=engine;self.db=db;self.login_state={};self.input_state={};self.message_ids=set()
 def allowed(self,u): return bool(u and u.id==settings.telegram_allowed_user_id)
 async def _edit(self,q,text,reply_markup=None):
  try:
   await q.edit_message_text(text,reply_markup=reply_markup)
  except (BadRequest,RetryAfter):
   return
 def kb(self):
  return InlineKeyboardMarkup([
   [InlineKeyboardButton('📊 الحالة',callback_data='status'),InlineKeyboardButton('🔎 تحليل الآن',callback_data='analyze')],
   [InlineKeyboardButton('📈 إحصائيات حسابي',callback_data='accountstats')],
   [InlineKeyboardButton('▶️ تشغيل',callback_data='start'),InlineKeyboardButton('⏹ إيقاف',callback_data='stop')],
   [InlineKeyboardButton('💱 الأزواج',callback_data='symbols'),InlineKeyboardButton('⚖️ R:R',callback_data='rr')],
   [InlineKeyboardButton('⚠️ المخاطرة',callback_data='risk'),InlineKeyboardButton('🛡 الحماية',callback_data='protection')],
   [InlineKeyboardButton('🎯 الثقة',callback_data='confidence')],
   [InlineKeyboardButton('📂 حد المراكز',callback_data='maxpos'),InlineKeyboardButton('❌ حد الخسائر',callback_data='maxloss')],
   [InlineKeyboardButton('🔐 حساب MT5',callback_data='mt5login')],
   [InlineKeyboardButton('🔒 الحقيقي مقفل',callback_data='live')]
  ])
 async def start(self,u,c):
  if not self.allowed(u.effective_user):return
  a=self.e.gw.account(); state=f'MT5: ✅ {a.login} / {a.server}' if a else 'MT5: ❌ غير مسجل الدخول\nاستخدم 🔐 حساب MT5'
  await u.message.reply_text(state+'\n\n'+await self.e.status(),reply_markup=self.kb())
 async def cb(self,u,c):
  q=u.callback_query
  if not self.allowed(q.from_user):return
  await q.answer(); x=q.data
  if x=='mt5login':
   self.login_state[q.from_user.id]={'step':'login'}; msg='🔐 تسجيل دخول MT5 التجريبي\n\nأرسل رقم حساب MT5.\nاستخدم /cancel للإلغاء.'
  elif x=='start':
   if not self.e.gw.account(): msg='❌ حساب MT5 غير متصل. استخدم 🔐 حساب MT5 أولاً.'
   else: await self.e.start(); msg='🟢 تم تشغيل البوت على الحساب التجريبي.'
  elif x=='stop': await self.e.stop(); msg='⏹ تم إيقاف البوت.'
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
         f'🎯 R:R: 1:{self.e.rr:g}\n'
         f'❌ خسائر متتالية: {self.e.consecutive_losses}/{self.e.max_consecutive_losses}')
  elif x=='analyze':
   if not self.e.gw.account(): msg='❌ سجّل الدخول إلى MT5 أولاً.'
   else:
    lines=['🔎 تحليل الأزواج المختارة']
    for symbol in self.e.symbols:
     i=self.e.gw.info(symbol)
     if not i: continue
     reg,sig,meta=self.e.an.analyze(self.e.gw.ticks(symbol),i.point,self.e.gw.rates_m5(symbol,200))
     tick=self.e.gw.tick(symbol)
     tick_age=max(0.0,__import__('time').time()-float(getattr(tick,'time_msc',0) or 0)/1000.0) if tick else 9999.0
     price=float(getattr(tick,'bid',0) or 0) if tick else float(meta.get('price',0) or 0)
     decision=str(meta.get('decision','-'))
     if sig:
      direction='شراء' if sig.side.value=='BUY' else 'بيع'
      lines.append(f'{symbol}: {direction} | {sig.strategy} | {sig.confidence*100:.0f}% | {price:g} | tick {tick_age:.1f}s')
     else:
      lines.append(f'{symbol}: {reg.value} | {decision} | {price:g} | tick {tick_age:.1f}s')
    msg='\n'.join(lines)
  elif x=='symbols':
   symbols=self.e.gw.available_symbols()
   names=[z.name for z in symbols]
   popular=[]
   keys=('XAUUSD','EURUSD','GBPUSD','USDJPY','USDCHF','USDCAD','AUDUSD','NZDUSD')
   for key in keys:
    exact=[n for n in names if n.upper()==key]
    matches=exact or [n for n in names if key in n.upper()]
    if matches: popular.append(matches[0])
   popular=list(dict.fromkeys(popular))[:12]
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
   import time
   candidates=('XAUUSD','EURUSD','GBPUSD','USDJPY','USDCHF','USDCAD','AUDUSD','NZDUSD')
   names=[z.name for z in self.e.gw.available_symbols()]
   ranked=[]
   for key in candidates:
    exact=[n for n in names if n.upper()==key]
    matches=exact or [n for n in names if key in n.upper()]
    if not matches: continue
    n=matches[0]; info=self.e.gw.info(n); t=self.e.gw.tick(n)
    ticks=self.e.gw.ticks(n,300)
    if not info or not t or not info.point or ticks is None or len(ticks)<2: continue
    bids=[float(z['bid']) for z in ticks if float(z['bid'])>0]
    if len(bids)<2: continue
    move=(max(bids)-min(bids))/info.point
    spread=(t.ask-t.bid)/info.point
    if spread <= 0:
     continue
    score=move/spread
    ranked.append((score,n,move,spread))
   ranked.sort(reverse=True)
   rows=[]
   for score,n,move,spread in ranked[:6]:
    icon='🥇' if 'XAU' in n.upper() else '🔥'
    rows.append([InlineKeyboardButton(f'{icon} {n} • move {move:.0f} • spread {spread:.1f}',callback_data=f'sym:{n}')])
   rows.append([InlineKeyboardButton('↩️ الرموز',callback_data='symbols')])
   text='🔥 الأنشط الآن\nالترتيب حسب حركة السعر الأخيرة ÷ السبريد.\nهذا مقياس للنشاط فقط وليس توقعاً للربحية.'
   if not ranked: text='⚠️ لا توجد بيانات لحظية كافية حالياً.'
   await self._edit(q,text,reply_markup=InlineKeyboardMarkup(rows))
   return
  elif x.startswith('sym:'):
   symbol=x.split(':',1)[1]
   if symbol in self.e.symbols:
    self.e.symbols.remove(symbol)
   else:
    self.e.symbols.append(symbol)
   self.e.symbol=self.e.symbols[0] if self.e.symbols else settings.default_symbol
   import json
   await self.db.set('symbols',json.dumps(self.e.symbols))
   msg=f'✅ الأزواج المختارة: {", ".join(self.e.symbols) if self.e.symbols else "لا يوجد"}'
   await self._edit(q,msg,reply_markup=self.kb())
   return
   
  elif x=='risk':
   self.input_state[q.from_user.id]='risk'
   msg='⚠️ أرسل نسبة المخاطرة فقط\nمثال: 15\nالمسموح: 0.25 إلى 50'
  elif x=='confidence':
   self.input_state[q.from_user.id]='confidence'
   msg=f'🎯 الثقة الحالية: {self.e.min_confidence:g}%\nأرسل النسبة فقط\nمثال: 75\nالمسموح: 50 إلى 95'
  elif x=='protection':
   self.input_state[q.from_user.id]='protection'
   msg='🛡️ أرسل نسبة بدء الحماية فقط\nمثال: 20\nالمسموح: 5 إلى 90'
  elif x=='maxpos':
   self.input_state[q.from_user.id]='maxpos'
   msg='📂 أرسل أقصى عدد مراكز فقط\nمثال: 5\nالمسموح: 1 إلى 10'
  elif x=='maxloss':
   self.input_state[q.from_user.id]='maxloss'
   msg='❌ أرسل حد الخسائر فقط\nمثال: 3\n0 = تعطيل الحد\nالمسموح: 0 إلى 20'
  elif x=='rr':
   self.input_state[q.from_user.id]='rr'
   msg='⚖️ أرسل الرقم فقط\nمثال: 3 يعني 1:3\nالمسموح: 0.5 إلى 10'
  elif x=='live': msg='🔒 التداول الحقيقي مقفل في نسخة الأمان الحالية.'
  else: msg='⚠️ هذا الزر غير مفعّل بعد.'
  await self._edit(q,msg,reply_markup=self.kb())
 async def cancel(self,u,c):
  if not self.allowed(u.effective_user): return
  from pathlib import Path
  import MetaTrader5 as mt5
  self.login_state.pop(u.effective_user.id,None)
  self.input_state.pop(u.effective_user.id,None)
  f=Path.home()/'.mt5bot_credentials.json'
  try:
   if f.exists(): f.unlink()
  except Exception:
   pass
  mt5.shutdown()
  await u.message.reply_text('🗑️ تم حذف بيانات حساب MT5 المحفوظة وتسجيل الخروج.',reply_markup=self.kb())
 async def text(self,u,c):
  if not self.allowed(u.effective_user) or not u.message:return
  uid=u.effective_user.id
  value=(u.message.text or '').strip()
  self.message_ids.add((u.effective_chat.id,u.message.message_id))

  key=self.input_state.pop(uid,None)
  if key:
   try:
    if key=='risk':
     v=float(value)
     if not 0.25<=v<=50: raise ValueError()
     self.e.risk_pct=v; await self.db.set('risk_pct',v); msg=f'✅ المخاطرة: {v:g}%'
    elif key=='confidence':
     v=float(value)
     if not 50<=v<=95: raise ValueError()
     self.e.min_confidence=v; await self.db.set('min_confidence',v); msg=f'✅ الثقة: {v:g}%'
    elif key=='protection':
     v=float(value)
     if not 5<=v<=90: raise ValueError()
     self.e.protection_pct=v; await self.db.set('protection_pct',v); msg=f'✅ الحماية: {v:g}%'
    elif key=='rr':
     v=float(value)
     if not .5<=v<=10: raise ValueError()
     self.e.rr=v; await self.db.set('rr',v); msg=f'✅ R:R = 1:{v:g}'
    elif key=='maxpos':
     v=int(value)
     if not 1<=v<=10: raise ValueError()
     self.e.max_positions=v; await self.db.set('max_positions',v); msg=f'✅ حد المراكز: {v}'
    elif key=='maxloss':
     v=int(value)
     if not 0<=v<=20: raise ValueError()
     self.e.max_consecutive_losses=v; self.e.loss_limit_notified=False
     await self.db.set('max_consecutive_losses',v); msg=f'✅ حد الخسائر: {v}'+(' (معطل)' if v==0 else '')
    elif key=='symbol':
     self.e.symbol=value.upper(); await self.db.set('symbol',self.e.symbol); msg=f'✅ الرمز: {self.e.symbol}'
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
     await self.db.set('symbols',json.dumps(selected)); msg='✅ الأزواج: '+', '.join(selected)
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
  if st['step']=='login':
   if not value.isdigit(): return await u.message.reply_text('رقم الحساب يجب أن يكون أرقاماً فقط. حاول مجدداً أو استخدم /cancel.')
   st['login']=int(value);st['step']='server';await u.message.reply_text('أرسل اسم خادم MT5 كما يظهر بالضبط، مثال: Broker-Demo.')
  elif st['step']=='server':
   if len(value)<2 or len(value)>100:return await u.message.reply_text('اسم الخادم غير صالح. حاول مجدداً أو استخدم /cancel.')
   st['server']=value;st['step']='password';await u.message.reply_text('أرسل كلمة مرور MT5 الآن. سأحاول حذف رسالة كلمة المرور مباشرة بعد قراءتها، ولن يتم تسجيلها في السجل.')
  elif st['step']=='password':
   password=value
   try: await u.message.delete()
   except Exception: pass
   login,server=st['login'],st['server']; self.login_state.pop(u.effective_user.id,None)
   ok,err,a=self.e.gw.login(login,password,server)
   if ok:
    # حفظ بيانات الحساب محلياً بصلاحيات المالك فقط
    from pathlib import Path
    import json, os
    cred_file=Path.home()/'.mt5bot_credentials.json'
    cred_file.write_text(json.dumps({
     'login':int(login),
     'server':server,
     'password':password
    }))
    os.chmod(cred_file,0o600)
    await self.db.log('MT5_LOGIN_SUCCESS',login=login,server=server)
    password=None
    await c.bot.send_message(u.effective_chat.id,f'✅ تم الاتصال بـ MT5\nرقم الحساب: {a.login}\nالخادم: {a.server}\nالوضع: حساب تجريبي محمي',reply_markup=self.kb())
   else:
    await self.db.log('MT5_LOGIN_FAILED',login=login,server=server,error=str(err))
    await c.bot.send_message(u.effective_chat.id,f'❌ فشل تسجيل الدخول إلى MT5\nالخطأ: {err}\nلم يتم تسجيل كلمة المرور. اضغط 🔐 حساب MT5 للمحاولة مجدداً.',reply_markup=self.kb())
 async def ask_value(self,u,key,prompt):
  if not self.allowed(u.effective_user): return
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

 async def symbol_prompt(self,u,c):
  await self.ask_value(u,'symbol','💱 أرسل رمز واحد فقط\nمثال: EURUSD')

 async def symbols_prompt(self,u,c):
  await self.ask_value(u,'symbols','📊 أرسل الرموز مفصولة بمسافة\nمثال:\nEURUSD GBPUSD XAUUSD')

 async def clean(self,u,c):
  if not self.allowed(u.effective_user): return
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
  if self.allowed(u.effective_user) and c.args: self.e.symbol=c.args[0].upper();await self.db.set('symbol',self.e.symbol);await u.message.reply_text(f'الرمز ← {self.e.symbol}',reply_markup=self.kb())
 async def rr(self,u,c):
  if self.allowed(u.effective_user) and c.args:
   try:v=float(c.args[0])
   except ValueError:return await u.message.reply_text('مثال: /rr 3')
   if not .5<=v<=10:return await u.message.reply_text('يجب أن تكون النسبة بين 0.5 و10')
   self.e.rr=v;await self.db.set('rr',v);await u.message.reply_text(f'العائد/المخاطرة ← 1:{v:g}',reply_markup=self.kb())
 async def risk(self,u,c):
  try:
   v=float(c.args[0]); assert 0.25<=v<=50
   self.e.risk_pct=v; await self.db.set('risk_pct',v)
   await u.message.reply_text(f'✅ المخاطرة: {v:g}%')
  except: await u.message.reply_text('استخدم /risk 0.25 (من 0.25 إلى 50)')

 async def confidence(self,u,c):
  if not self.allowed(u.effective_user): return
  try:
   v=float(c.args[0]); assert 50<=v<=95
   self.e.min_confidence=v
   await self.db.set('min_confidence',v)
   await u.message.reply_text(f'✅ الحد الأدنى للثقة: {v:g}%')
  except:
   await u.message.reply_text('استخدم /confidence 75 (من 50 إلى 95)')

 async def protection(self,u,c):
  try:
   v=float(c.args[0]); assert 5<=v<=90
   self.e.protection_pct=v; await self.db.set('protection_pct',v)
   await u.message.reply_text(f'✅ الحماية تبدأ عند: {v:g}%')
  except: await u.message.reply_text('استخدم /protection 45 (من 5 إلى 90)')

 async def maxpos(self,u,c):
  try:
   v=int(c.args[0]); assert 1<=v<=10
   self.e.max_positions=v; await self.db.set('max_positions',v)
   await u.message.reply_text(f'✅ حد المراكز: {v}')
  except: await u.message.reply_text('استخدم /maxpos 3 (من 1 إلى 10)')

 async def maxloss(self,u,c):
  try:
   v=int(c.args[0]); assert 0<=v<=20
   self.e.max_consecutive_losses=v; self.e.loss_limit_notified=False
   await self.db.set('max_consecutive_losses',v)
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
  await self.db.set('symbols',json.dumps(selected))
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
  a.add_handler(CallbackQueryHandler(self.cb))
  a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,self.text))
  return a
