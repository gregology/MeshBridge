"""Tests for the Home Assistant plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from meshbridge.events import EventType, MeshEvent
from meshbridge.plugins.homeassistant import HomeAssistantPlugin


@pytest.fixture
def mock_app():
    return AsyncMock()


@pytest.fixture
def ha_config():
    return {
        "enabled": True,
        "url": "http://ha.local:8123",
        "token": "test-token",
        "commands": [
            {
                "pattern": "(?i)^\\s*(weather|forecast)\\s*$",
                "entities": {"weather": "weather.home"},
                "response": "{weather[state]} — {weather[attributes][temperature]}°{weather[attributes][temperature_unit]}",
            },
            {
                "pattern": "(?i)^\\s*(temp|temperature)\\s*$",
                "entities": {"temp": "sensor.outdoor_temperature"},
                "response": "Temp: {temp[state]}°F",
            },
            {
                "pattern": "(?i)^\\s*humidity\\s*$",
                "entities": {"humidity": "sensor.outdoor_humidity"},
                "response": "Humidity: {humidity[state]}%",
            },
        ],
    }


@pytest.fixture
def ha_plugin(mock_app, ha_config):
    return HomeAssistantPlugin(mock_app, ha_config)


def _mock_session(state_data, status=200):
    """Create a mock aiohttp session that returns state_data from GET.

    ``state_data`` can be a single dict (returned for every GET) or a list
    of dicts (returned in order for successive GETs).
    """
    session = MagicMock()

    if isinstance(state_data, list):
        contexts = []
        for data in state_data:
            resp = AsyncMock()
            resp.status = status
            resp.json = AsyncMock(return_value=data)
            ctx = AsyncMock()
            ctx.__aenter__.return_value = resp
            contexts.append(ctx)
        session.get.side_effect = contexts
    else:
        mock_resp = AsyncMock()
        mock_resp.status = status
        mock_resp.json = AsyncMock(return_value=state_data)
        ctx = AsyncMock()
        ctx.__aenter__.return_value = mock_resp
        session.get.return_value = ctx

    session.close = AsyncMock()
    return session


# -- Metadata --

def test_plugin_metadata(ha_plugin):
    assert ha_plugin.plugin_name == "homeassistant"


def test_commands_compiled(ha_plugin):
    assert len(ha_plugin._commands) == 3
    assert ha_plugin._commands[0].entities == {"weather": "weather.home"}


# -- Pattern matching --

@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["weather", "Weather", "WEATHER", " weather ", "forecast"])
async def test_matches_weather(ha_plugin, text):
    """Weather/forecast triggers the weather command."""
    ha_plugin._session = _mock_session({
        "state": "sunny",
        "attributes": {"temperature": 72, "temperature_unit": "F"},
        "entity_id": "weather.home",
    })

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_awaited_once_with(
        text="sunny — 72°F", channel=0, source_plugin="homeassistant"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["temp", "temperature", "Temperature", " TEMP "])
async def test_matches_temp(ha_plugin, text):
    """Temp/temperature triggers the temperature command."""
    ha_plugin._session = _mock_session({
        "state": "68",
        "attributes": {},
        "entity_id": "sensor.outdoor_temperature",
    })

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_awaited_once_with(
        text="Temp: 68°F", channel=0, source_plugin="homeassistant"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["hello", "what is the weather today", "temp check", "check temperature please"],
)
async def test_ignores_non_matching_messages(ha_plugin, text):
    """Messages that don't match any command pattern are ignored."""
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_responds_to_dm_with_direct_reply(ha_plugin):
    """DM with a matching command gets a direct reply, not a broadcast."""
    ha_plugin._session = _mock_session({
        "state": "sunny",
        "attributes": {"temperature": 72, "temperature_unit": "F"},
        "entity_id": "weather.home",
    })
    event = MeshEvent(
        event_type=EventType.CONTACT_MESSAGE,
        text="weather",
        sender_name="TestNode",
        sender_key_prefix="abc123",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()
    ha_plugin._app.send_direct_to_mesh.assert_awaited_once_with(
        text="sunny — 72°F", contact_name="TestNode", source_plugin="homeassistant",
        contact_key="abc123",
    )


@pytest.mark.asyncio
async def test_ignores_own_messages(ha_plugin):
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="weather",
        channel=0,
        source_plugin="homeassistant",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_ignores_empty_text(ha_plugin):
    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=None,
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


# -- First match wins --

@pytest.mark.asyncio
async def test_first_match_wins(mock_app):
    """When multiple patterns could match, only the first triggers."""
    config = {
        "enabled": True,
        "url": "http://ha.local:8123",
        "token": "t",
        "commands": [
            {"pattern": "(?i)temp", "entities": {"t": "sensor.first"}, "response": "first: {t[state]}"},
            {"pattern": "(?i)temperature", "entities": {"t": "sensor.second"}, "response": "second: {t[state]}"},
        ],
    }
    plugin = HomeAssistantPlugin(mock_app, config)
    plugin._session = _mock_session({"state": "70", "attributes": {}})

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="temperature",
        channel=0,
        sender_name="TestNode",
    )
    await plugin.on_mesh_event(event)
    mock_app.broadcast.assert_awaited_once_with(
        text="first: 70", channel=0, source_plugin="homeassistant"
    )


# -- Response formatting --

def test_format_simple():
    result = HomeAssistantPlugin._format_response(
        "{t[state]}", {"t": {"state": "sunny", "attributes": {}}}
    )
    assert result == "sunny"


def test_format_nested_attributes():
    context = {"w": {"state": "sunny", "attributes": {"temperature": 72, "temperature_unit": "F"}}}
    result = HomeAssistantPlugin._format_response(
        "{w[state]} — {w[attributes][temperature]}°{w[attributes][temperature_unit]}", context
    )
    assert result == "sunny — 72°F"


def test_format_numeric_rounding():
    context = {"pm25": {"state": "8.60000038146973", "attributes": {"unit_of_measurement": "µg/m³"}}}
    result = HomeAssistantPlugin._format_response(
        "PM2.5: {pm25[state]:.1f}{pm25[attributes][unit_of_measurement]}", context
    )
    assert result == "PM2.5: 8.6µg/m³"


def test_format_spec_on_non_numeric_string():
    context = {"t": {"state": "sunny", "attributes": {}}}
    result = HomeAssistantPlugin._format_response("{t[state]:.1f}", context)
    assert "sunny" in result


def test_format_missing_key_graceful():
    context = {"t": {"state": "sunny", "attributes": {}}}
    result = HomeAssistantPlugin._format_response("{t[state]} {t[attributes][missing]}", context)
    assert "sunny" in result
    assert "missing" in result  # placeholder rather than crash


def test_format_default_response(mock_app):
    """Commands without a response template default to {state}."""
    config = {
        "enabled": True,
        "url": "http://ha.local:8123",
        "token": "t",
        "commands": [
            {"pattern": "test", "entities": {"t": "sensor.test"}},
        ],
    }
    plugin = HomeAssistantPlugin(mock_app, config)
    assert plugin._commands[0].response == "{state}"


# -- HA API error handling --

@pytest.mark.asyncio
async def test_api_error_does_not_broadcast(ha_plugin):
    """When HA returns an error, no message is broadcast."""
    ha_plugin._session = _mock_session(None, status=404)

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="weather",
        channel=0,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_not_awaited()


# -- Channel forwarding --

@pytest.mark.asyncio
async def test_responds_on_correct_channel(ha_plugin):
    """Response is broadcast on the same channel the message came from."""
    ha_plugin._session = _mock_session({"state": "45", "attributes": {}})

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="humidity",
        channel=5,
        sender_name="TestNode",
    )
    await ha_plugin.on_mesh_event(event)
    ha_plugin._app.broadcast.assert_awaited_once_with(
        text="Humidity: 45%", channel=5, source_plugin="homeassistant"
    )


# -- Multi-entity commands --

@pytest.fixture
def multi_plugin(mock_app):
    config = {
        "enabled": True,
        "url": "http://ha.local:8123",
        "token": "t",
        "commands": [
            {
                "pattern": "(?i)^\\s*(air quality|aqi)\\s*$",
                "entities": {
                    "pm25": "sensor.pm25",
                    "pm10": "sensor.pm10",
                },
                "response": "Air: PM2.5 {pm25[state]}{pm25[attributes][unit_of_measurement]} | PM10 {pm10[state]}{pm10[attributes][unit_of_measurement]}",
            },
        ],
    }
    return HomeAssistantPlugin(mock_app, config)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["air quality", "Air Quality", "AQI", " aqi "])
async def test_multi_entity_command(multi_plugin, text):
    """Multi-entity command fetches both and formats response."""
    multi_plugin._session = _mock_session([
        {"state": "12.5", "attributes": {"unit_of_measurement": "µg/m³"}, "entity_id": "sensor.pm25"},
        {"state": "28.0", "attributes": {"unit_of_measurement": "µg/m³"}, "entity_id": "sensor.pm10"},
    ])

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text=text,
        channel=0,
        sender_name="TestNode",
    )
    await multi_plugin.on_mesh_event(event)
    multi_plugin._app.broadcast.assert_awaited_once_with(
        text="Air: PM2.5 12.5µg/m³ | PM10 28.0µg/m³",
        channel=0,
        source_plugin="homeassistant",
    )


@pytest.mark.asyncio
async def test_multi_entity_partial_failure(multi_plugin):
    """If one entity fails, no response is broadcast."""
    ok_resp = AsyncMock()
    ok_resp.status = 200
    ok_resp.json = AsyncMock(
        return_value={"state": "12.5", "attributes": {"unit_of_measurement": "µg/m³"}}
    )
    ok_ctx = AsyncMock()
    ok_ctx.__aenter__.return_value = ok_resp

    fail_resp = AsyncMock()
    fail_resp.status = 404
    fail_ctx = AsyncMock()
    fail_ctx.__aenter__.return_value = fail_resp

    session = MagicMock()
    session.get.side_effect = [ok_ctx, fail_ctx]
    session.close = AsyncMock()
    multi_plugin._session = session

    event = MeshEvent(
        event_type=EventType.CHANNEL_MESSAGE,
        text="air quality",
        channel=0,
        sender_name="TestNode",
    )
    await multi_plugin.on_mesh_event(event)
    multi_plugin._app.broadcast.assert_not_awaited()


def test_multi_entity_format():
    """Response template with named entity access."""
    context = {
        "pm25": {"state": "10", "attributes": {"unit_of_measurement": "µg/m³"}},
        "pm10": {"state": "25", "attributes": {"unit_of_measurement": "µg/m³"}},
    }
    result = HomeAssistantPlugin._format_response(
        "PM2.5: {pm25[state]}{pm25[attributes][unit_of_measurement]} PM10: {pm10[state]}{pm10[attributes][unit_of_measurement]}",
        context,
    )
    assert result == "PM2.5: 10µg/m³ PM10: 25µg/m³"
