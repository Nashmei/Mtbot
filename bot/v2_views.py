import json,time,aiosqlite
UI_VERSION="AI Telegram V2"

def _j(raw):
 try:return json.loads(raw or "{}")
 except Exception:return {}

def _money(v): return f"${float(v):+,.2f}"

async def _rows(db,sql,args=()):
 async with aiosqlite.connect(db.path) as con:
  async with con.execute(sql,args) as cur:return await cur.fetchall()

async def latest_ai(db):
 rows=await _rows(db,"SELECT ts,symbol,details FROM audit WHERE event='AI_NATIVE_DECISION' ORDER BY id DESC LIMIT 1")
 if not rows:return None
 ts,symbol,raw=rows[0];d=_j(raw);d.update({"ts":ts,"symbol":symbol});return d

async def dashboard(engine,db):
 a=engine.gw.account();ai=await latest_ai(db)
 state="🟢 يعمل" if engine.running else "⚪ متوقف"
 mt5="🟢 متصل" if a else "🔴 غير متصل"
 equity=f"${a.equity:,.2f} {a.currency}" if a else "—"
 floating=0.0
 if a:
  ps=engine.gw.positions() or ();floating=sum(float(getattr(p,"profit",0) or 0) for p in ps)
 ai_line="لا توجد قرارات بعد"
 if ai:
  age=max(0,int(time.time()-ai["ts"]));dec=ai.get("decision","—")
  ai_line=f'{ai["symbol"]} • {dec} • {ai.get("strategy_id","none")} • {ai.get("confidence",0)}% • قبل {age}ث'
 return (f"🤖 MT5 AI SCALPER • {UI_VERSION}\n━━━━━━━━━━━━━━━━━━\n"
         f"{state}  |  MT5 {mt5}\n🧪 DEMO  |  🧠 AI Native\n\n"
         f"💰 Equity: {equity}\n📈 العائم: {_money(floating)}\n"
         f"📂 المراكز: {len(engine.trades)} / {engine.max_positions}\n⚠️ Risk: {engine.risk_pct:g}%\n\n"
         f"🧠 آخر قرار AI\n{ai_line}")

async def ai_center(engine,db):
 ai=await latest_ai(db)
 if not ai:return "🧠 AI Center\n━━━━━━━━━━━━━━\nلا توجد قرارات AI مسجلة بعد."
 def own(v):return "🤖 AI" if float(v or 0)==0 else str(v)
 ov=(f"⚙️ التحكم لكل صفقة (0 = AI)\n"
     f"R:R: {own(engine.ai_rr_override)} | SL: {own(engine.ai_sl_points_override)} pt | TP: {own(engine.ai_tp_points_override)} pt\n"
     f"Protection: {own(engine.ai_protection_override)} | Trailing: {own(engine.ai_trailing_override)} | Duration: {own(engine.ai_duration_override)}")
 return (f"🧠 AI Center\n━━━━━━━━━━━━━━\n"
         f"💱 {ai['symbol']} • {ai.get('decision','—')}\n"
         f"🧩 {ai.get('strategy_id','none')}\n🎯 الثقة: {ai.get('confidence',0)}%\n"
         f"📊 السوق: {ai.get('regime','UNKNOWN')}\n⚖️ AI R:R: {ai.get('rr',0):g}\n"
         f"🛡 AI Protection: {ai.get('protection_pct',0):g}% | Trailing: {ai.get('trailing_gap_pct',0):g}%\n"
         f"⏱ AI Duration: {ai.get('expected_duration_minutes',0):g}m\n"
         f"🔎 {ai.get('reason_code','—')}\n\n{ov}")

async def performance(db,hours=0):
 cutoff=time.time()-hours*3600 if hours else 0
 rows=await _rows(db,"SELECT event,details FROM audit WHERE ts>=? AND event IN ('TP','SL','PROTECTED_EXIT','TRAILING_EXIT','BREAKEVEN_EXIT','POSITION_CLOSED') ORDER BY id",(cutoff,))
 trades={}
 for event,raw in rows:
  d=_j(raw);ticket=d.get("ticket")
  if ticket is None or "pnl" not in d:continue
  trades[str(ticket)]=(event,float(d.get("pnl") or 0),d.get("strategy","unknown"))
 vals=list(trades.values());n=len(vals);wins=sum(p>0 for _,p,_ in vals);loss=sum(p<0 for _,p,_ in vals)
 gp=sum(p for _,p,_ in vals if p>0);gl=-sum(p for _,p,_ in vals if p<0);net=sum(p for _,p,_ in vals)
 pf=gp/gl if gl>0 else (999.0 if gp>0 else 0.0)
 exits={}
 strat={}
 for ev,p,st in vals:
  exits[ev]=exits.get(ev,0)+1
  x=strat.setdefault(st,[0,0,0.0]);x[0]+=1;x[1]+=p>0;x[2]+=p
 top=sorted(strat.items(),key=lambda z:z[1][2],reverse=True)[:5]
 label="آخر 24 ساعة" if hours==24 else "السجل الحالي"
 lines=[f"📈 الأداء • {label}","━━━━━━━━━━━━━━",f"Closed: {n} | W/L: {wins}/{loss}",f"WR: {(wins/n*100 if n else 0):.1f}% | PF: {pf:.2f}",f"Net: {_money(net)}","", "🏆 الاستراتيجيات"]
 lines += [f"{name}: {v[0]} • {v[1]/v[0]*100:.0f}% • {_money(v[2])}" for name,v in top]
 lines += ["","🚪 المخارج: "+(" • ".join(f"{k} {v}" for k,v in sorted(exits.items())) if exits else "—")]
 return "\n".join(lines)

async def positions(engine):
 ps=engine.gw.positions() or ()
 if not ps:return "💼 الصفقات المفتوحة\n━━━━━━━━━━━━━━\nلا توجد صفقات مفتوحة."
 lines=["💼 الصفقات المفتوحة","━━━━━━━━━━━━━━"]
 for p in ps:
  t=engine.trades.get(p.ticket);side="BUY" if int(getattr(p,"type",0))==0 else "SELL"
  lines.append(f"\n{p.symbol} • {side} • #{p.ticket}\nP/L: {_money(getattr(p,'profit',0))} | Vol: {p.volume:g}\nEntry {p.price_open:g} | SL {p.sl:g} | TP {p.tp:g}")
  if t:
   age=(time.time()-t.opened_at)/60
   lines.append(f"🧠 {t.strategy} • {t.confidence*100:.0f}%\n🛡 {'ON' if t.protection_45_active else 'OFF'} | Trailing {'ON' if t.trailing_moved else 'OFF'} | {age:.1f}m")
 return "\n".join(lines[:25])

async def health(engine,db):
 st=engine.gw.algo_status();now=time.time()
 counts=await _rows(db,"SELECT event,COUNT(*) FROM audit WHERE ts>=? AND event IN ('AI_NATIVE_ERROR','AI_NATIVE_429','ENGINE_ERROR','TELEGRAM_PANEL_ERROR') GROUP BY event",(now-3600,))
 c=dict(counts);last=await latest_ai(db)
 return (f"🩺 صحة النظام\n━━━━━━━━━━━━━━\n"
         f"{'✅' if st.get('connected') else '❌'} MT5 connected\n"
         f"{'✅' if st.get('trade_allowed') else '❌'} Algo Trading\n"
         f"{'✅' if st.get('account_trade_allowed') else '❌'} Account trading\n"
         f"{'✅' if st.get('trade_expert') else '❌'} Expert trading\n"
         f"🔄 Engine: {'RUNNING' if engine.running else 'STOPPED'} • cycle {engine.last_cycle_seconds:.2f}s\n"
         f"🧠 AI model: {engine.ai_native.model}\n"
         f"⏱ AI latency: {(last or {}).get('latency_ms','—')} ms\n"
         f"آخر ساعة: AI errors {c.get('AI_NATIVE_ERROR',0)} • 429 {c.get('AI_NATIVE_429',0)} • Engine {c.get('ENGINE_ERROR',0)}")
