import json
import secrets
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
import time
from core.config import settings

app = FastAPI(title="MT5 Bot API", docs_url=None, redoc_url=None)
engine = None

def attach_engine(e):
    global engine
    engine = e

def auth(authorization: str | None):
    expected = settings.app_api_token
    if not expected:
        raise HTTPException(503, "API token not configured")
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[7:]
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Unauthorized")

class TradingViewSignal(BaseModel):
    secret: str
    id: str
    symbol: str
    side: str
    timestamp: float

class BotSettings(BaseModel):
    symbols: list[str]
    risk_pct: float = Field(ge=0.25, le=50)
    protection_pct: float = Field(ge=1, le=100)
    rr: float = Field(ge=0.5, le=20)
    max_positions: int = Field(ge=1, le=50)
    max_consecutive_losses: int = Field(ge=0, le=50)
    confidence_score: float = Field(default=75, ge=0, le=100)


@app.post("/webhook/tradingview")
async def tradingview(data: TradingViewSignal):
    if engine is None:
        raise HTTPException(503, "Engine unavailable")
    expected=settings.tradingview_webhook_secret
    if not expected:
        raise HTTPException(503, "TradingView secret not configured")
    if not secrets.compare_digest(data.secret,expected):
        raise HTTPException(401, "Unauthorized")
    if not engine.running:
        raise HTTPException(409, "Bot is stopped")
    account=engine.gw.account()
    import MetaTrader5 as mt5
    if not account or account.trade_mode!=mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise HTTPException(403, "DEMO account required")
    ok,reason=await engine.enqueue_tradingview_signal(data.id,data.symbol,data.side,data.timestamp)
    if not ok:
        code=409 if reason=="duplicate" else 400
        raise HTTPException(code,reason)
    return {"ok":True,"status":"queued","id":data.id}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/api/v1/status")
async def status(authorization: str | None = Header(default=None)):
    auth(authorization)
    if engine is None:
        raise HTTPException(503, "Engine unavailable")

    a = engine.gw.account()
    positions = []

    for t in engine.trades.values():
        p = engine.gw.position_by_ticket(t.ticket)
        positions.append({
            "ticket": t.ticket,
            "symbol": t.symbol,
            "side": t.side.value,
            "volume": float(getattr(p, "volume", t.volume) if p else t.volume),
            "entry": t.entry,
            "sl": float(getattr(p, "sl", t.sl) if p else t.sl),
            "tp": float(getattr(p, "tp", t.tp) if p else t.tp),
            "profit": float(getattr(p, "profit", 0) if p else 0),
        })

    return {
        "running": engine.running,
        "mode": "DEMO",
        "mt5_connected": a is not None,
        "login": int(a.login) if a else None,
        "server": str(a.server) if a else None,
        "balance": float(a.balance) if a else None,
        "equity": float(a.equity) if a else None,
        "symbols": engine.symbols,
        "risk_pct": engine.risk_pct,
        "protection_pct": engine.protection_pct,
        "rr": engine.rr,
        "max_positions": engine.max_positions,
        "max_consecutive_losses": engine.max_consecutive_losses,
        "confidence_score": engine.min_confidence,
        "positions": positions,
    }

@app.post("/api/v1/start")
async def start(authorization: str | None = Header(default=None)):
    auth(authorization)
    if engine is None:
        raise HTTPException(503, "Engine unavailable")
    await engine.start()
    return {"ok": True, "running": engine.running}

@app.post("/api/v1/stop")
async def stop(authorization: str | None = Header(default=None)):
    auth(authorization)
    if engine is None:
        raise HTTPException(503, "Engine unavailable")
    await engine.stop()
    return {"ok": True, "running": engine.running}

@app.put("/api/v1/settings")
async def save_settings(data: BotSettings, authorization: str | None = Header(default=None)):
    auth(authorization)
    if engine is None:
        raise HTTPException(503, "Engine unavailable")

    symbols = list(dict.fromkeys(x.strip().upper() for x in data.symbols if x.strip()))
    if not symbols:
        raise HTTPException(400, "At least one symbol required")

    engine.symbols = symbols
    engine.symbol = symbols[0]
    engine.risk_pct = data.risk_pct
    engine.protection_pct = data.protection_pct
    engine.rr = data.rr
    engine.max_positions = data.max_positions
    engine.max_consecutive_losses = data.max_consecutive_losses
    engine.min_confidence = data.confidence_score

    await engine.db.set("symbols", json.dumps(symbols))
    await engine.db.set("risk_pct", data.risk_pct)
    await engine.db.set("protection_pct", data.protection_pct)
    await engine.db.set("rr", data.rr)
    await engine.db.set("max_positions", data.max_positions)
    await engine.db.set("max_consecutive_losses", data.max_consecutive_losses)
    await engine.db.set("min_confidence", data.confidence_score)

    return {"ok": True}
