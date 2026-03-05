"""
Load credentials from credentials.env and settings from config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from loguru import logger


def load_credentials(env_path: str | Path | None = None) -> dict[str, str]:
    """Load PKU_USERNAME and PKU_PASSWORD from credentials.env."""
    if env_path is None:
        env_path = Path(__file__).parent.parent / "credentials.env"

    env_path = Path(env_path)
    if not env_path.exists():
        raise FileNotFoundError(
            f"credentials.env not found at {env_path}. "
            "Copy credentials.env.example to credentials.env and fill in your details."
        )

    load_dotenv(dotenv_path=env_path, override=True)

    username = os.environ.get("PKU_USERNAME", "").strip()
    password = os.environ.get("PKU_PASSWORD", "").strip()

    if not username or not password:
        raise ValueError(
            "PKU_USERNAME or PKU_PASSWORD is empty in credentials.env."
        )

    # 超级鹰（可选）
    cjy_user = os.environ.get("CHAOJIYING_USERNAME", "").strip()
    cjy_pass = os.environ.get("CHAOJIYING_PASSWORD", "").strip()
    cjy_soft = os.environ.get("CHAOJIYING_SOFTID", "").strip()
    cjy_creds = (
        {"username": cjy_user, "password": cjy_pass, "softid": cjy_soft}
        if cjy_user and cjy_pass and cjy_soft
        else None
    )

    logger.debug(f"Credentials loaded for user: {username}"
                 + (" (超级鹰 enabled)" if cjy_creds else ""))
    return {"username": username, "password": password, "cjy_creds": cjy_creds}


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load config.yaml and return the parsed dict."""
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"config.yaml not found at {config_path}.")

    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logger.debug(f"Config loaded from {config_path}")
    return config


def get_enabled_venues(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only venues with enabled=True."""
    return [v for v in config.get("venues", []) if v.get("enabled", False)]
