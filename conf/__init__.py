"""Configuration layer — canonical home for config, settings, and scrubbing."""

from .config import config, IS_WINDOWS, AppConfig, create_directories, validate_config
from .settings import get_setting, save_settings, load_settings, is_setting_overridden, get_user_setting
