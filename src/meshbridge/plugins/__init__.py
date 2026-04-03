"""Plugin discovery and registration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from meshbridge.plugin import BasePlugin

if TYPE_CHECKING:
    from meshbridge.app import App

logger = logging.getLogger(__name__)

_PLUGIN_REGISTRY: dict[str, type[BasePlugin]] = {}


def register_plugin(cls: type[BasePlugin]) -> type[BasePlugin]:
    """Decorator to register a plugin class in the global registry."""
    _PLUGIN_REGISTRY[cls.plugin_name] = cls
    return cls


def _discover_builtin_plugins() -> None:
    """Import built-in plugin modules to trigger registration."""
    from meshbridge.plugins import discord as _  # noqa: F401
    from meshbridge.plugins import ping as _p  # noqa: F401


def load_plugins(app: App, config: dict) -> list[BasePlugin]:
    """Instantiate all enabled plugins based on config."""
    _discover_builtin_plugins()

    plugins_config = config.get("plugins", {})
    loaded: list[BasePlugin] = []

    for plugin_name, plugin_cfg in plugins_config.items():
        if not isinstance(plugin_cfg, dict):
            continue
        if not plugin_cfg.get("enabled", False):
            logger.debug("Plugin %s is disabled, skipping", plugin_name)
            continue

        cls = _PLUGIN_REGISTRY.get(plugin_name)
        if cls is None:
            logger.warning("Unknown plugin: %s (not in registry)", plugin_name)
            continue

        try:
            instance = cls(app, plugin_cfg)
            loaded.append(instance)
            logger.info("Loaded plugin: %s v%s", cls.plugin_name, cls.plugin_version)
        except Exception:
            logger.exception("Failed to instantiate plugin: %s", plugin_name)

    return loaded
