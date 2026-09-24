# MT5 Telegram Scalper

بوت تداول آلي لـ MetaTrader 5 يتم التحكم به من Telegram، مكتوب بـ Python ومصمم حالياً للعمل في وضع **DEMO فقط**. المشروع يجمع تحليل السوق متعدد الاستراتيجيات، إدارة المخاطر، تنفيذ ومتابعة الصفقات، واجهة Telegram عربية، وسجل SQLite للتدقيق والإعدادات.

> **تنبيه:** التداول الحقيقي (Live) مقفل في النسخة الحالية. المشروع للاختبار على حساب تجريبي، ولا توجد ضمانات ربح.

## الحالة الحالية

- التطبيق: **DEMO-only**؛ زر Live مقفل.
- البنية الحالية: **مستخدم Telegram واحد + Terminal MT5 واحد مشترك**.
- Telegram محمي بـ `TELEGRAM_ALLOWED_USER_ID`.
- بيانات دخول MT5 التي يدخلها المستخدم من Telegram تحفظ محلياً في `~/.mt5bot_credentials.json` بصلاحية `0600`.
- الإعدادات التشغيلية تحفظ في SQLite وتستعاد عند إعادة تشغيل البوت.
- البيئة المستخدمة فعلياً على الخادم: Ubuntu + Wine + Windows Python + MT5.
- آخر بيئة تم اختبارها: Wine Staging 11.18، Windows Python 3.11.9، حزمة MetaTrader5 5.0.6180.
- **ملاحظة تشغيلية مهمة (2026-09-23):** بعد تحديث Terminal تلقائياً من build 6204 إلى build 6207 ظهر `(-10005, 'IPC timeout')` بين حزمة Python وMT5 تحت Wine. لذلك يجب عدم اعتبار MT5 جاهزاً لمجرد أن عملية `terminal64.exe` تعمل؛ يلزم نجاح `mt5.initialize()` فعلياً قبل تشغيل التداول. مشكلة IPC هذه حُلّت لاحقاً بحسب إفادة صاحب السيرفر؛ لا تُعامل هذا الوصف التاريخي على أنه حالة التشغيل الحالية.

## المعمارية

مسار التشغيل الرئيسي:

```text
main.py
 ├─ core/config.py          إعدادات .env
 ├─ storage/db.py           SQLite: settings + audit
 ├─ core/mt5_gateway.py     طبقة MetaTrader5 API
 ├─ core/engine.py          دورة التداول وإدارة المراكز
 │   ├─ core/analyzer.py    تحليل السوق وتوليد الإشارات
 │   ├─ core/risk.py        السبريد وحساب الحجم
 │   └─ core/models.py      Signal / TradeState / Regime
 └─ bot/telegram_app.py     واجهة Telegram
```

`main.py` ينشئ قاعدة البيانات والـGateway والـEngine والواجهة، يستعيد إعدادات المستخدم، ثم يبدأ Telegram polling.

## التحليل والاستراتيجيات

`Analyzer` يستخدم أسعار ticks مع شموع MT5 المغلقة والسياق متعدد الأطر الزمنية. تصنيف السوق يتضمن `TREND` و`RANGE` و`BREAKOUT` و`VOLATILE` و`NO_TRADE`.

الاستراتيجيات الموجودة حالياً:

1. `scalp_breakout` — كسر M5 مع retest مباشر وتأكيد M15/H1.
2. `ema_cross_scalp` — تقاطع EMA 9/21 على شموع مغلقة (M1 أساساً وM5 fallback)، ولا يستخدم لـ XAUUSD.
3. `scalp_trend` — استمرار اتجاه مع M15 وpullback على EMA20 في M5 وتسارع لحظي.
4. `gold_scalp` — منطق مخصص لـ XAUUSD للحركة/التوسع اللحظي.
5. `scalp_m5_reversal` — انعكاس M5 عند دعم/مقاومة في بيئة غير اتجاهية.
6. `scalp_reversion` — mean reversion عند تطرف قصير المدى مع تأكيد انعكاس لحظي.

التحليل يطبق أيضاً فلاتر مثل spike في السبريد، doji/sideways، زخم ticks، ATR، ADX/DI، EMA، بنية السعر، وانحياز M15/H1 حسب الاستراتيجية. الحد الأدنى الافتراضي للثقة في الـEngine هو 75% ويمكن تغييره من Telegram بين 50% و95%.

## التنفيذ وإدارة المخاطر

قبل إرسال الصفقة يتحقق المحرك من حساب DEMO وصلاحيات MT5، ومن حداثة ticks والسعر الحي وحد انخفاض Equity اليومي، ثم يحسب الحجم اعتماداً على Equity والمسافة إلى SL. يختار سياسة الملء المتاحة للرمز، ويستخدم `order_check` قبل `order_send`. أوامر البوت تحمل magic number `4009`. الفحص المسبق لا يضمن قبول التنفيذ عند الوسيط.

الإعدادات المهمة:

| الإعداد | الافتراضي / النطاق |
|---|---|
| Risk per trade | 0.25% افتراضياً؛ Telegram يسمح 0.25–50% |
| R:R | 1:3 افتراضياً؛ Telegram يسمح 0.5–10 |
| Minimum confidence | 75% افتراضياً؛ 50–95% |
| Protection trigger | 45% افتراضياً؛ 5–90% |
| Max positions | 1 افتراضياً؛ 1–10 |
| Consecutive-loss limit | 3 افتراضياً؛ 0–20، و0 يعطل الحد |
| Max trade duration | 10 دقائق داخل Engine؛ 3–240 من Telegram |
| Re-entry cooldown | 120 ثانية |
| Spread multiplier | 1.8 × المتوسط المتحرك |
| Slippage/deviation | 10 points |
| Magic number | 4009 |

`Risk` يحتفظ بتاريخ spread مستقل لكل رمز، ويستخدم متوسطاً حديثاً وحداً نسبياً. بعد كل 3 رفضات متتالية بسبب السبريد يوسع الحد 10% بحد أقصى مرتين؛ القراءات المرفوضة لا تدخل في المتوسط. هذا لا يغني عن وضع `MAX_SPREAD_POINTS` مناسب لرموز الوسيط.

حد انخفاض Equity اليومي (`DAILY_LOSS_LIMIT_PCT`) يقارن Equity الحالي بقيمة أول فحص في يوم السيرفر ويحفظ خط الأساس في SQLite حتى بعد إعادة تشغيل البوت. عند بلوغ الحد يتوقف **الدخول الجديد** وتستمر إدارة المراكز المتتبعة. الإيداع والسحب والمراكز المحمولة من يوم سابق قد تؤثر على هذه المقارنة؛ ليست حساباً دقيقاً للخسارة المحققة. `cooldown_after_losses_min` إعداد محجوز غير مطبق.

## متابعة الصفقات

كل مركز يتتبعه `TradeState` مستقل. المحرك يعتمد القيم الفعلية للمركز بعد التنفيذ، ويسجل بيانات الدخول وSL/TP والحجم والاستراتيجية والثقة. توجد آليات حماية/Trailing وإغلاق زمني ضمن الـEngine، مع منع فتح صفقة قديمة غير متتبعة عند بدء المحرك.

إذا ظهر مركز يحمل magic البوت وغير موجود في ذاكرة المحرك، يوقف المحرك الدخول ويطلب فحص المركز يدوياً. عند فشل ربط مركز بعد إرسال أمر ناجح يوقف المحرك بالكامل. إغلاق جزئي عبر زر Stop يُبقي المركز المتبقي مسجلاً في الذاكرة لكن المحرك يكون متوقفاً، لذا افحص المتبقي يدوياً في MT5 ولا تعتمد على رسالة Telegram وحدها.

زر Stop في Telegram يوقف الدخول الجديد ويحاول إغلاق المراكز التي يديرها البوت، مع تسجيل النتيجة.

## Telegram UI

الواجهة عربية ومبنية على `python-telegram-bot`. تشمل القوائم الرئيسية: Dashboard/الحالة، التداول، التحليل، الإعدادات، والحساب.

الوظائف الحالية تشمل:

- تسجيل دخول MT5 من رسالة واحدة تحتوي `Server` و`Login` و`Password`، ثم محاولة حذف رسالة بيانات الدخول.
- عرض حالة الحساب وMT5/Algo readiness.
- اختيار رمز واحد أو عدة رموز، مع قائمة رموز شائعة ومسح سريع للرموز ذات ticks الحديثة.
- تحليل الأزواج المختارة.
- Start / Stop.
- تعديل Risk وConfidence وProtection وR:R وMax Positions وMax Losses ومدة الصفقة.
- `/clean` لتنظيف الرسائل التي تتبعها الواجهة.
- معالجة `RetryAfter` و`BadRequest` لتقليل مشاكل Telegram flood.
- رسائل التداول المصورة يمكن تحديث caption الخاص بها بدلاً من إرسال رسالة جديدة لكل تغير.
- شاشة الحالة تعرض عدد دورات المحرك ومدة آخر دورة؛ يسجل `ENGINE_CYCLE` ملخصاً مرة كل دقيقة تقريباً لتشخيص البطء دون كتابة سجل لكل فحص.

الأوامر المسجلة حالياً تشمل `/start`, `/cancel`, `/clean`, `/symbol`, `/symbols`, `/rr`, `/risk`, `/confidence`, `/protection`, `/maxpos`, `/maxloss`.
`/cancel` يلغي إدخال الإعداد أو بيانات الدخول الجاري فقط؛ لا يحذف credentials ولا يفصل MT5. زر «تحليل الآن» يستخدم نفس أطر M1/M5/M15/H1 المستخدمة في المحرك، ويرفض عرض فرصة من ticks قديمة.

## الاختبار التاريخي

`backtest.py` إعادة تشغيل تاريخية للإشارات، وتستخدم الآن أطر M1/M5/M15/H1 والقيم الافتراضية الأساسية للمحرك. ليست اختبار تنفيذ عند الوسيط: لا تحاكي الرسوم والانزلاق والسيولة والقيود الزمنية أو تزامن المراكز كاملاً. لا تستخدم `Equity index` فيها كتوقع لأداء الحساب.

## قاعدة البيانات

الملف الافتراضي:

```text
storage/bot.db
```

الجداول:

- `settings(key, value)` — إعدادات Telegram/Engine المحفوظة.
- `audit(id, ts, event, symbol, details)` — سجل الأحداث والتنفيذ والأخطاء المهمة.

لا تحذف قاعدة البيانات أثناء deployment إذا كنت تريد الاحتفاظ بالإعدادات والسجل.

## الإعداد عبر .env

`core/config.py` يقرأ `.env` من جذر المشروع. أهم المتغيرات:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_ID=...
APP_MODE=DEMO

MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

DEFAULT_SYMBOL=EURUSD
RISK_PER_TRADE_PCT=0.25
DAILY_LOSS_LIMIT_PCT=2
MAX_CONSECUTIVE_LOSSES=3
COOLDOWN_AFTER_LOSSES_MIN=30
RR=3
MIN_HOLD_SECONDS=5
MAX_HOLD_SECONDS=120
MIN_SL_POINTS=10
MAX_TEST_LOT=0.10
SPREAD_SAMPLE_SIZE=60
MAX_SPREAD_MULTIPLIER=1.8
MAX_SPREAD_POINTS=0
MAX_SLIPPAGE_POINTS=10
MAX_TICK_AGE_SECONDS=15
POLL_INTERVAL_MS=150
```

لا ترفع `.env` أو credentials إلى GitHub.

## المتطلبات

```text
python-telegram-bot==22.5
MetaTrader5>=5.0.45
pydantic-settings>=2.6
python-dotenv>=1.0
aiosqlite>=0.20
numpy>=1.26
pandas>=2.2
```

في Linux/Wine يجب تشغيل **Windows Python داخل نفس Wine prefix الذي يعمل فيه MT5**. لا تعتمد على Python Linux الأصلي لحزمة `MetaTrader5`.

## بيئة الخادم المستخدمة

المسار الحالي للمشروع:

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

واجهة X الحالية تستخدم `DISPLAY=:1` و`XAUTHORITY=/home/ubuntu/.Xauthority`.

## تشغيل يدوي للتطوير

بعد التأكد من أن MT5 وPython IPC يعملان:

```bash
cd "$HOME/.wine/drive_c/mt5bot"

APP_MODE=DEMO WINEDEBUG=-all PYTHONUNBUFFERED=1 /opt/wine-staging/bin/wine "C:\users\ubuntu\AppData\Local\Programs\Python\Python311\python.exe" -u main.py
```

لا تشغّل Terminal إضافياً يدوياً إذا كان MT5 يعمل بالفعل داخل Wine prefix نفسه. كذلك لا تشغّل البوت يدوياً بالتزامن مع `mtbot.service` حتى لا تتكرر جلسة Telegram polling.

## تشغيل البوت عبر systemd

على هذا السيرفر توجد خدمة واحدة للبوت: `mtbot.service`. لا توجد خدمة باسم `mt5.service`. يمكن تشغيل البوت يدوياً بدلاً من الخدمة، لكن لا تشغّل الطريقتين معاً. يعمل MT5 داخل Wine على جلسة VNC/XFCE ذات `DISPLAY=:1`، ويمكن لـ `mt5.initialize()` تشغيل Terminal عند الحاجة؛ تأكد من عدم وجود نسختين من `terminal64.exe` على Wine prefix نفسه.

لفحص خدمة البوت دون عرض المتغيرات السرية:

```bash
systemctl show mtbot.service -p ActiveState -p SubState -p MainPID -p NRestarts
```

قبل تشغيل المحرك من Telegram، تحقق أن الاتصال بـMT5 نجح وأن الحساب التجريبي متصل. حل صاحب السيرفر مشكلة IPC التي ظهرت بعد تحديث Terminal؛ هذا المستند لا يفترض أن العطل لا يزال قائماً.

اختبار API مستقل:

```bash
env HOME=/home/ubuntu DISPLAY=:1 XAUTHORITY=/home/ubuntu/.Xauthority WINEPREFIX=/home/ubuntu/.wine WINEDEBUG=-all /opt/wine-staging/bin/wine 'C:\users\ubuntu\AppData\Local\Programs\Python\Python311\python.exe' -c "
import MetaTrader5 as mt5
ok = mt5.initialize(
    r'C:\Program Files\MetaTrader 5\terminal64.exe',
    timeout=30000,
    portable=True,
)
print('initialize =', ok)
print('last_error =', mt5.last_error())
if ok:
    print(mt5.terminal_info())
    print(mt5.account_info())
    mt5.shutdown()
"
```

لا تعتبر النظام جاهزاً إلا إذا كانت `initialize=True`، ثم تحقق من `terminal_info().connected` و`trade_allowed` و`tradeapi_disabled` وحالة الحساب.

## Algo Trading

`core/mt5_gateway.py` يحتوي حالياً على `_enable_algo_trading()` التي تعدل `Config/common.ini` مع الحفاظ على encoding، وتضع تحت `[Experts]`:

```ini
AllowLiveTrading=1
Enabled=1
Account=0
Profile=0
Api=0
```

هذه الإعدادات **لا تعالج IPC timeout**. في آخر فحص قبل مشكلة IPC كان الحساب نفسه يسمح بالتداول، لكن حالة Terminal Algo تحتاج دائماً للتحقق من `terminal_info()` بعد نجاح الاتصال.

## مشكلة MT5 build 6207 / IPC

في 2026-09-23 قام MT5 LiveUpdate بتحديث Terminal من build 6204 إلى 6207. بعد التحديث:

- Terminal يبدأ ويظهر في process list.
- تم التأكد من وجود Terminal واحد فقط أثناء الاختبار.
- إعادة تشغيل Wine بالكامل لم تحل المشكلة.
- `mt5.initialize(path=..., portable=True)` وبدون `path` أعادا `(-10005, 'IPC timeout')`.
- حزمة Python كانت `MetaTrader5 5.0.6180` على Python 3.11.9.
- استمرت المشكلة تاريخياً حتى عند تجربة بدء Terminal عبر Python نفسه.
- تحذيرات `libEGL/DRI3` ظهرت في إحدى المحاولات؛ لا يوجد في النتائج الحالية ما يثبت أنها سبب IPC.
- هذه نتائج تشخيص تاريخية؛ أبلغ صاحب السيرفر لاحقاً بحل مشكلة IPC.

لذلك عند استكمال التشخيص يجب التركيز على طبقة **MT5 6207 ↔ Wine ↔ MetaTrader5 Python IPC**، وليس تغيير استراتيجية التداول أو Analyzer.

## Deployment من GitHub

الفرع المستخدم هو `main`. على الخادم:

```bash
cd "$HOME/.wine/drive_c/mt5bot" && git fetch origin main && git reset --hard origin/main
```

هذا لا ينبغي أن يحذف `.env` غير المتتبع، لكن تحقق دائماً من الأسرار وقاعدة البيانات قبل أي تنظيف يدوي. لا تستخدم أوامر حذف واسعة داخل مجلد المشروع.

## الأمان

- DEMO فقط في النسخة الحالية.
- لا تضع Telegram token أو كلمة مرور MT5 أو مفاتيح AWS داخل Git.
- اسمح فقط لـ Telegram user ID الموثوق.
- ملف credentials المحلي يجب أن يبقى بصلاحيات ضيقة.
- لا تعرض VNC أو SSH للعالم بشكل دائم؛ قيد Security Group إلى عناوين موثوقة.
- لا تشغل أكثر من MT5 على نفس Wine prefix دون سبب واضح.
- لا تشغل المحرك إذا فشل MT5 IPC أو كانت حالة التداول غير جاهزة.
- راقب `storage/bot.db` وsystemd logs بعد أي deployment.

## ملاحظات للمطور

عند تعديل أي وظيفة، تتبع المسار كاملاً بدلاً من تعديل ملف منفرد فقط:

```text
Telegram UI
  → Engine
    → Analyzer / Risk
      → MT5Gateway
        → MetaTrader5 terminal
  → SQLite settings/audit
```

أي تغيير في تسجيل الدخول أو التشغيل يجب اختباره مع lifecycle الخاص بـWine/MT5. وأي تغيير في استراتيجية أو إدارة صفقة يجب مراجعته مع إدارة المخاطر، تعدد الرموز، المراكز الحالية، الإشعارات، واستعادة الإعدادات بعد restart.

## حدود معروفة / أعمال لاحقة

- توثيق سبب حل IPC الذي أجراه صاحب السيرفر، والتحقق من نجاح الاتصال بعد أي تحديث لاحق.
- التحقق من حد انخفاض Equity اليومي مع الإيداع والسحب والمراكز المحمولة عبر منتصف الليل.
- اختبار سياسات الملء والإغلاق الجزئي لكل رمز مع الوسيط على DEMO.
- اختبار restart/reconciliation للمراكز المفتوحة بشكل أوسع.
- إضافة سياسة أخبار اقتصادية إذا كانت مطلوبة.
- اختبار Demo soak طويل قبل التفكير في Live.

---

**الوضع الحالي للمشروع: DEMO / safety-first. اختبر كل تغيير على حساب تجريبي أولاً.**


## T4Bot Control API

A private native-iOS control surface is being added without moving trading
logic out of Mtbot. The API is opt-in and disabled by default.

Runtime variables:

```dotenv
CONTROL_API_ENABLED=true
CONTROL_API_HOST=127.0.0.1
CONTROL_API_PORT=7099
CONTROL_API_TOKEN=<long-random-secret>
```

Keep `CONTROL_API_HOST=127.0.0.1` and expose it through an HTTPS reverse
proxy or private tunnel. Do not publish port 7099 directly to the Internet.

The API is versioned under `/v1` and uses Bearer authentication. T4Bot reads
state through `/v1/snapshot`, controls the existing Engine through explicit
endpoints, and uses WebSocket events only as refresh hints. The server remains
authoritative for DEMO enforcement, settings validation, strategies, risk,
orders and MT5 credentials.

Telegram can continue running in parallel. Setting
`TELEGRAM_ENABLED=false` disables Telegram while leaving the T4Bot API and
Mtbot engine process available.
