import asyncio,time
import MetaTrader5 as mt5
from .config import settings
from .models import TradeState,Side
from .analyzer import Analyzer
from .risk import Risk
class Engine:
 def __init__(self,gw,db,notify):
  self.gw=gw
  self.db=db
  self.notify=notify
  self.running=False

  # إعدادات قابلة للتحكم من Telegram
  self.symbols=[settings.default_symbol]
  self.symbol=settings.default_symbol
  self.rr=settings.rr
  self.risk_pct=settings.risk_per_trade_pct
  self.min_confidence=75.0
  self.protection_pct=45.0
  self.trailing_gap_pct=5.0
  self.max_trade_minutes=10.0
  self.reentry_cooldown_seconds=120.0
  self.last_close_by_symbol={}
  self.blocked_signal_by_symbol={}
  self.max_positions=1
  self.max_consecutive_losses=settings.max_consecutive_losses
  self.consecutive_losses=0
  self.loss_limit_notified=False

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
  self.reject_log_at={}
  self.reject_log_interval=60.0

  # الإعدادات المحفوظة تُحمّل لاحقاً داخل سياق async


 async def load_settings(self):
  try:
   self.rr=float(await self.db.get('rr',self.rr))
   self.risk_pct=float(await self.db.get('risk_pct',self.risk_pct))
   self.min_confidence=float(await self.db.get('min_confidence',self.min_confidence))
   self.protection_pct=float(await self.db.get('protection_pct',self.protection_pct))
   self.trailing_gap_pct=float(await self.db.get('trailing_gap_pct',self.trailing_gap_pct))
   self.max_trade_minutes=float(await self.db.get('max_trade_minutes',self.max_trade_minutes))
   self.max_positions=int(await self.db.get('max_positions',self.max_positions))
   self.max_consecutive_losses=int(await self.db.get('max_consecutive_losses',self.max_consecutive_losses))
   self.consecutive_losses=max(0,int(await self.db.get('consecutive_losses',0)))
  except (TypeError,ValueError):
   pass

 async def status(self):
  a=self.gw.account()
  if not a:
   return 'MT5 غير متصل'

  symbols=', '.join(self.symbols) if self.symbols else 'لا يوجد'
  state='🟢 يعمل' if self.running else '⚪ متوقف'

  return (
   f'{state} | 🔒 تجريبي\n'
   f'💱 الأزواج: {symbols}\n'
   f'📂 المراكز: {len(self.trades)} / {self.max_positions}\n'
   f'⚠️ المخاطرة: {self.risk_pct:g}% لكل صفقة\n'
   f'🛡 الحماية: {self.protection_pct:g}%\n'
   f'⏱ حد الصفقة: {self.max_trade_minutes:g} دقيقة\n'
   f'❌ الخسائر المتتالية: {self.consecutive_losses} / {self.max_consecutive_losses}\n'
   f'⚖️ العائد/المخاطرة: 1:{self.rr:g}\n'
   f'💰 Equity: {a.equity:.2f} {a.currency}'
  )

 async def start(self):
  old=[p for p in (self.gw.positions() or ()) if getattr(p,'magic',0)==4009 and p.ticket not in self.trades]
  if old:
   await self.notify('⚠️ توجد صفقة قديمة للبوت غير متتبعة. أغلقها يدويًا قبل التشغيل.')
   return

  if self.running:
   await self.notify('ℹ️ البوت يعمل بالفعل.')
   return

  if not self.symbols:
   await self.notify('⚠️ اختر زوجاً واحداً على الأقل قبل التشغيل.')
   return

  self.running=True
  self.last_analysis_by_symbol={}

  await self.db.log(
   'BOT_STARTED',
   symbols=self.symbols,
   risk_pct=self.risk_pct,
   protection_pct=self.protection_pct,
   max_positions=self.max_positions,
   max_consecutive_losses=self.max_consecutive_losses
  )

  await self.notify(
   f'▶️ تم تشغيل البوت\n'
   f'💱 مراقبة: {", ".join(self.symbols)}\n'
   f'📂 حد المراكز: {self.max_positions}\n'
   f'⚠️ المخاطرة: {self.risk_pct:g}%\n'
   f'🛡 الحماية: {self.protection_pct:g}%'
  )

  asyncio.create_task(self.loop())

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
   try: await self.step()
   except Exception as e: await self.db.log('ENGINE_ERROR',self.symbol,error=str(e))
   await asyncio.sleep(settings.poll_interval_ms/1000)
 async def step(self):
  account=self.gw.account()
  if not account or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO:
   self.running=False; await self.notify('🔒 الحساب التجريبي فقط.'); return

  # إدارة كل الصفقات المفتوحة أولاً
  for t in list(self.trades.values()):
   info=self.gw.info(t.symbol); tick=self.gw.tick(t.symbol)
   if info and tick: await self.manage(t,tick,info)

  # تحديث لوحة الصفقات المجمعة
  if self.trades:
   await self.live_dashboard()

  # لا دخول جديد عند بلوغ الحدود
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
   # Avoid stacking the same USD directional exposure across correlated FX pairs.
   usd_group={'EURUSD','GBPUSD','AUDUSD','NZDUSD'}
   info=self.gw.info(symbol); tick=self.gw.tick(symbol)
   if not info or not tick or not info.point or tick.bid<=0 or tick.ask<=tick.bid:
    await self._log_reject('SCAN_REJECT',symbol,reason='INVALID_MARKET_DATA')
    return

   ok,sp,avg,lim=self.risk.spread_ok(tick,info)
   if not ok:
    await self._log_reject('SPREAD_REJECT',symbol,spread=sp,average=avg,limit=lim)
    return

   reg,sig,meta=self.an.analyze(self.gw.ticks(symbol),info.point,self.gw.rates_m5(symbol,200),symbol=symbol,rates_m15=self.gw.rates_m15(symbol,200))
   if not sig:
    await self._log_reject('NO_SIGNAL',symbol,regime=reg.value)
    return
   confidence_score=float(sig.confidence)*100.0
   # Telegram confidence setting is a real hard entry filter.
   if confidence_score < self.min_confidence:
    await self._log_reject('CONFIDENCE_REJECT',symbol,strategy=sig.strategy,confidence=confidence_score,min_confidence=self.min_confidence)
    return
   if symbol in usd_group and any(t.symbol in usd_group and t.side==sig.side for t in self.trades.values()):
    await self._log_reject('CORRELATION_REJECT',symbol,side=sig.side.value,strategy=sig.strategy)
    return

   # بعد الإغلاق: مهلة قصيرة، ثم يجب أن تتجدد الإشارة قبل تكرار نفس الاستراتيجية/الاتجاه.
   signal_key=(sig.strategy,sig.side.value)
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

   # تقييد الحجم حسب المارجن المتاح
   margin_1lot=mt5.order_calc_margin(typ,symbol,1.0,price)
   if margin_1lot is not None and margin_1lot>0:
    margin_capacity=(float(account.margin_free)*0.80)/float(margin_1lot)
    if margin_capacity < vmin:
     await self.notify(
      f'⛔ لم تنفذ {symbol}\n'
      f'المارجن لا يسمح حتى بأقل لوت {vmin:g}'
     )
     return
    msteps=math.floor((margin_capacity-vmin)/vstep+1e-9)
    margin_vol=vmin+max(0,msteps)*vstep
    vol=min(vol,margin_vol,vmax)

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

   before={p.ticket for p in (self.gw.positions(symbol) or ())}
   req={'action':mt5.TRADE_ACTION_DEAL,'symbol':symbol,'volume':vol,'type':typ,
        'price':price,'sl':sl,'tp':tp,'deviation':settings.max_slippage_points,
        'magic':4009,'comment':f'TGSCALP:{sig.strategy}',
        'type_time':mt5.ORDER_TIME_GTC,'type_filling':mt5.ORDER_FILLING_FOK}

   chk=self.gw.order_check(req)
   if not chk:
    await self.notify(f'❌ لم تنفذ {symbol}\norder_check لم يرجع نتيجة\nMT5: {mt5.last_error()}')
    return
   if chk.retcode!=0:
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

   # MT5 قد يتأخر في إظهار المركز بعد نجاح التنفيذ
   pos=None
   for _ in range(50):
    await asyncio.sleep(.1)
    pos=self.gw.find_new_bot_position(symbol,before)
    if pos:
     break

   if not pos:
    await self.db.log('POSITION_LINK_FAILED',symbol,result=str(res))
    try:
     await self.notify(
      f'🚨 نُفذت صفقة {symbol} لكن تعذر ربطها آلياً. '
      f'لن يتم فتح صفقة جديدة على الرمز حتى الفحص.'
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
    await self.db.log(
     'INVALID_INITIAL_R',symbol,
     ticket=pos.ticket,entry=fill,sl=actual_sl
    )
    return

   t=TradeState(
    pos.ticket,symbol,sig.side,fill,actual_sl,actual_tp,initial_r,time.time(),
    strategy=sig.strategy,regime=reg.value,confidence=sig.confidence,
    reason=sig.reason,volume=float(pos.volume or vol),
    protection_pct=self.protection_pct,trailing_gap_pct=self.trailing_gap_pct
   )

   sl=actual_sl
   tp=actual_tp
   vol=float(pos.volume or vol)
   self.trades[pos.ticket]=t
   # A successful trade resets this symbol to the normal spread baseline.
   self.risk.reset_spread_relaxation(symbol)

   await self.db.log('OPEN',symbol,ticket=pos.ticket,entry=fill,sl=sl,tp=tp,volume=vol,side=sig.side.value,strategy=sig.strategy,regime=reg.value,confidence=float(sig.confidence),reason=sig.reason)
   side_icon='🟢' if sig.side==Side.BUY else '🔴'
   side_text='شراء' if sig.side==Side.BUY else 'بيع'
   await self.notify(
    f'{side_icon} {side_text} — {symbol}\n\n'
    f'📦 | اللوت: {vol:g}\n'
    f'⚠️ | المخاطرة: ${actual_risk:.2f} ({actual_risk_pct:.2f}%)\n'
    f'💰 | هدف TP تقريبي: ${actual_risk*self.rr:.2f}\n'
    f'🛑 | وقف خسارة : ${actual_risk:.2f}\n'
    f'🎯 | حماية الربح {t.protection_pct:g}٪ — [${actual_risk*self.rr*t.protection_pct/100.0:.2f}]\n'
    f'🕔 | مدة اغلاق تلقائي [{self.max_trade_minutes:g} دقايق]'
   )
   asyncio.create_task(self._send_trade_chart(t,actual_risk,actual_risk_pct))

 async def _send_trade_chart(self,t,actual_risk,actual_risk_pct):
  try:
   import os,tempfile
   import matplotlib
   matplotlib.use('Agg')
   import matplotlib.pyplot as plt
   import matplotlib.dates as mdates
   from datetime import datetime

   rates=self.gw.rates_m5(t.symbol,80)
   if rates is None or len(rates)<10:
    await self.db.log('TRADE_CHART_FAILED',t.symbol,ticket=t.ticket,error='not enough M5 candles')
    return

   xs=[datetime.fromtimestamp(int(r['time'])) for r in rates]
   opens=[float(r['open']) for r in rates]
   highs=[float(r['high']) for r in rates]
   lows=[float(r['low']) for r in rates]
   closes=[float(r['close']) for r in rates]

   fig,ax=plt.subplots(figsize=(11,6.2),dpi=130)
   xnum=mdates.date2num(xs)
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

   caption=(
    f'📊 {t.symbol} — {side}\n'
    f'🎫 {t.ticket} | 📦 {t.volume:g}\n'
    f'🧠 {t.strategy} | 🎯 {t.confidence*100:.0f}%\n'
    f'➡️ Entry: {t.entry:g}\n🛑 SL: {t.sl:g}\n💰 TP: {t.tp:g}\n'
    f'⚠️ Risk: ${actual_risk:.2f} ({actual_risk_pct:.2f}%) | R:R 1:{self.rr:g}\n'
    f'🛡 Protection: {t.protection_pct:g}%'
   )
   await self.notify(caption,photo_path=path,caption=caption)
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
   idle=max(0.0,time.time()-t.last_progress_at)
   remaining=max(0,60-int(idle))
   protection=f'مفعلة 🔐 | الخمول: {remaining}ث'
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
     await self.notify(f'⏱ إغلاق حد {self.max_trade_minutes:g} دقيقة — {t.symbol}')
     return
    await self.db.log('MAX_DURATION_EXIT_FAILED',t.symbol,ticket=t.ticket,result=str(res))

  favorable=(price-t.entry) if t.side==Side.BUY else (t.entry-price)
  r=favorable/t.initial_r
  age=time.time()-t.opened_at
  pos=self.gw.position_by_ticket(t.ticket)

  # Position disappeared: determine the real MT5 close reason.
  if not pos:
   deals=self.gw.history_deals_by_position(t.ticket)
   exit_deal=None
   for d in deals:
    if getattr(d,'entry',None) in (getattr(mt5,'DEAL_ENTRY_OUT',1),getattr(mt5,'DEAL_ENTRY_OUT_BY',3)):
     exit_deal=d

   if exit_deal:
    reason=getattr(exit_deal,'reason',None)
    profit=float(getattr(exit_deal,'profit',0) or 0)
    swap=float(getattr(exit_deal,'swap',0) or 0)
    commission=float(getattr(exit_deal,'commission',0) or 0)
    pnl=profit+swap+commission
    exit_price=float(getattr(exit_deal,'price',0) or 0)

    if reason==mt5.DEAL_REASON_TP:
     event='TP'
     icon='🎯'
    elif reason==mt5.DEAL_REASON_SL:
     event='SL'
     icon='🛑'
    else:
     event='POSITION_CLOSED'
     icon='🏁'

    if pnl < 0:
     self.consecutive_losses+=1
    elif pnl > 0:
     self.consecutive_losses=0
    await self.db.set("consecutive_losses",self.consecutive_losses)

    await self.db.log(event,t.symbol,exit_price=exit_price,pnl=pnl,reason=reason)
    await self.notify(
     f'{icon} {event} — {t.symbol}\n'
     f'🚪 سعر الخروج: {exit_price}\n'
     f'P/L: {pnl:.2f}\n'
     f'Duration: {age:.1f}s'
    )
   else:
    await self.db.log('POSITION_CLOSED',t.symbol,reason='history_not_found')
    await self.notify(f'🏁 تم إغلاق الصفقة — {t.symbol}\n⚠️ سبب الإغلاق غير متاح في سجل MT5.')

   self.last_close_by_symbol[t.symbol]=time.time()
   self.blocked_signal_by_symbol[t.symbol]=(t.strategy,t.side.value)
   self.trades.pop(t.ticket,None)
   return

  # تحديث لوحة Telegram الحية أثناء وجود الصفقة.
  # live panel handled centrally

  # حماية الربح: تبدأ فقط بعد تحقيق 45% من المسافة إلى TP.
  target_distance=abs(t.tp-t.entry)
  target_progress=(favorable/target_distance) if target_distance>0 else 0.0
  now=time.time()

  # عند تحقيق 45%: انقل SL إلى مستوى 45% وابدأ عداد 60 ثانية.
  trigger=t.protection_pct/100.0
  if target_progress>=trigger and not t.protection_45_active:
   level45=t.entry+(target_distance*trigger) if t.side==Side.BUY else t.entry-(target_distance*trigger)
   min_dist=max(int(getattr(info,'trade_stops_level',0) or 0),int(getattr(info,'trade_freeze_level',0) or 0))*info.point
   valid=(level45 <= tick.bid-min_dist) if t.side==Side.BUY else (level45 >= tick.ask+min_dist)
   res=self.gw.modify(pos.ticket,t.symbol,level45,t.tp) if valid else None

   if res and res.retcode==mt5.TRADE_RETCODE_DONE:
    t.sl=level45
    t.protection_45_active=True
    t.trailing=True
    t.best_favorable_price=price
    t.last_progress_at=now

    await self.db.log(
     'PROTECTION_ACTIVATED',t.symbol,
     sl=level45,price=price,target_progress=target_progress
    )
    await self.notify(
     f'🛡 تفعيل حماية {t.protection_pct:g}% — {t.symbol}\n'
     f'🛑 الوقف: {level45:.{info.digits}f}\n'
     f'⏱ بدأ عداد الخمول 60 ثانية'
    )
   else:
    await self.db.log('PROTECTION_FAILED',t.symbol,result=str(res))

  # بعد التفعيل: أفضل سعر جديد يعيد عداد 60 ثانية ويحرك SL للأمام.
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

    # لا يرجع SL خلف مستوى تفعيل الحماية.
    protection_level=(
     t.entry+(target_distance*(t.protection_pct/100.0))
     if t.side==Side.BUY
     else t.entry-(target_distance*(t.protection_pct/100.0))
    )

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
      await self.db.log(
       'TRAILING_PROTECTION',t.symbol,
       old_sl=oldsl,new_sl=cand,best_price=t.best_favorable_price
      )

  # بعد تفعيل 45% فقط: 60 ثانية بلا أفضل سعر جديد = إغلاق بالسوق.
  if t.protection_45_active and (now-t.last_progress_at)>=60:
   idle=now-t.last_progress_at
   res=self.gw.close(pos)

   if res and res.retcode in (
    mt5.TRADE_RETCODE_DONE,
    mt5.TRADE_RETCODE_DONE_PARTIAL
   ):
    await self.db.log(
     'PROFIT_STALL_EXIT',t.symbol,
     idle_seconds=idle,result=str(res)
    )
    await self.notify(
     f'⏱ إغلاق حماية الربح — {t.symbol}\n'
     f'📌 60 ثانية بدون تقدم جديد\n'
     f'🛡 تم الإغلاق لحماية الربح'
    )
   else:
    await self.db.log(
     'PROFIT_STALL_EXIT_FAILED',t.symbol,
     idle_seconds=idle,result=str(res)
    )
    # تبقى الصفقة تحت المراقبة ونحاول مجددًا بعد 60 ثانية.
    t.last_progress_at=now