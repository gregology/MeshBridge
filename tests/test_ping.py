"""Tests for the ping/pong responder plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugins.ping import PingPlugin


@pytest.fixture
def mock_app():
    return AsyncMock()


@pytest.fixture
def ping_plugin(mock_app):
    return PingPlugin(mock_app, {"enabled": True})


def test_plugin_metadata(ping_plugin):
    assert ping_plugin.plugin_name == "ping"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "ping",
        "Ping",
        "PING",
        " ping ",
        "radio check",
        "Radio Check",
        "RADIO CHECK",
        "radio check?",
        "radiocheck",
    ],
)
async def test_responds_to_ping_variants(ping_plugin, text):
    """All recognized ping phrases trigger a pong."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await ping_plugin.on_mesh_event(event)
    ping_plugin._app.broadcast.assert_awaited_once_with(
        text="pong", channel=0, source_plugin="ping"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "ping me",
        "not a ping",
        "please ping",
        "radio check ok",
    ],
)
async def test_ignores_non_ping_messages(ping_plugin, text):
    """Messages that aren't ping variants are ignored."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await ping_plugin.on_mesh_event(event)
    ping_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_responds_to_dm_ping(ping_plugin):
    """Direct messages with 'ping' get a direct pong reply."""
    event = MeshEvent(
        event_type=EventType.CONTACT_MESSAGE,
        text="ping",
        sender_name="TestNode",
    )
    await ping_plugin.on_mesh_event(event)
    ping_plugin._app.broadcast.assert_not_awaited()
    ping_plugin._app.send_direct_to_mesh.assert_awaited_once_with(
        text="pong", contact_name="TestNode", source_plugin="ping"
    )


@pytest.mark.asyncio
async def test_ignores_own_messages(ping_plugin):
    """Don't respond to our own pong echoed back."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="ping",
        channel=0,
        source_plugin="ping",
    )
    await ping_plugin.on_mesh_event(event)
    ping_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_responds_on_correct_channel(ping_plugin):
    """Pong is sent back on the same channel the ping came from."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="ping",
        channel=3,
        sender_name="TestNode",
    )
    await ping_plugin.on_mesh_event(event)
    ping_plugin._app.broadcast.assert_awaited_once_with(
        text="pong", channel=3, source_plugin="ping"
    )


@pytest.mark.asyncio
async def test_responds_to_discord_ping(ping_plugin):
    """Ping from Discord triggers a broadcast pong."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        source="discord",
        text="ping",
        channel=0,
        sender_name="DiscordUser",
        source_plugin="discord",
    )
    await ping_plugin.on_mesh_event(event)
    ping_plugin._app.broadcast.assert_awaited_once_with(
        text="pong", channel=0, source_plugin="ping"
    )
