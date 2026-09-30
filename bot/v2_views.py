import json
import time

import aiosqlite

UI_VERSION = "Deterministic Strategy Engine"


def _j(raw):
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _money(v):
    return f"${float(v):+,.2f}"


async def _rows(db, sql, args=()):
    async with aiosqlite.connect(db.path) as con:
        async with con.execute(sql, args) as cur:
            return await cur.fetchall()


async def latest_scan(db):
    rows = await _rows(db, "SELECT ts,symbol,details FROM audit WHERE event='STRATEGY_SCAN' ORDER BY id DESC LIMIT 1")
    if not rows:
        return None
    ts, symbol, raw = rows[0]
    data = _j(raw)
    data.update({"ts": ts, "symbol": symbol})
    return data


async def dashboard(engine, db):
    a = engine.gw.account()
    scan = await latest_scan(db)
    state = "🟢 يعمل" if engine.running else "⚪ متوقف"
    mt5 = "🟢 متصل" if a else "🔴 غير متصل"
    equity = f"${a.equity:,.2f} {a.currency}" if a else "—"
    floating = 0.0
    if a:
        ps = engine.gw.positions() or ()
        floating = sum(float(getattr(p, "profit", 0) or 0) for p in ps)
    modes = "🧪 DEMO" if a and getattr(a, "trade_mode", None) == 0 else ("💵 REAL" if a else "—")
    scan_line = "لا توجد قرارات بعد"
    if scan:
        age = max(0, int(time.time() - scan["ts"]))
        selected = scan.get("selected") or "—"
        side = scan.get("side") or "WAIT"
        scan_line = f'{scan["symbol"]} • {side} • {selected} • {scan.get("regime", "—")} • قبل {age}ث'
    registry = len(getattr(engine.registry, "strategies", []) or [])
    return (
        f"🤖 MT5 Scalper • {UI_VERSION}\n━━━━━━━━━━━━━━━━━━\n"
        f"{state}  |  MT5 {mt5}\n{modes}  |  🧩 {registry} strategy\n\n"
        f"💰 Equity: {equity}\n📈 العائم: {_money(floating)}\n"
        f"📂 المراكز: {len(engine.trades)} / {engine.max_positions}\n"
        f"⚠️ Risk: {engine.risk_pct:g}%  |  🎯 Min conf: {max(engine.min_confidence, engine.min_entry_confidence):g}%\n"
        f"📊 آخر مسح\n{scan_line}"
    )


async def strategy_center(engine, db):
    rows = await engine.strategy_report(window=200)
    catalog = engine.strategy_catalog()
    lines = ["🧩 مركز الاستراتيجيات", "━━━━━━━━━━━━━━", f"Catalog: {len(catalog)} strategy"]
    disabled = [r for r in rows if r.get("combo_enabled") is False]
    lines.append(f"Combos disabled: {len(disabled)}")
    if not rows:
        lines.append("\nلا يوجد سجل صفقات كافٍ بعد.")
    else:
        lines.append("\n📈 الأداء (استراتيجية × رمز × نظام)")
        for r in rows[:8]:
            flag = "🟢" if r.get("combo_enabled") else "🔴"
            lines.append(
                f'{flag} {r["strategy"]} • {r["symbol"]} • {r["regime"]}: '
                f'{r["trades"]}t {r["win_rate"]:.0f}% PF{r["profit_factor"]:.2f} {_money(r["net"])}'
            )
    scan = await latest_scan(db)
    if scan:
        lines.append("\n🔎 آخر مسح")
        lines.append(f'{scan["symbol"]} • {scan.get("regime")} • direction {scan.get("direction")}')
        evaluated = scan.get("evaluated") or []
        for row in evaluated[:6]:
            lines.append(f'  - {row.get("id")}: {row.get("decision")} {row.get("reason_code") or ""}')
    return "\n".join(lines)


async def strategy_detail(engine, db, strategy_id):
    catalog = {row["id"]: row for row in engine.strategy_catalog()}
    spec = catalog.get(strategy_id)
    if not spec:
        return f"⚠️ استراتيجية غير معروفة: {strategy_id}"
    perf = [r for r in await engine.strategy_report(window=500) if r.get("strategy") == strategy_id]
    lines = [
        f'🧩 {spec["name"]}',
        "━━━━━━━━━━━━━━",
        f'ID: {spec["id"]} | Family: {spec["family"]} | Tier: {spec["tier"]}',
        f'TF: {", ".join(spec["timeframes"])}',
        f'Symbols: {", ".join(spec["symbols"])}',
        f'Regimes: {", ".join(spec["regimes"])}',
        f'Forbidden: {", ".join(spec["forbidden"]) or "—"}',
        f'Indicators: {", ".join(spec["indicators"])}',
    ]
    if perf:
        lines.append("\n📈 Evidence")
        for r in perf[:8]:
            lines.append(
                f'{r["symbol"]} {r["regime"]}: {r["trades"]}t '
                f'WR {r["win_rate"]:.0f}% PF {r["profit_factor"]:.2f} {_money(r["net"])} '
                f'({r.get("combo_verdict")})'
            )
    else:
        lines.append("\nلا توجد صفقات مغلقة لهذه الاستراتيجية بعد.")
    return "\n".join(lines)


async def performance(db, hours=0):
    cutoff = time.time() - hours * 3600 if hours else 0
    rows = await _rows(
        db,
        "SELECT event,details FROM audit WHERE ts>=? AND event IN "
        "('TP','SL','PROTECTED_EXIT','TRAILING_EXIT','BREAKEVEN_EXIT','TP1_PARTIAL_EXIT',"
        "'MAX_DURATION_EXIT','POSITION_CLOSED') ORDER BY id",
        (cutoff,),
    )
    trades = {}
    for event, raw in rows:
        d = _j(raw)
        ticket = d.get("ticket")
        if ticket is None or "pnl" not in d:
            continue
        trades[str(ticket)] = (event, float(d.get("pnl") or 0), d.get("strategy", "unknown"))
    vals = list(trades.values())
    n = len(vals)
    wins = sum(p > 0 for _, p, _ in vals)
    loss = sum(p < 0 for _, p, _ in vals)
    gp = sum(p for _, p, _ in vals if p > 0)
    gl = -sum(p for _, p, _ in vals if p < 0)
    net = sum(p for _, p, _ in vals)
    pf = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)
    expectancy = net / n if n else 0.0
    exits = {}
    strat = {}
    for ev, p, st in vals:
        exits[ev] = exits.get(ev, 0) + 1
        x = strat.setdefault(st, [0, 0, 0.0])
        x[0] += 1
        x[1] += p > 0
        x[2] += p
    top = sorted(strat.items(), key=lambda z: z[1][2], reverse=True)[:6]
    label = "آخر 24 ساعة" if hours == 24 else "السجل الحالي"
    lines = [
        f"📈 الأداء • {label}", "━━━━━━━━━━━━━━",
        f"Closed: {n} | W/L: {wins}/{loss}",
        f"WR: {(wins / n * 100 if n else 0):.1f}% | PF: {pf:.2f} | Exp: {_money(expectancy)}",
        f"Net: {_money(net)}", "", "🏆 الاستراتيجيات",
    ]
    lines += [f"{name}: {v[0]} • {v[1] / v[0] * 100:.0f}% • {_money(v[2])}" for name, v in top]
    lines += ["", "🚪 المخارج: " + (" • ".join(f"{k} {v}" for k, v in sorted(exits.items())) if exits else "—")]
    return "\n".join(lines)


async def positions(engine):
    ps = engine.gw.positions() or ()
    if not ps:
        return "💼 الصفقات المفتوحة\n━━━━━━━━━━━━━━\nلا توجد صفقات مفتوحة."
    lines = ["💼 الصفقات المفتوحة", "━━━━━━━━━━━━━━"]
    for p in ps:
        t = engine.trades.get(p.ticket)
        side = "BUY" if int(getattr(p, "type", 0)) == 0 else "SELL"
        lines.append(
            f"\n{p.symbol} • {side} • #{p.ticket}\nP/L: {_money(getattr(p, 'profit', 0))} | Vol: {p.volume:g}\n"
            f"Entry {p.price_open:g} | SL {p.sl:g} | TP {p.tp:g}"
        )
        if t:
            age = (time.time() - t.opened_at) / 60
            lines.append(
                f"🧩 {t.strategy} • {t.regime} • {t.confidence:.0f}%\n"
                f"🛡 {'ON' if t.protection_45_active else 'OFF'} | Trailing {'ON' if t.trailing_moved else 'OFF'} | "
                f"{age:.1f}m | MFE/MAE {t.mfe_r:.2f}R/{t.mae_r:.2f}R"
            )
    return "\n".join(lines[:25])


async def health(engine, db):
    st = engine.gw.algo_status()
    now = time.time()
    counts = await _rows(
        db,
        "SELECT event,COUNT(*) FROM audit WHERE ts>=? AND event IN "
        "('STRATEGY_REGISTRY_ERROR','ENGINE_ERROR','TELEGRAM_PANEL_ERROR','COMBO_STATS_ERROR',"
        "'POST_FILL_RISK_DRIFT_REJECT') GROUP BY event",
        (now - 3600,),
    )
    c = dict(counts)
    advisor = getattr(engine, "ai_advisor", None)
    ai_state = "ON (veto-only)" if advisor is not None and advisor.active else "OFF"
    combos = len(engine.registry._cache) if getattr(engine.registry, "_cache", None) else 0
    return (
        "🩺 صحة النظام\n━━━━━━━━━━━━━━\n"
        f"{'✅' if st.get('connected') else '❌'} MT5 connected\n"
        f"{'✅' if st.get('trade_allowed') else '❌'} Algo Trading\n"
        f"{'✅' if st.get('account_trade_allowed') else '❌'} Account trading\n"
        f"{'✅' if st.get('trade_expert') else '❌'} Expert trading\n"
        f"🔄 Engine: {'RUNNING' if engine.running else 'STOPPED'} • cycle {engine.last_cycle_seconds:.2f}s\n"
        f"🧩 Strategies: {len(engine.registry.strategies)} | combo evidence: {combos}\n"
        f"🧠 AI cross-check: {ai_state}\n"
        f"آخر ساعة: registry {c.get('STRATEGY_REGISTRY_ERROR', 0)} • "
        f"engine {c.get('ENGINE_ERROR', 0)} • combo {c.get('COMBO_STATS_ERROR', 0)} • "
        f"risk-drift {c.get('POST_FILL_RISK_DRIFT_REJECT', 0)}"
    )
