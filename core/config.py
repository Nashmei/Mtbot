from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Bootstrap/runtime settings only.

    Trading preferences are account-scoped in storage/bot.db and are changed
    through an authenticated control surface.
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
    max_spread_multiplier: float = 2.5
    max_spread_points: float = 0.0
    max_slippage_points: int = 10
    slippage_atr_fraction: float = 0.15
    slippage_max_hard_cap: int = 80
    min_sl_points: float = 10.0
    poll_interval_ms: int = 150
    max_tick_age_seconds: float = 15.0

    # Optional AI cross-check (veto-only, disabled by default).
    ai_crosscheck_enabled: bool = False

    execution_notice_max_entries: int = 2000
    reject_log_cache_size: int = 5000
    chart_send_enabled: bool = True
    chart_send_timeout_seconds: float = 12.0
    tick_window_max: int = 3000

    db_path: str = str(BASE_DIR / 'storage' / 'bot.db')
    db_backup_enabled: bool = True
    db_backup_interval_hours: int = 24
    db_backup_keep: int = 7
    db_backup_dir: str = str(BASE_DIR / 'storage' / 'backups')

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / '.env'),
        case_sensitive=False,
        extra='ignore',
    )


settings = Settings()
