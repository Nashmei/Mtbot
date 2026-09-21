# MT5 Telegram Scalper V1

Safety-first Python/MT5 scalping controller with Telegram UI. **DEMO-only execution in V1**: the Telegram Live button is deliberately locked. Validate on a broker demo account before implementing live unlock.

## Features
- One selected broker symbol at a time.
- Tick-based market regime selector: breakout, momentum/trend, mean-reversion, or no-trade.
- Target holding window up to 120 seconds (SL/TP can close earlier).
- Risk-based lot sizing (default 0.25% equity), R:R configurable.
- Spread guard: rolling spread average + relative threshold + optional absolute cap.
- MT5 `deviation` slippage limit and post-fill slippage audit.
- Profit protection: +1R break-even; +1.5R protect 25% of current favorable move; then dynamic trailing; 120s time exit.
- Telegram event notifications only; trailing modifications do not spam Telegram.
- SQLite audit trail.
- Telegram User-ID allowlist.

> Important: a market order can be filled before post-fill slippage is known. The bot passes an allowed `deviation` to MT5 and audits actual slippage; it cannot truthfully “undo” an already completed fill. Broker execution rules still apply.

## Telegram setup (phone)
1. Open Telegram and chat with **@BotFather**.
2. Send `/newbot`, choose a display name and a unique username ending in `bot`.
3. BotFather gives you a token. Keep it secret; paste it only into `.env` as `TELEGRAM_BOT_TOKEN`.
4. Find your numeric Telegram user ID using a trusted ID-info bot or Telegram API method, and put it in `TELEGRAM_ALLOWED_USER_ID`. Do not use your username here.
5. Open your new bot and tap **Start**.

## AWS Ubuntu reality: MT5 + Python
MetaTrader 5 is a Windows desktop terminal. MetaQuotes supports running MT5 on Linux through Wine. The official Python integration communicates directly with the running terminal. In practice, on Ubuntu the most compatible arrangement is to run **MT5 and Windows Python inside the same Wine prefix**. Native Linux Python should not be assumed to work with the `MetaTrader5` package.

### Recommended AWS instance
Use Ubuntu 22.04/24.04 x86_64, at least 2 vCPU / 4 GB RAM. Keep the instance close to the broker's trade server when possible. Do **not** expose Telegram or MT5 ports in the Security Group; the Telegram bot uses outbound polling. SSH (22) should be restricted to your IP.

### 1. Base packages
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y wine64 winbind xvfb unzip wget cabextract
```

### 2. Install MT5 under Wine
Follow MetaQuotes' current Linux/Wine installer instructions. Log into your **DEMO** account in the MT5 terminal first and enable algorithmic trading. In MT5 settings, ensure external Python trading is not disabled.

For a headless EC2 host, keep an X virtual framebuffer running:
```bash
Xvfb :99 -screen 0 1280x800x24 &
export DISPLAY=:99
wine "C:\\Program Files\\MetaTrader 5\\terminal64.exe" &
```
Exact Wine paths can vary. Verify the terminal opens and logs into the demo account before continuing.

### 3. Install Windows Python in Wine
Download a supported 64-bit Windows Python installer from python.org, then:
```bash
export DISPLAY=:99
wine python-installer.exe
```
During setup select **Add Python to PATH**. Open Wine cmd and verify:
```bash
wine cmd
python --version
pip --version
```

### 4. Upload this project
Copy the project to the server, then make it visible to Wine. An easy location is:
```bash
mkdir -p ~/.wine/drive_c/mt5bot
cp -r mt5_telegram_scalper/* ~/.wine/drive_c/mt5bot/
cp mt5_telegram_scalper/.env.example ~/.wine/drive_c/mt5bot/.env
```
Edit the secret configuration:
```bash
nano ~/.wine/drive_c/mt5bot/.env
```
Use the exact broker server name shown by MT5 and a **demo** login/password.

### 5. Install Python dependencies inside Wine
```bash
export DISPLAY=:99
wine cmd /c "cd C:\\mt5bot && python -m pip install --upgrade pip && pip install -r requirements.txt"
```

### 6. First test
```bash
export DISPLAY=:99
wine cmd /c "cd C:\\mt5bot && python main.py"
```
You should see `MT5 connected`. Open Telegram, `/start`, choose `/symbol EURUSD` using the exact symbol name from your broker, then **Analyze**. Only after checking the values should you press Start.

## Configuration defaults
- `RISK_PER_TRADE_PCT=0.25`
- `RR=3`
- `MAX_HOLD_SECONDS=120`
- `MAX_SPREAD_MULTIPLIER=1.8` relative to rolling average
- `MAX_SPREAD_POINTS=0` means no absolute cap; set a broker/symbol-specific cap after observing demo data.
- `MAX_SLIPPAGE_POINTS=10`

## Risk controls still recommended before Live
V1 deliberately leaves Live locked. Before implementing Live, add/validate: daily-loss lockout, consecutive-loss cooldown, persisted trade reconciliation after restart, broker-specific filling-mode negotiation, economic-news policy, minimum stop/freeze levels, and a demo soak test. The configuration already reserves daily-loss/consecutive-loss defaults, but **they are not represented as completed enforcement in this V1**.

## Telegram commands
- `/start` dashboard
- `/symbol EURUSD`
- `/rr 3`
Buttons: Status, Analyze, Start, Stop, Symbol, R:R, Live (locked).

## Audit
`storage/bot.db` contains the `audit` table. Spread rejects, order-check rejects, execution rejects, lifecycle events and engine errors are recorded there.

## Security
Never paste `.env`, Telegram token, MT5 password, or AWS private key into Telegram. Rotate a Telegram token immediately in BotFather if exposed. Restrict SSH by source IP. Demo is the only enabled trading environment in this build.
