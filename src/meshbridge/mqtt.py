"""Async-friendly wrapper around paho-mqtt."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# Type alias for async MQTT message callbacks
MQTTCallback = Callable[[str, bytes], Awaitable[None]]


class MQTTClient:
    """Wraps paho-mqtt to bridge its threaded callbacks into asyncio.

    Paho's network loop runs in a background thread via ``loop_start()``.
    Incoming messages are dispatched to async callbacks using
    ``asyncio.run_coroutine_threadsafe()``.
    """

    def __init__(self, config: dict, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_event_loop()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._subscriptions: dict[str, MQTTCallback] = {}

        self._broker = config.get("broker", "127.0.0.1")
        self._port = config.get("port", 1883)

        username = config.get("username")
        password = config.get("password")
        if username:
            self._client.username_pw_set(username, password)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    async def connect(self) -> None:
        """Connect to the MQTT broker and start the background network loop."""
        self._client.connect(self._broker, self._port)
        self._client.loop_start()
        logger.info("MQTT connected to %s:%d", self._broker, self._port)

    async def disconnect(self) -> None:
        """Stop the network loop and disconnect."""
        self._client.loop_stop()
        self._client.disconnect()
        logger.info("MQTT disconnected")

    async def publish(self, topic: str, payload: str) -> None:
        """Publish a message to an MQTT topic."""
        self._client.publish(topic, payload, qos=1)

    async def subscribe(self, topic: str, callback: MQTTCallback) -> None:
        """Subscribe to an MQTT topic with an async callback."""
        self._subscriptions[topic] = callback
        self._client.subscribe(topic, qos=1)
        logger.debug("Subscribed to %s", topic)

    # -- paho callbacks (called from paho's background thread) --

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        logger.info("MQTT broker connected (rc=%s)", reason_code)
        # Re-subscribe on reconnect
        for topic in self._subscriptions:
            client.subscribe(topic, qos=1)

    def _on_message(self, client, userdata, msg) -> None:
        for pattern, callback in self._subscriptions.items():
            if _topic_matches(pattern, msg.topic):
                asyncio.run_coroutine_threadsafe(
                    callback(msg.topic, msg.payload),
                    self._loop,
                )
                break

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            logger.warning(
                "MQTT disconnected unexpectedly (rc=%s), will auto-reconnect",
                reason_code,
            )


def _topic_matches(pattern: str, topic: str) -> bool:
    """Simple MQTT topic wildcard matching for ``+`` and ``#``."""
    pattern_parts = pattern.split("/")
    topic_parts = topic.split("/")
    for i, pp in enumerate(pattern_parts):
        if pp == "#":
            return True
        if i >= len(topic_parts):
            return False
        if pp != "+" and pp != topic_parts[i]:
            return False
    return len(pattern_parts) == len(topic_parts)
