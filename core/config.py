from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
BASE_DIR = Path(__file__).resolve().parents[1]
class Settings(BaseSettings):
    telegram_bot_token:str; telegram_allowed_user_id:int
    mt5_login:int|None=None; mt5_password:str=''; mt5_server:str=''; mt5_terminal_path:str|None=None
    app_mode:str='DEMO'; live_unlock_phrase:str='ENABLE LIVE'; default_symbol:str='EURUSD'
    risk_per_trade_pct:float=.25; daily_loss_limit_pct:float=2.; max_consecutive_losses:int=3; cooldown_after_losses_min:int=30
    rr:float=3.; min_hold_seconds:int=5; max_hold_seconds:int=120
    min_sl_points:float=10.; max_test_lot:float=.10
    spread_sample_size:int=60; max_spread_multiplier:float=1.8; max_spread_points:float=0.; max_slippage_points:int=10; poll_interval_ms:int=150
    max_tick_age_seconds:float=15.
    db_path:str=str(BASE_DIR/'storage'/'bot.db')
    model_config=SettingsConfigDict(env_file=str(BASE_DIR/'.env'), case_sensitive=False, extra='ignore')
settings=Settings()
