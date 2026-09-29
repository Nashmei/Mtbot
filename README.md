# MTBOT — AIplus / V1 CLEAN

MTBOT هو محرك تداول آلي لـ MetaTrader 5 يعمل على Python داخل Wine، ويجمع بين قراءة السوق من MT5، قرار تداول AI-native، إدارة مخاطرة محسوبة من Equity، تنفيذ أوامر، حماية أرباح ديناميكية، واجهة Telegram، وControl API خاص لتطبيق T4Bot.

هذه الوثيقة تصف **النسخة الحالية فقط: `AIplus / V1 CLEAN`**. تم حذف الفروع والنسخ التاريخية القديمة من المستودع، وأصبح `AIplus` هو الفرع الوحيد والافتراضي.

> **تحذير تداول:** البرنامج قادر على إرسال أوامر حقيقية عندما يكون حساب MT5 حقيقيًا ويتم فتح قفل Real. اختبر الإعدادات على Demo أولًا. لا يوجد نموذج ذكاء اصطناعي أو نظام إدارة مخاطر يضمن الربح أو يمنع الانزلاق/الفجوات/رفض الوسيط.

---

## 1. الحالة الحالية

- الفرع: `AIplus`.
- الإصدار التشغيلي: `V1 CLEAN`.
- نقطة التشغيل: `main.py`.
- المنصة: MetaTrader 5.
- بيئة الخادم المستخدمة: Ubuntu + Wine Staging + Windows Python 3.11 + MT5.
- نمط القرار: `AI-native` (`Engine.ai_native_only = True`).
- نموذج AI الافتراضي: `google/diffusiongemma-26b-a4b-it` عبر NVIDIA NIM.
- Telegram: اختياري ويمكن تعطيله.
- T4Bot Control API: اختياري، افتراضيًا على `127.0.0.1:7099` عند تفعيله.
- قاعدة البيانات: SQLite في `storage/bot.db` افتراضيًا.
- الإعدادات القابلة للتداول تحفظ لكل حساب MT5 بشكل مستقل.
- Demo مدعوم.
- Real مدعوم لكن التنفيذ الحقيقي يحتاج `real_trading_enabled` للحساب.
- لا يوجد إغلاق صفقة تلقائي بسبب `MAX_DURATION_EXIT` في النسخة الحالية.
- لا يوجد حظر ثابت `23:45–00:30` للدخول في النسخة الحالية.
- إدارة الحماية تستخدم tick history مع fail-safe عند تعذر جلبه.
- Manual Signal Mode موجود كمسار اختياري داخل Engine.

---

## 2. شجرة المشروع

```text
/home/ubuntu/.wine/drive_c/mt5bot/
├── main.py
├── requirements.txt
├── README.md
├── .env                    # محلي فقط — غير مرفوع إلى Git
├── .env.example
├── .gitignore
│
├── core/
│   ├── engine.py
│   ├── ai_native_selector.py
│   ├── ai_advisor.py
│   ├── analyzer.py
│   ├── broker_resolver.py
│   ├── config.py
│   ├── models.py
│   ├── mt5_gateway.py
│   ├── risk.py
│   ├── strategies.py
│   ├── strategy_library.py
│   ├── strategy_ranker.py
│   └── web_research.py
│
├── bot/
│   ├── telegram_app.py
│   └── v2_views.py
│
├── api/
│   ├── __init__.py
│   ├── control_api.py
│   └── events.py
│
└── storage/
    ├── db.py
    ├── bot.db              # runtime — غير مرفوع إلى Git
    └── t4bot_media/        # runtime — غير مرفوع إلى Git
```

لا توجد في النسخة النظيفة مجلدات `tests/` أو `scripts/` أو `backtest.py` أو workflow قديم. ملفات `__pycache__` و`.pyc` نواتج تشغيل وليست جزءًا من المصدر.

---

## 3. وظيفة كل ملف

### `main.py`
نقطة دخول التطبيق. ينشئ `DB` و`MT5Gateway` و`Engine` و`EventHub`، ويحاول استرجاع حساب MT5 المحفوظ محليًا، ثم يشغّل واجهات التحكم المفعلة. كما يحتوي طبقة الإشعارات التي تربط أحداث المحرك بـT4Bot وTelegram وتدير تحديث رسالة الصفقة ونتيجتها.

### `core/engine.py`
قلب MTBOT. مسؤول عن دورة المسح، التحقق من الحساب والسوق، قراءة الرموز، تجهيز snapshot للـAI، تطبيق القرار، فلاتر الدخول، المخاطرة، التنفيذ، تتبع الصفقات، الحماية، trailing، نتائج الصفقات، حدود الخسارة، هدف الجلسة، audit، وManual Signal Mode.

### `core/ai_native_selector.py`
واجهة القرار AI-native. تجمع snapshot مضغوطًا من tick/M1/M5/M15/H1، وترسل الطلب إلى NVIDIA NIM، وتتحقق من JSON، وتحوّل القرار إلى `Signal`. فيها cache، cooldown لكل رمز، فاصل عالمي، backoff عند HTTP 429، منع إعادة استهلاك نفس SIGNAL، وتطبيع محدود لصيغ JSON/النسب/regime بدون تجاوز حدود الأمان.

### `core/ai_advisor.py`
طبقة AI مساندة موجودة في المعمارية. ليست بديلًا عن `AINativeSelector` في نمط `ai_native_only` الحالي، لكنها جزء من Engine ومسارات التحليل المساندة.

### `core/analyzer.py`
التحليل الفني الداخلي وربط بيانات السوق بمنطق الاستراتيجيات والتقييم.

### `core/strategies.py`
حساب/تنفيذ منطق المؤشرات والاستراتيجيات الفنية المستخدمة بواسطة التحليل الداخلي.

### `core/strategy_library.py`
تعريف Playbooks/مكتبة الاستراتيجيات التي تعرض أيضًا للـAI داخل snapshot.

### `core/strategy_ranker.py`
منطق تقييم/ترتيب الاستراتيجيات اعتمادًا على الأداء التاريخي المتاح.

### `core/risk.py`
وظائف مرتبطة بالمخاطرة وحدودها وحالة الخسائر.

### `core/models.py`
نماذج البيانات الداخلية مثل Side وRegime وSignal وTradeState.

### `core/mt5_gateway.py`
طبقة MetaTrader 5: الاتصال، تسجيل الدخول، account info، symbols، ticks، bars، positions، order check/send، تعديل SL، الإغلاق، وقراءة تاريخ/بيانات الوسيط المطلوبة للمحرك.

### `core/broker_resolver.py`
مساعدة في اكتشاف/حل معلومات سيرفر الوسيط عند الحاجة لعملية تسجيل الدخول.

### `core/config.py`
إعدادات bootstrap/runtime من `.env` باستخدام `pydantic-settings`. إعدادات التداول المتغيرة ليست كلها هنا؛ كثير منها يحفظ في SQLite لكل حساب.

### `core/web_research.py`
مسار بحث خارجي محدود يمكن استخدامه عندما يحتاج التحليل إلى سياق بحثي إضافي. فشل البحث لا يتحول تلقائيًا إلى تصريح تداول.

### `bot/telegram_app.py`
واجهة Telegram: Dashboard، تشغيل/إيقاف، الحساب، الرموز، الإعدادات، المخاطرة، الثقة، حدود الخسارة، AI controls، الصحة، الأداء والصفقات.

### `bot/v2_views.py`
تنسيق شاشات Telegram المساعدة مثل AI Center والأداء والمراكز وصحة النظام.

### `api/control_api.py`
FastAPI الخاص بـT4Bot. يقدم health/snapshot/start/stop/login/settings/symbols/analysis/audit/history/media/WebSocket.

### `api/events.py`
EventHub وبث أحداث المحرك وحفظ/تقديم وسائط الصفقات لـT4Bot.

### `storage/db.py`
طبقة SQLite غير المتزامنة. تنشئ وتدير `settings` و`audit`، وتوفر الأداء الحديث، audit، وتاريخ الصفقات المغلقة مع account scoping.

### `storage/bot.db`
بيانات runtime الفعلية: الإعدادات، audit، تاريخ الأحداث وحالة الجلسة. مستبعدة من Git.

### `storage/t4bot_media/`
وسائط/صور الصفقات التي يستخدمها T4Bot. مستبعدة من Git.

---

## 4. دورة التشغيل من البداية للنهاية

المسار العام:

```text
systemd
  ↓
main.py
  ↓
DB + MT5Gateway + Engine
  ↓
استرجاع حساب MT5 إن وجد
  ↓
تحميل إعدادات الحساب من SQLite
  ↓
تشغيل Telegram / Control API حسب .env
  ↓
Engine.start()
  ↓
فحص الحساب + Algo Trading + الحدود
  ↓
المسح على symbols المختارة
  ↓
Ticks + M1 + M5 + M15 + H1
  ↓
AINativeSelector.snapshot()
  ↓
NVIDIA NIM
  ↓
SIGNAL أو NO_TRADE
  ↓
تحقق Engine من الثقة/السعر/السبريد/المخاطرة/المراكز
  ↓
MT5Gateway → order_check/order_send
  ↓
TradeState
  ↓
SL/TP + Protection + Trailing
  ↓
Audit + Telegram/T4Bot events
```

---

## 5. AI-native بالتفصيل

النموذج الافتراضي في المصدر:

```text
google/diffusiongemma-26b-a4b-it
```

ويمكن تحديده عبر:

```dotenv
MTBOT_AI_NATIVE_MODEL=...
```

الطلب يستخدم حاليًا:

```text
temperature = 0.0
top_p       = 1.0
max_tokens  = 512
stream      = false
```

الـsnapshot يتضمن على الأقل:

- symbol.
- point/digits.
- bid/ask.
- spread بالنقاط.
- tick momentum قصير ومتوسط.
- tick range.
- ATR على M5 بالنقاط.
- آخر بيانات M1/M5/M15/H1.
- أداء الاستراتيجيات عند توفره.
- news/research context عند توفره.
- قائمة playbooks.

قرار SIGNAL يجب أن يحتوي قيمًا قابلة للتحقق مثل:

```json
{
  "decision": "SIGNAL",
  "side": "BUY",
  "strategy_id": "strategy_name",
  "confidence": 78,
  "regime": "TREND",
  "sl_price": 1.2345,
  "tp_price": 1.2400,
  "protection_pct": 30,
  "trailing_gap_pct": 5,
  "expected_duration_minutes": 7,
  "research_required": false,
  "research_query": ""
}
```

هذا مثال صيغة فقط وليس توصية تداول.

### حدود parser الحالية

- `confidence`: من 0 إلى 100.
- `regime`: `TREND`, `RANGE`, `BREAKOUT`, `VOLATILE`, `MIXED`, `UNKNOWN`.
- SIGNAL: `BUY` أو `SELL` فقط.
- `sl_price` و`tp_price`: أكبر من صفر.
- `protection_pct`: من 15% إلى 80%.
- `trailing_gap_pct`: من 2% إلى 25%.
- `expected_duration_minutes`: من 2 إلى 10 دقائق كقيمة توقع/وصف للصفقة.

يدعم parser بعض المرادفات مثل `TRENDING → TREND` و`SIDEWAYS → RANGE`، كما يقبل نسبة مكتوبة مثل `30%`. إذا كان رد النموذج غير صالح من ناحية JSON/schema، توجد محاولة واحدة تطلب إعادة نفس القرار بصيغة JSON صحيحة دون تغيير أطروحة BUY/SELL أو اختراع بيانات سوق.

### مهم: مدة AI ليست مؤقت إغلاق

رغم أن AI يعيد `expected_duration_minutes`، **النسخة الحالية لا تحتوي `MAX_DURATION_EXIT`**. انتهاء المدة المتوقعة وحده لا يغلق المركز. المركز يبقى تحت SL/TP وإدارة الحماية/Trailing ومنطق الإغلاق الفعلي.

### Cache ومنع التكرار

مفتاح snapshot يعتمد على حالة آخر bars في M1/M5/M15/H1 وليس micro-noise لكل tick. الهدف منع ضرب المزود بطلب جديد في كل polling cycle. SIGNAL المستهلك لا يعاد استخدامه لنفس snapshot.

### Rate limiting

توجد:

- مهلة لكل symbol.
- مهلة عالمية بين طلبات AI.
- provider backoff.
- تعامل خاص مع HTTP 429.
- timeout لطلب AI.

إذا كان `NVIDIA_API_KEY` غير موجود، يرجع المسار `NO_TRADE / AI_UNAVAILABLE` بدل فتح صفقة بلا AI.

---

## 6. Manual Signal Mode

Engine يحتوي مسار Manual Signal Mode باستخدام ملفي runtime:

```text
storage/manual_signal_mode
storage/manual_signal.json
```

وجود `storage/manual_signal_mode` يفعّل النمط اليدوي. عندها ينتظر Engine إشارة مرسلة في `manual_signal.json` بدل الاعتماد على المسار التلقائي لذلك القرار. بعد استهلاك الإشارة يسجل:

```text
MANUAL_SIGNAL_CONSUMED
```

ويحذف ملف الإشارة المستهلكة. أوامر هذا المسار تحمل تعليق:

```text
TGSCALP_MANUAL
```

الملفان مستبعدان من Git لأنهما حالة runtime وليسا source code.

> لا تنشئ `manual_signal.json` يدويًا بدون الالتزام بالـschema الذي يتوقعه Engine؛ قرار غير صالح يتم رفضه ولا ينبغي استخدام الملف كطريقة لتجاوز فحوص المخاطرة والتنفيذ.

---

## 7. إدارة المخاطرة

القيم الافتراضية الأساسية في Engine عند عدم وجود profile محفوظ للحساب:

| الإعداد | الافتراضي |
|---|---:|
| `risk_pct` | 0.25% من Equity |
| `rr` | 3.0 |
| `min_confidence` | 75% |
| `protection_pct` | 45% |
| `trailing_gap_pct` | 5% |
| `max_trade_minutes` | 10 (إعداد تاريخي/عرض؛ ليس MAX_DURATION_EXIT) |
| `max_positions` | 1 |
| `max_consecutive_losses` | 3 |
| `daily_loss_limit_pct` | 2% |
| `session_profit_limit` | 0 = معطل |
| re-entry cooldown | 120 ثانية |

`risk_pct` يطبق على Equity، ثم يحسب المحرك الخسارة المتوقعة للوت مقابل SL ويخفض الحجم عند الحاجة حتى لا تتجاوز المخاطرة الحد المحسوب. كما يسجل الفرق بين المخاطرة المخططة والفعلية (`risk drift`) بعد التنفيذ.

إعدادات الحساب تحفظ بالشكل:

```text
account:<MT5_LOGIN>:<setting>
```

لذلك إعدادات حساب لا يفترض أن تنتقل تلقائيًا إلى حساب آخر.

---

## 8. AI Controls / Overrides

Engine يدعم overrides لحساب معين. القيمة `0` تعني عادة: اترك القرار للـAI.

المفاتيح الموجودة تشمل:

```text
ai_rr_override
ai_sl_points_override
ai_tp_points_override
ai_protection_override
ai_trailing_override
ai_duration_override
```

عند AI-native، المحرك يأخذ SL/TP/Protection/Trailing/Duration من قرار AI ثم يطبق override المفعّل إن كانت قيمته أكبر من صفر، مع بقاء فحوص النطاقات والأمان.

---

## 9. الحماية وTrailing

الحماية مرتبطة بتقدم السعر من Entry باتجاه TP.

بصورة مبسطة لصفقة BUY:

```text
target_distance = TP - Entry
progress        = (best_price - Entry) / target_distance
trigger         = protection_pct / 100
```

عندما يصل التقدم إلى trigger، تتفعل حماية الربح ويبدأ trailing. SELL يعكس اتجاه الحساب.

### Tick-aware management

المحرك لا يعتمد فقط على السعر الذي يصادفه لحظة دورة الإدارة؛ يستخدم tick history لتحديد أفضل حركة مواتية رآها السوق، حتى لا تضيع لمسة سعر سريعة بين دورتي polling.

إذا فشل جلب tick history مؤقتًا، النسخة الحالية تحتوي fail-safe وتسجل:

```text
MANAGEMENT_TICKS_UNAVAILABLE
```

بدل أن يؤدي الخطأ وحده إلى انهيار دورة إدارة الصفقة.

### Trailing gap

بعد تفعيل الحماية، يحسب trailing gap كنسبة من المسافة الكاملة بين Entry وTP، ثم يحاول تحريك SL في اتجاه الربح فقط مع مراعاة قيود الوسيط/الرمز. لا يفترض أن يرجع SL للخلف ويزيد المخاطرة.

---

## 10. SL / TP / الخروج

المركز يعتمد على SL وTP الفعليين المرسلين/المعدلين في MT5، إضافة إلى إدارة Protection/Trailing.

أحداث الإغلاق التي تدعمها قاعدة التاريخ تشمل تاريخيًا:

```text
TP
SL
PROTECTED_EXIT
TRAILING_EXIT
BREAKEVEN_EXIT
POSITION_CLOSED
```

قد تحتوي قاعدة بيانات قديمة على أحداث `MAX_DURATION_EXIT` من إصدارات سابقة، ولهذا ما زال قارئ التاريخ يعرف الاسم. **هذا لا يعني أن النسخة الحالية تنفذ إغلاقًا بالمدة**؛ منطق `MAX_DURATION_EXIT` غير موجود في Engine الحالي.

---

## 11. حدود التشغيل

قبل فتح صفقة يطبق Engine عدة طبقات، منها بحسب الحالة:

- وجود حساب MT5 صالح.
- السماح بـDemo أو فتح قفل Real للحساب الحقيقي.
- جاهزية Algo Trading/MT5.
- صلاحية إعدادات المخاطرة.
- حد عدد المراكز.
- حد Equity اليومي.
- حد الخسائر المتتالية.
- هدف ربح الجلسة إذا كان مفعّلًا.
- توفر tick/bars المطلوبة.
- confidence threshold.
- spread/quote checks.
- cooldown لإعادة الدخول.
- فحوص حجم اللوت والمخاطرة.
- `order_check` ثم `order_send` عبر MT5.

إزالة حظر rollover الثابت لا تعني تجاهل ظروف السوق؛ فحوص السبريد والسعر والمخاطرة تبقى قائمة.

---

## 12. Demo وReal

Demo يعمل دون قفل Real.

في الحساب الحقيقي، مجرد تسجيل الدخول لا يعني السماح بالتداول. Engine يفحص نوع الحساب ويحتاج:

```text
real_trading_enabled = true
```

لذلك الحساب الحقيقي يمكن أن يكون متصلًا بينما Engine يرفض فتح صفقات إلى أن يفتح القفل لذلك الحساب.

القيمة account-scoped داخل SQLite.

---

## 13. Session Profit Limit

`session_profit_limit = 0` يعني أن الهدف معطل.

عند تفعيله، يحتفظ Engine بـ:

```text
session_start_balance
session_profit_hit
session_active
```

إذا وصل الربح المحقق مقارنةً بـBalance بداية الجلسة إلى الحد، يسجل:

```text
SESSION_PROFIT_LIMIT
```

ويوقف التحليل والدخول الجديد وفق منطق الجلسة. حالة الجلسة تحفظ في SQLite بحيث يمكن استعادتها بعد restart.

---

## 14. Daily Equity Loss Limit

المحرك يحتفظ baseline يومي للحساب ويقارن Equity بالحد المحدد. عند تجاوز الحد يسجل حدثًا مثل:

```text
DAILY_EQUITY_LIMIT
```

ويمنع الدخول الجديد وفق منطق الحماية اليومية.

---

## 15. Telegram

يتم تفعيله من `.env`:

```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_ALLOWED_USER_ID=<user-id>
```

واجهة Telegram الحالية تحتوي أقسامًا مثل:

- Dashboard.
- التداول: Start / Stop / Status.
- AI Center.
- الأداء.
- الصفقات المفتوحة.
- الأسواق والرموز.
- الإعدادات.
- صحة النظام.
- الحساب وربط MT5.
- Real lock عند الحساب الحقيقي.

أوامر Telegram المسجلة حاليًا:

```text
/start
/cancel
/clean
/symbol
/symbols
/rr
/risk
/confidence
/protection
/maxpos
/maxloss
/maxduration
/dailyloss
```

الواجهة تقيد المستخدم بواسطة `TELEGRAM_ALLOWED_USER_ID`. كما توجد معالجة لـTelegram `RetryAfter` وflood control، وتحديث لرسالة الصفقة والنتيجة.

---

## 16. T4Bot Control API

يتم تفعيله مثلًا:

```dotenv
CONTROL_API_ENABLED=true
CONTROL_API_HOST=127.0.0.1
CONTROL_API_PORT=7099
CONTROL_API_TOKEN=<strong-secret>
```

المسارات الحالية:

```text
GET   /v1/health
GET   /v1/snapshot
POST  /v1/engine/start
POST  /v1/engine/stop
POST  /v1/account/login
PATCH /v1/settings
GET   /v1/symbols
PUT   /v1/symbols
POST  /v1/analysis/run
GET   /v1/audit
GET   /v1/trades/history
GET   /v1/media/{media_id}
WS    /v1/ws
```

الـAPI مصمم كواجهة خاصة ويستخدم Bearer token. يفضل إبقاؤه على loopback وعدم فتح 7099 مباشرة للإنترنت؛ استخدم private tunnel أو reverse proxy آمن عند الحاجة للوصول الخارجي.

WebSocket يستخدم لبث تحديثات Engine/T4Bot في الوقت الحقيقي.

---

## 17. قاعدة البيانات

الافتراضي:

```text
storage/bot.db
```

الجداول الأساسية:

```sql
settings(key TEXT PRIMARY KEY, value TEXT)
audit(id INTEGER PRIMARY KEY, ts REAL, event TEXT, symbol TEXT, details TEXT)
```

SQLite يعمل مع:

```text
journal_mode = WAL
synchronous  = NORMAL
busy_timeout = 5000ms
foreign_keys = ON
```

`DB` يستخدم lock غير متزامن للكتابات لتقليل تعارض الكتابة داخل العملية نفسها.

### Audit

`audit` هو السجل التشغيلي للمحرك: قرارات AI، فتح/إغلاق، حماية، أخطاء، حدود يومية، أحداث الجلسة وغيرها.

### Strategy performance

`strategy_performance()` يربط OPEN بأول حدث إغلاق للتذكرة ويحسب نقاطًا مبسطة:

```text
ربح   = +1
تعادل =  0
خسارة = -1
```

### Trade history

`closed_trades()` يقرأ الصفقات المغلقة ويطبق account scoping عند إعطاء `account_login`. السجلات القديمة غير الموسومة لا تنسب تلقائيًا للحساب الحالي.

---

## 18. متغيرات البيئة

لا تضع أسرارًا داخل README أو source code.

### Telegram

```dotenv
TELEGRAM_ENABLED=
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_ID=
```

### Control API

```dotenv
CONTROL_API_ENABLED=
CONTROL_API_HOST=
CONTROL_API_PORT=
CONTROL_API_TOKEN=
```

### MT5

```dotenv
MT5_TERMINAL_PATH=
APP_MODE=
```

### تنفيذ السوق

`core/config.py` يدعم:

```dotenv
SPREAD_SAMPLE_SIZE=
MAX_SPREAD_MULTIPLIER=
MAX_SPREAD_POINTS=
MAX_SLIPPAGE_POINTS=
SLIPPAGE_ATR_FRACTION=
SLIPPAGE_MAX_HARD_CAP=
MIN_SL_POINTS=
POLL_INTERVAL_MS=
MAX_TICK_AGE_SECONDS=
```

### AI runtime

إعدادات `Settings` تشمل مفاتيح مثل:

```dotenv
AI_MAX_ERROR_STREAK=
AI_MAX_429_STREAK=
AI_429_COOLDOWN_SECONDS=
AI_SIGNAL_TTL_SECONDS=
AI_MIN_GAP_SECONDS=
AI_GLOBAL_MIN_GAP_SECONDS=
AI_SELF_CONSISTENCY_LOW=
AI_SELF_CONSISTENCY_HIGH=
AI_PROVIDER_FALLBACK_ENABLED=
```

و`AINativeSelector` يقرأ مباشرة:

```dotenv
NVIDIA_API_KEY=
MTBOT_AI_NATIVE_MODEL=
MTBOT_AI_NATIVE_TIMEOUT=
MTBOT_AI_NATIVE_MIN_CONF=
MTBOT_AI_NATIVE_MIN_INTERVAL=
MTBOT_AI_NATIVE_GLOBAL_INTERVAL=
MTBOT_AI_NATIVE_MAX_BACKOFF=
```

### Runtime/audit/chart

```dotenv
EXECUTION_NOTICE_MAX_ENTRIES=
REJECT_LOG_CACHE_SIZE=
CHART_SEND_ENABLED=
CHART_SEND_TIMEOUT_SECONDS=
TICK_WINDOW_MAX=
```

### DB

```dotenv
DB_PATH=
DB_BACKUP_ENABLED=
DB_BACKUP_INTERVAL_HOURS=
DB_BACKUP_KEEP=
DB_BACKUP_DIR=
```

بعض المتغيرات موجودة في `Settings` لأجزاء اختيارية/تاريخية؛ وجود اسم إعداد لا يعني بالضرورة أن كل مسار يستخدمه في كل نمط تشغيل.

---

## 19. `.gitignore` والأسرار

المشروع يستبعد عمدًا:

```text
.env
*.db
*.sqlite*
.mt5bot_credentials.json
__pycache__/
*.pyc
*.log
backup/bak patterns
storage/t4bot_media/
storage/manual_signal.json
storage/manual_signal_mode
```

لا تستخدم `git add -f` لهذه الملفات إلا إذا كنت تعرف بالضبط سبب ذلك. خصوصًا `.env` وcredentials يجب ألا ترفع إلى GitHub.

---

## 20. تسجيل دخول MT5

عند وجود credentials محفوظة محليًا، `main.py` يحاول استعادتها من:

```text
~/.mt5bot_credentials.json
```

الملف محلي ومستبعد من Git.

إذا لم يوجد حساب محفوظ، يبقى تسجيل الدخول متاحًا من واجهة تحكم مفعلة مثل Telegram/T4Bot.

---

## 21. المتطلبات

`requirements.txt` الحالي:

```text
python-telegram-bot==22.5
MetaTrader5>=5.0.45
pydantic-settings>=2.6
python-dotenv>=1.0
aiosqlite>=0.20
numpy>=1.26
pandas>=2.2
fastapi>=0.115,<1
uvicorn>=0.30,<1
PyJWT[crypto]>=2.9,<3
```

مهم: مكتبة `MetaTrader5` المستخدمة هنا تعمل مع Windows Python داخل Wine في إعداد الخادم الحالي. تشغيل Linux Python العادي ليس مساويًا لبيئة runtime المستخدمة للخدمة.

---

## 22. مسارات الخادم الحالية

المشروع:

```text
/home/ubuntu/.wine/drive_c/mt5bot
```

Wine prefix:

```text
/home/ubuntu/.wine
```

Windows Python المستخدم بواسطة الخدمة:

```text
/home/ubuntu/.wine/drive_c/users/ubuntu/AppData/Local/Programs/Python/Python311/python.exe
```

الخدمة:

```text
mtbot.service
```

WorkingDirectory:

```text
/home/ubuntu/.wine/drive_c/mt5bot
```

ExecStart الحالي:

```text
/opt/wine-staging/bin/wine /home/ubuntu/.wine/drive_c/users/ubuntu/AppData/Local/Programs/Python/Python311/python.exe -u main.py
```

---

## 23. أوامر إدارة الخدمة

الحالة:

```bash
systemctl status mtbot.service
```

معلومات مختصرة:

```bash
systemctl show mtbot.service -p ActiveState -p SubState -p MainPID -p ExecMainStartTimestamp -p NRestarts -p Result
```

السجل:

```bash
journalctl -u mtbot.service -n 100 --no-pager
```

إعادة التشغيل بعد تعديل source/config:

```bash
sudo systemctl restart mtbot.service
```

الإيقاف:

```bash
sudo systemctl stop mtbot.service
```

التشغيل:

```bash
sudo systemctl start mtbot.service
```

بسبب `Restart=always` في systemd، قتل عملية Python/Wine يدويًا ليس الطريقة الصحيحة لإيقاف الخدمة؛ systemd قد يعيد تشغيلها. استخدم `systemctl stop` عندما تريد إيقاف البوت فعلًا.

---

## 24. التحقق بعد Deployment

تحقق من الخدمة:

```bash
systemctl show mtbot.service -p ActiveState -p SubState -p MainPID -p Result
```

ثم راقب السجل:

```bash
journalctl -u mtbot.service -n 100 --no-pager
```

أخطاء مهمة للبحث عنها:

```text
ENGINE_ERROR
AI_NATIVE_ERROR
AI_NATIVE_429
MANAGEMENT_TICKS_UNAVAILABLE
UNMANAGED_POSITION
TRADE_CHART_FAILED
```

`MANAGEMENT_TICKS_UNAVAILABLE` يعني أن fail-safe اشتغل؛ لا يعني وحده أن الصفقة أغلقت أو أن الخدمة انهارت.

---

## 25. استكشاف أخطاء AI

### `AI_UNAVAILABLE`
تحقق من وجود `NVIDIA_API_KEY` في البيئة التي تصل للخدمة.

### `AI_PROVIDER_429`
المزود طبق rate limit. Selector يدخل backoff بدل إرسال طلبات متلاحقة.

### `invalid AI JSON`
النموذج لم يعد JSON صالحًا. النسخة الحالية تحاول إصلاح الصياغة مرة واحدة ثم تفشل مغلقًا إلى NO_TRADE إذا استمر الخطأ.

### `invalid regime`
القيمة ليست ضمن enum المقبول ولم تكن من المرادفات التي يطبعها parser.

### `invalid protection percent`
القيمة خارج 15–80%.

### `invalid trailing gap percent`
القيمة خارج 2–25%.

---

## 26. استكشاف أخطاء MT5

إذا كان MT5 غير جاهز:

1. تحقق أن terminal يعمل داخل نفس Wine prefix.
2. تحقق من الحساب/server/password المحفوظ أو أعد تسجيل الدخول من واجهة التحكم.
3. تحقق من Algo Trading.
4. تحقق من أن الرمز متاح لدى الوسيط.
5. تحقق من tick حديث ومن عدم إغلاق السوق.
6. افحص journal بدل افتراض أن الخطأ من AI.

`order_check` خطوة تحقق قبل الإرسال، لكنه ليس ضمانًا أن `order_send` سينجح؛ السعر والسيولة والهامش وحالة السوق قد تتغير.

---

## 27. ملاحظات عن الرموز

Engine يبدأ افتراضيًا بـ:

```text
EURUSD
```

لكن قائمة الرموز تحفظ لكل حساب ويمكن تعديلها من واجهات التحكم. توفر الرمز يعتمد على الوسيط والحساب، لذلك لا تفترض أن اسم رمز من وسيط آخر موجود بنفس الصيغة.

---

## 28. ما الذي أزيل في V1 CLEAN؟

تم تبسيط المستودع وإزالة عناصر التطوير/الإصدارات القديمة التي لا يعتمد عليها runtime الحالي، ومنها:

```text
tests/
backtest.py
scripts/
.github/ workflow القديم
historical VERSION-* backups
```

كما تم حذف جميع فروع Git القديمة، وأصبح:

```text
AIplus
```

هو الفرع الوحيد محليًا وعلى GitHub والـdefault branch.

بيانات runtime لم تدخل المستودع: `.env`, `bot.db`, `t4bot_media`, manual signal files.

---

## 29. فلسفة fail-closed

في الأماكن الحساسة، الهدف أن الخطأ لا يتحول إلى صفقة تلقائيًا. أمثلة:

- لا API key للـAI → NO_TRADE.
- JSON AI غير صالح بعد محاولة التصحيح → NO_TRADE/error path.
- confidence أقل من الحد → رفض.
- إعدادات حماية خارج النطاق → رفض.
- Real account والقفل غير مفعّل → لا تشغيل تداول حقيقي.
- بيانات MT5 الأساسية غير جاهزة → لا دخول.

وفي إدارة مركز موجود بالفعل، الهدف مختلف: خطأ مؤقت في tick history لا ينبغي أن يسقط دورة الإدارة كلها، لذلك توجد fallback/fail-safe مع audit.

---

## 30. خلاصة معمارية

MTBOT AIplus ليس سكربتًا يرسل BUY/SELL مباشرة من نص AI. القرار يمر عبر سلسلة منفصلة من المسؤوليات:

```text
Market Data
   ↓
AI decision + schema validation
   ↓
Engine filters
   ↓
Account/session limits
   ↓
Risk sizing
   ↓
Broker/MT5 checks
   ↓
Execution
   ↓
Independent trade management
   ↓
Protection / Trailing / SL / TP
   ↓
Audit + UI events
```

هذا الفصل مقصود: AI يختار الصفقة وإعداداتها ضمن schema، بينما Engine وMT5Gateway يفرضان قيود التنفيذ والمخاطرة ويتابعان المركز بعد فتحه.

---

## 31. GitHub

المستودع:

```text
Nashmei/Mtbot
```

الفرع الوحيد والافتراضي:

```text
AIplus
```

عند النشر على الخادم، تأكد أنك على `AIplus` وأن working tree لا يحتوي تغييرات غير مقصودة قبل السحب أو إعادة التشغيل.
