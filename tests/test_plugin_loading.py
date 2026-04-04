"""Tests for plugin discovery and loading."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from meshbridge.plugin import BasePlugin
from meshbridge.plugins import _PLUGIN_REGISTRY, load_plugins, register_plugin


@pytest.fixture
def mock_app():
    return AsyncMock()


class _FakePlugin(BasePlugin):
    plugin_name = "fake"
    plugin_version = "0.0.1"

    async def start(self): ...
    async def stop(self): ...
    async def on_mesh_event(self, event): ...


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot and restore the registry so test-registered plugins don't leak."""
    saved = dict(_PLUGIN_REGISTRY)
    yield
    _PLUGIN_REGISTRY.clear()
    _PLUGIN_REGISTRY.update(saved)


def test_register_plugin_adds_to_registry():
    """@register_plugin adds the class to the global registry."""
    register_plugin(_FakePlugin)
    assert _PLUGIN_REGISTRY["fake"] is _FakePlugin


def test_load_enabled_plugin(mock_app):
    """An enabled plugin in config is instantiated and returned."""
    register_plugin(_FakePlugin)
    config = {"plugins": {"fake": {"enabled": True}}}
    loaded = load_plugins(mock_app, config)
    assert len(loaded) == 1
    assert isinstance(loaded[0], _FakePlugin)


def test_load_disabled_plugin(mock_app):
    """A disabled plugin is skipped."""
    register_plugin(_FakePlugin)
    config = {"plugins": {"fake": {"enabled": False}}}
    loaded = load_plugins(mock_app, config)
    assert len(loaded) == 0


def test_load_default_disabled(mock_app):
    """Plugins without explicit enabled key default to disabled."""
    register_plugin(_FakePlugin)
    config = {"plugins": {"fake": {}}}
    loaded = load_plugins(mock_app, config)
    assert len(loaded) == 0


def test_load_unknown_plugin(mock_app):
    """An unknown plugin name in config is skipped without crashing."""
    config = {"plugins": {"nonexistent": {"enabled": True}}}
    loaded = load_plugins(mock_app, config)
    assert len(loaded) == 0


def test_load_skips_non_dict_config(mock_app):
    """Non-dict plugin config values (e.g. a string) are skipped."""
    register_plugin(_FakePlugin)
    config = {"plugins": {"fake": "not a dict"}}
    loaded = load_plugins(mock_app, config)
    assert len(loaded) == 0


def test_load_survives_plugin_init_error(mock_app):
    """If a plugin's __init__ raises, loading continues with other plugins."""

    class _BadPlugin(BasePlugin):
        plugin_name = "bad"
        async def start(self): ...
        async def stop(self): ...
        async def on_mesh_event(self, event): ...
        def __init__(self, app, config):
            raise RuntimeError("init failed")

    register_plugin(_BadPlugin)
    register_plugin(_FakePlugin)
    config = {
        "plugins": {
            "bad": {"enabled": True},
            "fake": {"enabled": True},
        }
    }
    loaded = load_plugins(mock_app, config)
    assert len(loaded) == 1
    assert loaded[0].plugin_name == "fake"


def test_load_no_plugins_section(mock_app):
    """Config with no plugins section returns empty list."""
    loaded = load_plugins(mock_app, {})
    assert loaded == []


def test_builtin_discovery_registers_discord_and_ping(mock_app):
    """After load_plugins runs, discord and ping are in the registry."""
    load_plugins(mock_app, {})
    assert "discord" in _PLUGIN_REGISTRY
    assert "ping" in _PLUGIN_REGISTRY
