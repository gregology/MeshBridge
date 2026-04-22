"""Tests for the route/path/traceroute responder plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugins.route import RoutePlugin, _format_reply


@pytest.fixture
def mock_app():
    app = AsyncMock()
    app.request_trace = AsyncMock(
        return_value={"path_text": "ab > cd > ef", "hops": 3, "contact_name": "TestNode"}
    )
    return app


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
        " path ",
        "traceroute",
        "Traceroute",
        " traceroute ",
    ],
)
async def test_responds_to_trigger_words(route_plugin, text):
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.request_trace.assert_awaited_once_with(
        "TestNode", timeout=30.0, inbound_path_len=None
    )
    route_plugin._app.broadcast.assert_awaited_once_with(
        text="ab > cd > ef", channel=0, source_plugin="route"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["hello", "route me", "show path", "traceroute please", "my route"],
)
async def test_ignores_non_matching_messages(route_plugin, text):
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.request_trace.assert_not_awaited()
    route_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_reply_uses_sender_name(route_plugin):
    """Channel queries use sender_name (no pubkey in payload) for the trace."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="route",
        channel=5,
        sender_name="TestNode",
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.request_trace.assert_awaited_once_with(
        "TestNode", timeout=30.0, inbound_path_len=None
    )
    route_plugin._app.broadcast.assert_awaited_once_with(
        text="ab > cd > ef", channel=5, source_plugin="route"
    )


@pytest.mark.asyncio
async def test_dm_reply_uses_sender_key(route_plugin):
    """DM queries prefer sender_key_prefix for the trace."""
    event = MeshEvent(
        event_type=EventType.CONTACT_MESSAGE,
        text="route",
        sender_name="TestNode",
        sender_key_prefix="abcdef123456",
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.request_trace.assert_awaited_once_with(
        "abcdef123456", timeout=30.0, inbound_path_len=None
    )
    route_plugin._app.broadcast.assert_not_awaited()
    route_plugin._app.send_direct_to_mesh.assert_awaited_once_with(
        text="ab > cd > ef",
        contact_name="TestNode",
        source_plugin="route",
        contact_key="abcdef123456",
    )


@pytest.mark.asyncio
async def test_reply_when_trace_fails(mock_app):
    """Error result is surfaced to the user."""
    mock_app.request_trace = AsyncMock(return_value={"error": "trace timed out"})
    plugin = RoutePlugin(mock_app, {"enabled": True})
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="path",
        channel=0,
        sender_name="TestNode",
    )
    await plugin.on_mesh_event(event)
    plugin._app.broadcast.assert_awaited_once_with(
        text="trace failed: trace timed out", channel=0, source_plugin="route"
    )


@pytest.mark.asyncio
async def test_direct_0_hop_result(mock_app):
    mock_app.request_trace = AsyncMock(
        return_value={"path_text": "direct (0 hops)", "hops": 0, "contact_name": "X"}
    )
    plugin = RoutePlugin(mock_app, {"enabled": True})
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="route",
        channel=0,
        sender_name="X",
    )
    await plugin.on_mesh_event(event)
    plugin._app.broadcast.assert_awaited_once_with(
        text="direct (0 hops)", channel=0, source_plugin="route"
    )


@pytest.mark.asyncio
async def test_ignores_own_messages(route_plugin):
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="route",
        channel=0,
        source_plugin="route",
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.request_trace.assert_not_awaited()
    route_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_ignores_when_no_sender_identity(route_plugin):
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="route",
        channel=0,
    )
    await route_plugin.on_mesh_event(event)
    route_plugin._app.request_trace.assert_not_awaited()
    route_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_timeout_from_config(mock_app):
    plugin = RoutePlugin(mock_app, {"enabled": True, "timeout": 5})
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="route",
        channel=0,
        sender_name="TestNode",
    )
    await plugin.on_mesh_event(event)
    plugin._app.request_trace.assert_awaited_once_with(
        "TestNode", timeout=5.0, inbound_path_len=None
    )


def test_format_reply_error():
    assert _format_reply({"error": "x"}) == "trace failed: x"


def test_format_reply_empty():
    assert _format_reply({}) == "trace failed"


def test_format_reply_success():
    assert _format_reply({"path_text": "ab > cd"}) == "ab > cd"
