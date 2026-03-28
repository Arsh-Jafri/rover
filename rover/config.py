import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

_config = None


def load_config(config_path: str | None = None) -> dict:
    load_dotenv()

    if config_path is None:
        config_path = os.environ.get("ROVER_CONFIG", "config.yaml")

    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {
            "gmail": {"search_query": "category:purchases newer_than:45d"},
            "anthropic": {"model": "claude-sonnet-4-20250514"},
            "scraping": {
                "max_retries": 3,
                "min_delay": 2,
                "max_delay": 5,
                "rate_limit_per_domain": 10,
                "timeout": 15,
            },
            "notifications": {"enabled": True},
            "claims": {"enabled": True},
            "retailers_yaml": "retailers.yaml",
            "default_refund_window_days": 14,
        }

    config.setdefault("anthropic", {})
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        config["anthropic"]["api_key"] = api_key

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        config.setdefault("notifications", {})["resend_api_key"] = resend_key

    return config


def get_config(config_path: str | None = None) -> dict:
    global _config
    if _config is None:
        _config = load_config(config_path)
    return _config
