"""Configuration loading and validation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATHS = [
    Path("./config.yaml"),
    Path.home() / ".config" / "meshbridge" / "config.yaml",
    Path("/etc/meshbridge/config.yaml"),
]

REQUIRED_KEYS: dict[str, list[str]] = {
    "device": ["serial_port"],
    "mqtt": ["broker"],
}


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load and validate configuration from a YAML file."""
    config_path = _resolve_path(path)
    logger.info("Loading config from %s", config_path)
    with open(config_path) as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(config).__name__}")
    _validate(config)
    return config


def _resolve_path(path: str | None) -> Path:
    """Find the config file, checking explicit path then default locations."""
    if path:
        p = Path(path)
        if p.exists():
            return p
        raise FileNotFoundError(f"Config file not found: {path}")
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    searched = [str(p) for p in DEFAULT_CONFIG_PATHS]
    raise FileNotFoundError(
        f"No config file found. Searched: {searched}. Run 'meshbridge setup' to create one."
    )


def _validate(config: dict[str, Any]) -> None:
    """Validate that required config sections and keys are present."""
    for section, keys in REQUIRED_KEYS.items():
        if section not in config:
            raise ValueError(f"Missing required config section: '{section}'")
        for key in keys:
            if key not in config[section]:
                raise ValueError(f"Missing required config key: '{section}.{key}'")
