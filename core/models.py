from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'


class Regime(str, Enum):
    TREND = 'TREND'
    RANGE = 'RANGE'
    BREAKOUT = 'BREAKOUT'
    VOLATILE = 'VOLATILE'
    NO_TRADE = 'NO_TRADE'


class Grade(str, Enum):
    A = 'A'
    B = 'B'
    C = 'C'


@dataclass
class Signal:
    side: Side
    strategy: str
    confidence: float
    sl_points: float
    reason: str
    sl_price: float = 0.0
    tp_price: float = 0.0
    protection_pct: float = 0.0
    trailing_gap_pct: float = 0.0
    expected_duration_minutes: float = 0.0
    grade: str = ''

    def __post_init__(self):
        try:
            conf = float(self.confidence)
        except (TypeError, ValueError):
            raise ValueError('confidence must be numeric')
        if not 0.0 <= conf <= 1.0:
            raise ValueError(f'confidence must be 0..1, got {conf}')
        try:
            slp = float(self.sl_points)
        except (TypeError, ValueError):
            raise ValueError('sl_points must be numeric')
        if slp <= 0:
            raise ValueError(f'sl_points must be > 0, got {slp}')
        if not self.strategy or not str(self.strategy).strip():
            raise ValueError('strategy must be non-empty')


@dataclass
class TradeState:
    ticket: int
    symbol: str
    side: Side
    entry: float
    sl: float
    tp: float
    initial_r: float
    opened_at: float

    be_done: bool = False
    lock_done: bool = False
    trailing: bool = False
    trailing_moved: bool = False

    strategy: str = ''
    regime: str = ''
    confidence: float = 0.0
    reason: str = ''
    volume: float = 0.0
    protection_pct: float = 45.0
    trailing_gap_pct: float = 5.0
    expected_duration_minutes: float = 10.0
    grade: str = ''

    protection_45_active: bool = False
    best_favorable_price: float = 0.0
    last_progress_at: float = 0.0
    signal_bar: int = 0
    last_management_tick_msc: int = 0
