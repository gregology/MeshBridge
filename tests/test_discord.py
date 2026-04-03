"""Tests for the Discord plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugins.discord import DiscordPlugin


@pytest.fixture
def discord_config() -> dict:
    return {
        "enabled": True,
        "webhook_url": "https://discord.com/api/webhooks/test/token",
        "bot_username": "TestBridge",
        "include_metadata": True,
        "channels": [0],
        "event_types": ["CHANNEL_MESSAGE", "CONTACT_MESSAGE"],
    }


@pytest.fixture
def mock_app():
    app = AsyncMock()
    return app


@pytest.fixture
def discord_plugin(mock_app, discord_config):
    return DiscordPlugin(mock_app, discord_config)


def test_plugin_metadata(discord_plugin):
    """Plugin has correct name and version."""
    assert discord_plugin.plugin_name == "discord"
    assert discord_plugin.plugin_version == "0.1.0"


@pytest.mark.asyncio
async def test_filters_by_event_type(discord_plugin):
    """Events not in event_types list are ignored."""
    discord_plugin._session = AsyncMock()
    event = MeshEvent(event_type=EventType.TELEMETRY, node_name="Node1")
    await discord_plugin.on_mesh_event(event)
    # _post_webhook should not be called
    discord_plugin._session.post.assert_not_called()


@pytest.mark.asyncio
async def test_filters_by_channel(discord_plugin):
    """Events on non-configured channels are ignored."""
    discord_plugin._session = AsyncMock()
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="test",
        channel=5,
        sender_name="Node1",
    )
    await discord_plugin.on_mesh_event(event)
    discord_plugin._session.post.assert_not_called()


@pytest.mark.asyncio
async def test_skips_own_events(discord_plugin):
    """Events originating from the discord plugin are not echoed back."""
    discord_plugin._session = AsyncMock()
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="test",
        channel=0,
        source_plugin="discord",
    )
    await discord_plugin.on_mesh_event(event)
    discord_plugin._session.post.assert_not_called()
