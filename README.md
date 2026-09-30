# MTBOT — Deterministic Strategy Engine

MTBOT هو محرك تداول آلي لـ MetaTrader 5 يعمل على Python داخل Wine، ويجمع بين بيانات MT5،
تصنيف نظام السوق (Regime)، حزمة استراتيجيات حتمية قابلة للتنفيذ الآلي، إدارة مخاطرة محسوبة من
Equity، تنفيذ أوامر مع فحوص الوسيط، إدارة صفقة لكل استراتيجية، واجهة Telegram، وControl API
لـ T4Bot.

> **تحذير تداول:** البرنامج قادر على إرسال أوامر حقيقية عندما يكون حساب MT5 حقيقيًا ويتم فتح
> قفل Real. اختبر على Demo أولاً. لا يوجد نظام يضمن الربح أو يمنع الانزلاق/الفجوات/رفض الوسيط.

---

## 1. المعمارية الحالية

- نمط القرار: **حتمي بالكامل** — الاستراتيجيات تملك Entry/SL/TP، والـ AI اختياري veto-only ومعطل افتراضيًا.
- لا يوجد مسار AI-native ولا Strategy-52/55/56/57/58. تم حذفها.
- `AI_MARKET_ANALYSIS` لم تعد مصدر دخول؛ لا يوجد `ai_native_only`.
- الأنظمة: `TREND | RANGE | BREAKOUT | VOLATILE | NO_TRADE` (من `core/regime.py`).
- الاختيار: `Strategy × Symbol-Class(GOLD/INDEX/FX) × Regime` عبر `core/strategy_registry.py`.
- إدارة المخاطرة: المستخدم يضبط Risk %؛ المحرك يحسب اللوت من مسافة SL الفعلية. لا يتم تشويه SL لإجبار R:R.
- فحوص التنفيذ: spread/slippage، `trade_stops_level`، volume step/min/max، margin، margin level 200%،
  re-quote، anti-chase، reentry cooldown، correlation guard، order_check.
- إدارة الصفقة: partial TP1، break-even، protection، trailing، max hold، وخاصية الخروج الزمني تأكيدي
  (لا تُغلق صفقة بناءً على سعر لحظي غير مؤكد) — لكل استراتيجية عبر `management`.
- حماية التركز: `core/account_guard.py` يمنع التحوط الصناعي (نفس عامل المخاطرة بعكس الاتجاه)،
  ويحد المراكز المترابطة ومن ميزانية مخاطرها.
- الاستراتيجيات من الفئة **C** (المرشحة) تعمل كـ trial: مفعّلة على Demo/backtest فقط، وتُقفل تلقائياً على
  حساب حقيقي حتى تثبت إيجابية expectancy على نفس تركيبة Strategy×SymbolClass×Regime.
- التوثيق الكامل لكل استراتيجية: symbols / regimes / forbidden / timeframes / indicators / SL / TP /
  management / chart payload موجودة في `core/strategy_pack.py` و`StrategySpec`.

## 2. مسار القرار

```
closed bars (M1..W1)
        -> MarketContext (core/strategy_base.py)
        -> RegimeDetector (core/regime.py)
        -> StrategyRegistry: eligibility (symbol/regime) + combo evidence gate
        -> 25 deterministic strategy.analyze(ctx)
        -> confidence gate (max(min_confidence, min_entry_confidence))
        -> optional AI veto (data-only, disabled by default, fails open)
        -> anti-chase + correlation + spread re-check
        -> exact risk sizing from SL distance -> broker checks -> send
        -> TradeState (per-strategy management) -> MT5 truth -> DB -> Telegram/T4Bot
```

## 3. الاستراتيجيات (25)

| id | family | symbols | regimes |
|---|---|---|---|
| trend_ema_pullback | trend | FX majors/crosses | TREND |
| trend_break_retest | trend | XAU/FX/INDEX | TREND, BREAKOUT |
| trend_momentum_resume | trend | XAU/FX/INDEX | TREND |
| range_bollinger_reversion | mean_reversion | FX | RANGE |
| range_stochastic_reversal (trial) | mean_reversion | FX | RANGE |
| asia_range_breakout | breakout | FX | BREAKOUT, TREND |
| liquidity_sweep_reclaim | reversal | FX/XAU | RANGE, VOLATILE, TREND |
| gold_trend_pullback | trend | XAUUSD | TREND |
| gold_opening_range_breakout | breakout | XAUUSD | BREAKOUT, TREND, VOLATILE |
| volatility_squeeze_breakout | breakout | XAU/FX/INDEX | BREAKOUT, RANGE |
| vwap_trend_pullback | trend | INDEX/XAU/FX | TREND |
| volatility_expansion_follow | volatility | XAU/FX/INDEX | BREAKOUT, VOLATILE |
| london_open_momentum | breakout | FX | BREAKOUT, TREND |
| double_extreme_reversal | reversal | FX/XAU | RANGE, VOLATILE |
| failed_breakout_reclaim | reversal | XAU/FX/INDEX | RANGE, VOLATILE, BREAKOUT |
| rsi_divergence_reversal | reversal | FX | RANGE, TREND |
| ema_micro_stack_trend | trend | XAU/FX/INDEX | TREND |
| ny_session_continuation | trend | USDCAD/USDJPY/USDCHF/INDEX/XAU | TREND, BREAKOUT |
| gold_momentum_scalp | momentum | XAUUSD | TREND, BREAKOUT, VOLATILE |
| index_opening_drive | trend | INDEX | TREND, BREAKOUT |
| round_number_reaction (trial) | reversal | XAU/FX | RANGE, TREND, VOLATILE |
| three_drive_exhaustion (trial) | reversal | FX/XAU | RANGE, TREND |
| gold_asia_range_breakout | breakout | XAUUSD | BREAKOUT, TREND |
| inside_bar_breakout (trial) | breakout | XAU/FX/INDEX | BREAKOUT, TREND, RANGE |
| two_bar_reversal_volume (trial) | reversal | XAU/FX/INDEX | RANGE, TREND, VOLATILE |

كل استراتيجية تعرّف أيضاً `forbidden` regimes و`timeframes` و`indicators` الدقيقة وقواعد SL هيكلية
و TP/TP1/TP2 و`management` (protection/trailing/partial/max_hold) وanti-chase، وتُرسم على الشارت من
`draw` payload.

## 4. التحقق الآلي (Validation)

- `tools/backtest.py`: إعادة تشغيل شموع M1 تستخدم نفس `MarketContext` + `RegimeDetector` +
  `StrategyRegistry` الإنتاجية. لا look-ahead (قرار على شمعة مغلقة، تنفيذ على فتح الشمعة التالية)،
  spread لكل شمعة، SL قبل TP داخل نفس الشمعة، walk-forward in/out-of-sample.
- التقارير: trades, win rate, expectancy, profit factor, avg win/loss, max drawdown, MAE/MFE،
  ومجمّعة حسب Strategy × Symbol × Regime.
- `core/strategy_registry.py` يعطّل أي تركيبة بلغت 12 صفقة مغلقة مع expectancy ≤ 0 أو PF < 1.05،
  ويخصم ثقة من التركيبات الضعيفة (≥5 صفقات).
- `core/account_guard.py` يمنع الصفقات المتعارضة على نفس عامل المخاطرة، ويقيّد عدد المراكز المترابطة
  ومجموع مخاطرها بحسب إعداد المستخدم.

تشغيل الباكتست من داخل Wine Python الذي يملك MT5:

```
python.exe tools\backtest.py --symbols XAUUSD,EURUSD --days 400 --walk-forward 0.6 --output backtest.json
```

اختبارات الوحدة (تعمل على Linux بدون MT5):

```
python3 -m unittest discover -s tests -v
```

## 5. الواجهات

- Telegram: لوحة، تشغيل/إيقاف، تحليل الأزواج، مركز الاستراتيجيات + الكتالوج، الأداء، الصفقات،
  الصحة، مصفوفة Strategy×Symbol×Regime (🟢/🔴)، والإعدادات (Risk %, min confidence, min entry confidence, max positions,
  max consecutive losses, daily equity loss, max daily trades, max correlated positions,
  session profit limit, symbols, real lock).
- T4Bot Control API: `GET /v1/snapshot` (settings + strategies + strategy performance)،
  `GET /v1/strategies`، `GET /v1/strategies/performance`، `POST /v1/analysis/run`،
  `PATCH /v1/settings` بنفس مفاتيح الإعدادات أعلاه.

## 6. الملفات

```
main.py
core/
  engine.py              # pipeline + execution + per-trade management
  regime.py              # deterministic market regime
  indicators.py          # dependency-free causal TA
  strategy_base.py       # StrategySpec + MarketContext + shared geometry
  strategy_pack.py       # the 25 machine-executable strategies
  strategy_registry.py   # reward/penalty + combo gating + selection
  ai_advisor.py          # optional veto-only AI cross-check
  models.py, config.py, mt5_gateway.py, risk.py
bot/  telegram_app.py, v2_views.py
api/  control_api.py, events.py
storage/ db.py
tools/ backtest.py
tests/ test_engine.py
```
