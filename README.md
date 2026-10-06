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
- لا يوجد مسار AI-native ولا `AI_MARKET_ANALYSIS` كمصدر دخول؛ الـ AI الاختياري يعمل veto-only فقط.
- حزمة الاستراتيجيات القديمة المرقمة 52/55/56/57/58 ليست هي المعمارية الحالية.
- يوجد **Kronos K-line Forecast** كاستراتيجية مستقلة تعتمد ملف forecast مولد خارج حلقة التنفيذ، وتعمل على XAUUSD/EURUSD.
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
        -> 35 strategies: 34 deterministic + Kronos forecast
        -> confidence gate (max(min_confidence, min_entry_confidence))
        -> optional AI veto (data-only, disabled by default, fails open)
        -> anti-chase + correlation + spread re-check
        -> exact risk sizing from SL distance -> broker checks -> send
        -> TradeState (per-strategy management) -> MT5 truth -> DB -> Telegram/T4Bot
```

## 3. الاستراتيجيات الحالية (35)

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

ويضاف إلى الجدول أعلاه حالياً:

| id | family | symbols | regimes |
|---|---|---|---|
| fast_micro_reversal | reversal | XAUUSD/EURUSD/GBPUSD | جميع الأنظمة |
| ha_doji_box_continuation | trend | XAUUSD | جميع الأنظمة |
| qima_chart_pattern_breakout | breakout | XAUUSD | حسب StrategySpec |
| qima_nr4_breakout | breakout | XAUUSD | حسب StrategySpec |
| qima_sr_bounce | reversal | XAUUSD | حسب StrategySpec |
| qima_gap_continuation | continuation | XAUUSD | حسب StrategySpec |
| qima_trend_following | trend | XAUUSD | TREND |
| qima_macd_crossover | momentum | XAUUSD | TREND |
| qima_fibonacci_retracement | trend | XAUUSD | TREND |
| kronos_forecast | foundation_model | XAUUSD/EURUSD | جميع الأنظمة |

وبذلك السجل الحالي يحتوي **35 استراتيجية**: 34 من `strategy_pack.py` + استراتيجية Kronos.

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
  strategy_pack.py       # 34 machine-executable deterministic strategies
  strategy_registry.py   # reward/penalty + combo gating + selection
  ai_advisor.py          # optional veto-only AI cross-check
  models.py, config.py, mt5_gateway.py, risk.py
bot/  telegram_app.py, v2_views.py
api/  control_api.py, events.py
storage/ db.py
tools/ backtest.py
tests/ test_engine.py
```

## 7. Fast Micro Reversal

`fast_micro_reversal` استراتيجية انعكاس سريعة لـ M1/M5 على XAUUSD وEURUSD وGBPUSD. تبحث عن rejection/impulse على M5 ثم تؤكد التحول بواسطة EMA5/EMA9 وكسر micro-structure على M1. الوقف هيكلي ومقيد بـ ATR، والهدف لا يقل عن 1.8R. إعداد الإدارة الافتراضي للاستراتيجية: protection 30%، trailing trigger 55%، trailing gap 7%، وmax hold 90 دقيقة.

## 8. Heikin-Ashi Doji Box

`ha_doji_box_continuation` للذهب: يحدد اتجاه H1 بواسطة Heikin-Ashi، ثم يبحث على M5 عن long-wick doji وصندوق تصحيح مع إغلاقين للتأكيد. الوقف عند منتصف الصندوق والهدف 2.5× ارتفاع الصندوق. الإدارة: protection 35%، trailing 60%، gap 8%، max hold 240 دقيقة.

## 9. Kronos Forecast

`kronos_forecast` يستخدم forecast خارجي لـ M5 على XAUUSD/EURUSD. يحتاج 80 شمعة M5، ويقبل forecast حديثاً حتى 30 دقيقة وبثقة لا تقل عن 68%. الوقف هيكلي والهدف مأخوذ من expected high/low، ولا يقبل هندسة أقل من 1.5R. inference منفصل عن حلقة MT5 عبر `kronos_runtime/` حتى لا يحجب المحرك.

ملفات التشغيل الرئيسية: `kronos_runtime/daemon.py`, `worker.py`, `export_m5.py`, `run_once.sh`, `start_daemon.sh`. ملفات `data/` و`signals/` هي runtime artifacts وتتغير أثناء التشغيل.

## 10. مراقب فرص الذهب — Alert Only

`core/gold_alert_monitor.py` مراقب **قراءة فقط** لـ XAUUSD. يعمل كل 5 ثوانٍ افتراضياً، ويقرأ M1/M5/M15/H1 ويحسب EMA/ATR ودعم/مقاومة ديناميكية. يبحث عن breakout مؤكد أو rejection متوافق مع الاتجاه، ثم يرسل دخولاً مرجعياً وSL وTP1=1.8R وTP2=2.8R إلى Telegram/T4Bot.

المراقب **لا يرسل أي أمر إلى MT5**؛ التنفيذ يدوي. يوجد cooldown افتراضي 300 ثانية لمنع تكرار نفس التنبيه. يبدأ تلقائياً من `main.py` كـ asyncio task مستقلة.

## 11. إدارة المخاطرة والتنفيذ

- `risk_pct` هو **حد أقصى للمخاطرة** وليس هدفاً يجب استهلاكه بالكامل.
- اللوت يحسب من Equity ومسافة SL، ثم يطبّق قيود min/max/step الخاصة بالوسيط.
- إذا كان أقل لوت لدى الوسيط يتجاوز حد المخاطرة، يرفض المحرك الصفقة بدلاً من فرض مخاطرة أعلى.
- بعد التنفيذ يعاد حساب الخطر الفعلي من fill الحقيقي ويُسجل `POST_FILL_RISK_DRIFT` عند وجود انحراف.
- الحساب الحقيقي له `real_trading_enabled` lock مستقل.
- الحمايات تشمل حد خسارة Equity يومي، حد عدد الصفقات اليومية، حد المراكز المترابطة، وحد ربح الجلسة.
- إدارة المركز تدعم protection، trailing، break-even، partial TP1 عند طلب الاستراتيجية، وmax hold عندما تكون قيمته أكبر من صفر.

## 12. التخزين والحسابات

الإعدادات التداولية مخزنة **لكل حساب MT5** داخل `storage/bot.db`. قاعدة البيانات تحفظ الإعدادات، سجل الأحداث، الصفقات، وإحصاءات الأداء/التركيبات. النسخ الاحتياطي للـDB مدعوم دورياً من الإعدادات.

`storage/bot.db` وملفات SQLite و`.env` وملف بيانات دخول MT5 ليست ملفات يفترض نشرها في Git. بيانات اعتماد MT5 المحفوظة تكون خارج المستودع في `~/.mt5bot_credentials.json`.

## 13. التشغيل على الخادم

بيئة الإنتاج الحالية مبنية لتشغيل Python/MetaTrader5 داخل Wine. نقطة الدخول هي:

```bash
python.exe -u main.py
```

وفي الخادم الحالي تتم إدارة العملية بواسطة `mtbot.service`. عند الإقلاع: تهيئة DB → MT5 Gateway → Control API إن كان مفعلاً → Telegram → استعادة حساب MT5 المحفوظ في الخلفية → تشغيل Gold Alert Monitor.

## 14. متغيرات البيئة

ابدأ من `.env.example`. أهم مجموعات الإعدادات: Telegram، Control API، مسار MT5/Wine، حدود spread/slippage/tick freshness، DB، والـ AI cross-check الاختياري. لا تضع tokens/passwords داخل README أو Git.

## 15. ملاحظات النقل والاستعادة

GitHub يحفظ الكود والتاريخ، لكنه ليس بديلاً كاملاً عن نسخة runtime. عند نقل الخادم تحتاج بصورة منفصلة إلى: قاعدة `storage/bot.db` المتسقة، بيانات دخول MT5 الآمنة، ملف `.env` الحقيقي، بيئة Wine/MT5، واعتماديات Kronos/model إن كانت مخزنة خارج Git. لا تنسخ DB وهي في حالة كتابة بدون snapshot/backup صحيح.

## 16. حالة المشروع

README هذا يصف بنية فرع `AIplus` الحالية وقت آخر تحديث. المصدر النهائي للحقيقة هو الكود نفسه و`StrategySpec` لكل استراتيجية؛ ملفات `.bak` هي نسخ تاريخية وليست مسار الإنتاج.
