# TradingView integration

TradingView will be the signal source. The bot remains responsible for DEMO-only MT5 execution, Telegram-configured risk, R:R, spread/slippage checks, position limits, loss limits, protection and trailing.

Webhook security requirements:
- Shared secret stored only in `.env`.
- Reject stale signals and duplicate signal IDs.
- Accept only BUY/SELL and configured symbols.
- Never bypass the MT5 DEMO account lock.

The existing internal analyzer remains available as a fallback until webhook execution is tested on DEMO.
