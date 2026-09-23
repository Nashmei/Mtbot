from dataclasses import dataclass
from enum import Enum

class Side(str,Enum):
    BUY='BUY'
    SELL='SELL'

class Regime(str,Enum):
    TREND='TREND'
    RANGE='RANGE'
    BREAKOUT='BREAKOUT'
    VOLATILE='VOLATILE'
    NO_TRADE='NO_TRADE'

@dataclass
class Signal:
    side:Side
    strategy:str
    confidence:float
    sl_points:float
    reason:str

@dataclass
class TradeState:
    ticket:int
    symbol:str
    side:Side
    entry:float
    sl:float
    tp:float
    initial_r:float
    opened_at:float

    be_done:bool=False
    lock_done:bool=False
    trailing:bool=False

    # معلومات اللوحة العربية الحية
    strategy:str=''
    regime:str=''
    confidence:float=0.0
    reason:str=''
    volume:float=0.0
    protection_pct:float=45.0
    trailing_gap_pct:float=5.0

    # نظام الحماية + خمول 60 ثانية
    protection_45_active:bool=False
    best_favorable_price:float=0.0
    last_progress_at:float=0.0
    signal_bar:int=0
