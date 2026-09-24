from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Bootstrap/runtime settings only.

    Trading preferences are account-scoped in storage/bot.db and are changed
    through an authenticated control surface. They intentionally do not come
    from .env.
    """

    telegram_enabled: bool = True
    telegram_bot_token: str | None = None
    telegram_allowed_user_id: int | None = None

    control_api_enabled: bool = False
    control_api_host: str = '127.0.0.1'
    control_api_port: int = 7099
    control_api_token: str | None = None

    apns_enabled: bool = False
    apns_key_id: str | None = None
    apns_team_id: str | None = None
    apns_auth_key_path: str | None = None
    apns_bundle_id: str = 'com.nashmei.t4bot'
    apns_environment: str = 'production'

    mt5_terminal_path: str | None = None
    app_mode: str = 'DEMO'
    spread_sample_size: int = 60
    max_spread_multiplier: float = 1.8
    max_spread_points: float = 0.0
    max_slippage_points: int = 10
    min_sl_points: float = 10.0
    poll_interval_ms: int = 150
    max_tick_age_seconds: float = 15.0
    db_path: str = str(BASE_DIR/'storage'/'bot.db')

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR/'.env'),
        case_sensitive=False,
        extra='ignore',
    )


settings = Settings()
