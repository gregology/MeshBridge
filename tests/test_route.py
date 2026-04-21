"""Tests for the route/path/traceroute responder plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugins.route import RoutePlugin


@pytest.fixture
def mock_app():
    return AsyncMock()


@pytest.fixture
def route_plugin(mock_app):
    return RoutePlugin(mock_app, {"enabled": True})


def test_plugin_metadata(route_plugin):
    assert route_plugin.plugin_name == "route"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "route",
        "Route",
        "ROUTE",
        " route ",
        "path",
        "Path",
        "PATH",
        " path ",
        "traceroute",
        "Traceroute",
        "TRACEROUTE",
        " traceroute ",
    ],
)
async def test_responds_to_trigger_words(route_plugin, text):
    """All recognized trigger words produce a response."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
        path=["abc123", "def456", "ghi789"],
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.broadcast.assert_awaited_once_with(
        text="abc123 > def456 > ghi789", channel=0, source_plugin="route"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "route me",
        "show path",
        "traceroute please",
        "my route",
    ],
)
async def test_ignores_non_matching_messages(route_plugin, text):
    """Messages that aren't exact trigger words are ignored."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
        path=["abc123"],
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_formats_path_as_arrow_separated(route_plugin):
    """Path hashes are joined with ' > ' separator."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="route",
        channel=0,
        sender_name="TestNode",
        path=["aaa111", "bbb222"],
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.broadcast.assert_awaited_once_with(
        text="aaa111 > bbb222", channel=0, source_plugin="route"
    )


@pytest.mark.asyncio
async def test_no_path_data_when_path_is_none(route_plugin):
    """Replies 'no path data' when path is None."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="path",
        channel=0,
        sender_name="TestNode",
        path=None,
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.broadcast.assert_awaited_once_with(
        text="no path data", channel=0, source_plugin="route"
    )


@pytest.mark.asyncio
async def test_no_path_data_when_path_is_empty(route_plugin):
    """Replies 'no path data' when path is an empty list."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="traceroute",
        channel=0,
        sender_name="TestNode",
        path=[],
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.broadcast.assert_awaited_once_with(
        text="no path data", channel=0, source_plugin="route"
    )


@pytest.mark.asyncio
async def test_dm_reply(route_plugin):
    """DM triggers a direct reply, not a broadcast."""
    event = MeshEvent(
        event_type=EventType.CONTACT_MESSAGE,
        text="route",
        sender_name="TestNode",
        sender_key_prefix="abc123",
        path=["xyz789"],
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.broadcast.assert_not_awaited()
    route_plugin._app.send_direct_to_mesh.assert_awaited_once_with(
        text="xyz789", contact_name="TestNode", source_plugin="route", contact_key="abc123"
    )


@pytest.mark.asyncio
async def test_dm_reply_no_path(route_plugin):
    """DM with no path data gets 'no path data' direct reply."""
    event = MeshEvent(
        event_type=EventType.CONTACT_MESSAGE,
        text="path",
        sender_name="TestNode",
        sender_key_prefix="abc123",
        path=None,
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.send_direct_to_mesh.assert_awaited_once_with(
        text="no path data", contact_name="TestNode", source_plugin="route", contact_key="abc123"
    )


@pytest.mark.asyncio
async def test_ignores_own_messages(route_plugin):
    """Don't respond to our own messages echoed back."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="route",
        channel=0,
        source_plugin="route",
        path=["abc123"],
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_responds_on_correct_channel(route_plugin):
    """Reply is sent on the same channel the query came from."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="route",
        channel=5,
        sender_name="TestNode",
        path=["abc123"],
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.broadcast.assert_awaited_once_with(
        text="abc123", channel=5, source_plugin="route"
    )
