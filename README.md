# MT5 AI Scalper — REAL/DEMO branch

بوت تداول آلي لـ MetaTrader 5 مكتوب بـ Python. هذا الفرع مشتق من `AI` ومخصص لتطوير مسار **REAL / DEMO**، لكن **الحالة البرمجية الحالية ما زالت DEMO-only**: المحرك وواجهة T4Bot يرفضان الحساب غير التجريبي حتى يتم تنفيذ دعم Real وحواجزه الأمنية واختباره صراحة.

> **تنبيه:** لا تعتبر اسم الفرع دليلاً على تفعيل التداول الحقيقي. في الكود الحالي يبدأ Engine فقط عندما يكون `account.trade_mode == ACCOUNT_TRADE_MODE_DEMO`، وControl API يرفض تسجيل الحساب غير التجريبي.

## الحالة الحالية

- الفرع: `REAL/DEMO`، مشتق من `AI`.
- التنفيذ الحالي: **DEMO-only**.
- منطق التداول في هذا الفرع: **AI-native**؛ `Engine.ai_native_only = True`.
- Telegram موجود ويمكن تعطيله عبر `TELEGRAM_ENABLED=false`.
- T4Bot Control API اختياري عبر `CONTROL_API_ENABLED=true`.
- Control API محمي بـ Bearer token ولا يفعّل Swagger/OpenAPI public docs.
- T4Bot يحصل على snapshot وحالة الحساب والمراكز والإعدادات والتحليل وسجل التداول، ويستقبل الأحداث عبر WebSocket.
- إعدادات التداول محفوظة **لكل حساب MT5 بشكل مستقل** داخل SQLite.
- الحساب الجديد لا يرث إعدادات حساب آخر. يوجد migration للإعدادات القديمة فقط عند استعادة الحساب المحفوظ في startup.
- بيانات دخول MT5 المحفوظة محلياً تستخدم `~/.mt5bot_credentials.json`.
- سجل التشغيل والإعدادات في `storage/bot.db`.
- البيئة المستهدفة على الخادم: Ubuntu + Wine Staging + Windows Python + MT5.

## المعمارية الحالية

```text
main.py
 ├─ core/config.py
 ├─ storage/db.py
 ├─ core/mt5_gateway.py
 ├─ core/engine.py
 │   ├─ core/analyzer.py
 │   ├─ core/risk.py
 │   ├─ core/ai_native_selector.py
 │   ├─ core/ai_advisor.py
 │   └─ core/web_research.py
 ├─ bot/telegram_app.py
 └─ api/
     ├─ control_api.py
     └─ events.py
```

`main.py` ينشئ DB وMT5Gateway وEngine وEventHub. إذا وجد credentials محفوظة يحاول استعادة حساب MT5، ثم يحمّل profile الإعدادات الخاص بذلك الحساب. بعد ذلك يمكن تشغيل Control API وTelegram كلٌ حسب إعداداته.

## AI-native

الفرع الحالي يتجاوز توليد الدخول التقليدي عند التداول الفعلي ويستخدم `AINativeSelector` لاتخاذ قرار الصفقة. القرار يعاد بشكل منظم ويتضمن عند وجود SIGNAL عناصر مثل:

- BUY / SELL.
- strategy id.
- confidence.
- regime.
- SL وTP.
- protection وtrailing.
- مدة متوقعة.
- سبب القرار.
- إمكانية طلب بحث ويب عند الحاجة.

الـAI يستخدم cache على حالة الشموع لتجنب تكرار الطلب لنفس السوق، ويمنع إعادة استخدام SIGNAL مستهلك، ويطبق cooldown لكل رمز وفاصل عالمي وprovider backoff عند 429.

إذا كان `NVIDIA_API_KEY` غير موجود يفشل المسار بشكل مغلق إلى `NO_TRADE` بدلاً من فتح صفقة بدون قرار AI.

توجد أيضاً `AIAdvisor` كطبقة AI أخرى في المشروع، بنموذج أساسي وإمكانية escalation لنموذج أعمق عند انخفاض الثقة. الردود تتحقق من schema وتفشل إلى REJECT عند الخطأ.

## البحث الإخباري

`core/web_research.py` يوفر بحثاً محدوداً عند طلب AI للبحث، باستخدام GDELT مع نطاقات موثوقة محددة في الكود، ويرصد عناوين حديثة مرتبطة بأحداث عالية التأثير. فشل البحث لا يتحول تلقائياً إلى إذن بالتداول، ويتم تسجيل النتيجة في audit.

## إعدادات التداول لكل حساب

الإعدادات التشغيلية الأساسية داخل Engine تشمل افتراضياً:

| الإعداد | الافتراضي |
|---|---:|
| Risk per trade | 0.25% |
| Legacy R:R | 1:3 |
| Minimum confidence | 75% |
| Protection | 45% |
| Trailing gap | 5% |
| Max trade duration | 10 دقائق |
| Max positions | 1 |
| Consecutive-loss limit | 3 |
| Daily equity loss limit | 2% |
| Session profit target | معطل (0) |
| Re-entry cooldown | 120 ثانية |

في AI-native توجد overrides مستقلة لـ RR وSL وTP وProtection وTrailing والمدة. القيمة `0` تعني أن AI يقرر ذلك العنصر بدلاً من فرض override يدوي.

مفاتيح الإعدادات للحساب المتصل تحفظ بصيغة:

```text
account:<MT5_LOGIN>:<setting>
```

وبذلك لا تنتقل إعدادات حساب إلى حساب آخر.

## هدف الجلسة

يوجد `session_profit_limit` مع `session_start_balance` وحالة session محفوظة في SQLite. عند بدء جلسة نظيفة يسجل المحرك Balance البداية، ويمكن استخدام هدف ربح للجلسة. حالة الجلسة تبقى قابلة للاستعادة بعد restart بدلاً من اعتبار restart جلسة جديدة تلقائياً.

## التنفيذ وإدارة المخاطر

قبل بدء المحرك، الكود الحالي يتحقق من:

1. صلاحية إعدادات المخاطرة والحدود.
2. وجود حساب MT5.
3. أن الحساب **DEMO**.
4. حد Equity اليومي.
5. جاهزية MT5 وAlgo Trading.
6. إمكانية قراءة المراكز.
7. عدم وجود مركز قديم يحمل magic البوت `4009` وغير متتبع.

التنفيذ يمر عبر `MT5Gateway` وRisk ويستخدم بيانات الرمز والسعر الفعلي وسياسة الملء المتاحة وقيود الوسيط. لا تعتبر نتيجة `order_check` ضماناً لقبول `order_send`.

## متابعة الصفقات

كل ticket له `TradeState` مستقل. المحرك يحتفظ بحالة الصفقات المتتبعة، ويراقب الحماية وtrailing والمدة والإغلاق، ويسجل أحداث التنفيذ والنتائج في audit.

إذا ظهر مركز للبوت غير موجود في ذاكرة المحرك، يمنع بدء تشغيل جديد حتى تتم مراجعته. هذا يمنع تبني مركز قديم بصورة عمياء بعد restart أو deployment.

## T4Bot Control API

يعمل فقط إذا:

```dotenv
CONTROL_API_ENABLED=true
CONTROL_API_TOKEN=<strong-secret>
```

الإعداد الافتراضي:

```text
host = 127.0.0.1
port = 7099
```

المسارات الحالية تشمل:

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

جميعها محمية بالمصادقة المناسبة. WebSocket يرسل أحداث Engine فوراً، ويحدّث snapshot لحالة MT5 مع coalescing للتحديثات غير المتغيرة.

### قيود الحساب الحالية في T4Bot

`POST /v1/account/login` يرفض حالياً أي حساب ليس DEMO. لذلك **Real غير مفعّل بعد** حتى لو كان اسم الفرع `REAL/DEMO`.

## Telegram

Telegram واجهة تحكم إضافية وليست إلزامية عند تعطيلها من البيئة. عند تشغيلها تستخدم `TELEGRAM_ALLOWED_USER_ID` لتقييد المستخدم.

تدعم الوظائف الموجودة في المشروع مثل:

- حالة المحرك والحساب.
- تسجيل دخول MT5.
- اختيار رمز أو عدة رموز.
- Start / Stop.
- تحليل.
- إعدادات المخاطرة والثقة والحماية والحدود.
- تنظيف رسائل الواجهة.
- التعامل مع Telegram flood / RetryAfter.
- تحديث رسائل الصفقة أثناء تغير حالتها.

لا تشغل أكثر من polling instance لنفس Telegram bot token.

## قاعدة البيانات

المسار الافتراضي:

```text
storage/bot.db
```

الجداول الأساسية:

- `settings(key, value)`
- `audit(id, ts, event, symbol, details)`

توجد وظائف لاستخراج الأداء الحديث للاستراتيجيات، audit الحديث، وتاريخ الصفقات المغلقة. تاريخ الصفقات المقدم للحساب الحالي يطبق account scoping ولا ينسب السجلات القديمة غير الموسومة إلى الحساب الحالي.

**لا تحذف قاعدة البيانات أثناء deployment** إذا كنت تريد الاحتفاظ بالإعدادات والسجل وحالة الجلسة.

## إعدادات .env

`core/config.py` يستخدم .env لإعدادات bootstrap/runtime فقط. إعدادات التداول القابلة للتغيير تحفظ في SQLite لكل حساب ولا يفترض أن تأتي من .env.

مثال:

```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_ID=...

CONTROL_API_ENABLED=false
CONTROL_API_HOST=127.0.0.1
CONTROL_API_PORT=7099
CONTROL_API_TOKEN=...

MT5_TERMINAL_PATH=C:\\Program Files\\MetaTrader 5\\terminal64.exe
APP_MODE=DEMO

SPREAD_SAMPLE_SIZE=60
MAX_SPREAD_MULTIPLIER=1.8
MAX_SPREAD_POINTS=0
MAX_SLIPPAGE_POINTS=10
MIN_SL_POINTS=10
POLL_INTERVAL_MS=150
MAX_TICK_AGE_SECONDS=15

NVIDIA_API_KEY=...
```

يوجد في config أيضاً إعدادات APNs، لكن تنبيهات التداول الحالية لـT4Bot تبث كأحداث realtime ويحوّلها تطبيق iOS إلى local notifications؛ وجود حقول APNs في config لا يعني أن Mtbot يرسل APNs حالياً.

لا ترفع `.env` أو credentials أو tokens إلى GitHub.

## المتطلبات

المتطلبات الحالية في `requirements.txt`:

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

في Linux/Wine يجب تشغيل Windows Python داخل Wine prefix المتصل بنفس MT5؛ Python Linux الأصلي ليس بديلاً عن بيئة MetaTrader5 المستخدمة هنا.

## بيئة الخادم المعروفة

المشروع:

```text
/home/ubuntu/.wine/drive_c/mt5bot
```

MT5:

```text
/home/ubuntu/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe
```

Windows Python:

```text
C:\users\ubuntu\AppData\Local\Programs\Python\Python311\python.exe
```

Wine prefix:

```text
/home/ubuntu/.wine
```

واجهة X المستخدمة سابقاً:

```text
DISPLAY=:1
XAUTHORITY=/home/ubuntu/.Xauthority
```

هذه معلومات بيئة معروفة وليست إثباتاً للحالة اللحظية للسيرفر.

## التشغيل

مثال تشغيل يدوي في بيئة الخادم المعروفة:

```bash
cd "$HOME/.wine/drive_c/mt5bot"
WINEDEBUG=-all PYTHONUNBUFFERED=1 /opt/wine-staging/bin/wine   "C:\users\ubuntu\AppData\Local\Programs\Python\Python311\python.exe" -u main.py
```

لا تشغّل نسخة يدوية بالتزامن مع `mtbot.service`، ولا تشغّل Terminal إضافياً داخل نفس Wine prefix بدون حاجة.

## systemd

الخدمة المعروفة للبوت هي:

```text
mtbot.service
```

فحص الحالة بدون عرض الأسرار:

```bash
systemctl show mtbot.service -p ActiveState -p SubState -p MainPID -p NRestarts
```

وجود `terminal64.exe` كعملية لا يكفي لإثبات جاهزية MT5. يجب نجاح `mt5.initialize()` ثم التحقق من connected وtrade permissions.

## MT5 / Wine IPC

حدث تاريخياً بعد تحديث MT5 إلى build 6207 خطأ:

```text
(-10005, 'IPC timeout')
```

ثم أبلغ صاحب السيرفر لاحقاً أن المشكلة حُلّت. لذلك هذه المعلومة تاريخية وليست وصفاً للحالة الحالية. عند ظهور المشكلة مجدداً يجب تشخيص طبقة MT5 ↔ Wine ↔ MetaTrader5 Python قبل تغيير Analyzer أو استراتيجية التداول.

## الاختبار التاريخي

`backtest.py` الموجود حالياً ما زال signal replay للـAnalyzer التقليدي على M1/M5/M15/H1 لرموز محددة، ولا يمثل backtest كاملاً لمسار AI-native الحالي.

لا يحاكي بدقة:

- broker fills.
- fees/commission.
- slippage الفعلي.
- rollover.
- margin.
- portfolio concurrency.
- stop/freeze restrictions.
- provider latency أو قرارات AI-native الحية.

لذلك لا تستخدم Equity index الناتج كتوقع لأداء الحساب الحقيقي.

## Deployment لهذا الفرع

هذا الفرع **ليس `main`**. عند نشره لاحقاً يجب تحديد `REAL/DEMO` صراحة وعدم استخدام أمر deployment الخاص بـmain بالخطأ.

لا يتم أي deployment لمجرد تحديث GitHub. النشر على السيرفر خطوة مستقلة ويجب التأكد قبلها من:

- الفرع والـcommit المطلوبين.
- حفظ `.env`.
- حفظ `storage/bot.db`.
- عدم حذف credentials المحلية.
- عدم تشغيل نسختين من البوت أو Telegram polling.
- نجاح MT5 IPC بعد إعادة التشغيل.

## الأمان

- التنفيذ الحالي DEMO-only.
- لا تضع Telegram token أو Control API token أو NVIDIA key أو MT5 password أو مفاتيح AWS داخل Git.
- Control API يجب أن يبقى خلف مصادقة وشبكة موثوقة.
- لا تعتبر اسم `REAL/DEMO` تصريحاً بالتداول الحقيقي.
- دعم Real يجب أن يضاف كميزة صريحة مع حواجز واضحة واختبارات Demo أولاً.
- لا تشغل المحرك إذا فشل MT5 IPC أو كانت صلاحيات التداول غير جاهزة.
- لا تحذف SQLite أثناء deployment.
- راقب audit وsystemd logs بعد أي نشر.

## قاعدة العمل للمطور

أي تعديل يجب تتبعه عبر المسار الكامل المتأثر:

```text
Telegram / T4Bot
       ↓
     Engine
       ↓
AI / Analyzer / Risk
       ↓
   MT5Gateway
       ↓
MetaTrader5 Terminal
       ↓
SQLite audit/settings
```

قبل تعديل تسجيل الدخول أو دعم Real/DEMO، راجع أيضاً account-scoped settings وحالة session والمراكز المفتوحة وإعادة التشغيل. قبل تعديل إدارة الصفقة، راجع التنفيذ والحماية وtrailing والمدة والإشعارات وسجل audit معاً.

## الخطوة القادمة للفرع REAL/DEMO

الهدف المقصود من هذا الفرع هو إضافة دعم حسابات **Real + Demo** بصورة صريحة وآمنة. هذا العمل **لم يُنفذ بعد** في الحالة الموثقة هنا.

قبل اعتبار Real مدعوماً يجب على الأقل تحديث واختبار:

1. سياسة قبول نوع الحساب في Engine.
2. سياسة login في Control API وTelegram.
3. إظهار نوع الحساب بوضوح في كل واجهة.
4. قفل/تأكيد مستقل للتداول الحقيقي.
5. حفظ حالة Real authorization بطريقة آمنة.
6. منع أي انتقال صامت من Demo إلى Real.
7. اختبارات restart/reconnect/account switching.
8. اختبار إدارة المراكز الموجودة قبل التشغيل.
9. حدود مخاطرة مناسبة للحساب الحقيقي.
10. Demo soak واختبار شامل قبل تفعيل Real.

---

**الحالة الموثقة من الكود الحالي: فرع REAL/DEMO موجود، لكن التنفيذ لا يزال DEMO-only وAI-native.**
