"""Main application orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import uuid

from meshbridge.bridge import Bridge
from meshbridge.config import load_config
from meshbridge.events import EventType, MeshEvent
from meshbridge.mqtt import MQTTClient
from meshbridge.plugin import BasePlugin
from meshbridge.plugins import load_plugins

logger = logging.getLogger(__name__)


class App:
    """Top-level orchestrator that wires together Bridge, MQTT, and plugins.

    Startup:  config -> MQTT -> Bridge -> plugins -> event dispatch loop
    Shutdown: plugins -> Bridge -> MQTT (reverse order)
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path = config_path
        self._config: dict = {}
        self._mqtt: MQTTClient | None = None
        self._bridge: Bridge | None = None
        self._plugins: list[BasePlugin] = []
        self._shutdown_event = asyncio.Event()
        self._pending_traces: dict[str, asyncio.Future[dict]] = {}

    async def run(self) -> None:
        """Main entry point. Runs until SIGINT/SIGTERM."""
        self._config = load_config(self._config_path)
        self._setup_logging()
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown_event.set)

        # 1. Connect MQTT
        self._mqtt = MQTTClient(self._config["mqtt"], loop)
        await self._mqtt.connect()

        # 2. Start Bridge
        self._bridge = Bridge(self._config, self._mqtt)
        await self._bridge.start()

        # 3. Load and start plugins
        self._plugins = load_plugins(self, self._config)
        for plugin in self._plugins:
            logger.info("Starting plugin: %s", plugin.plugin_name)
            await plugin.start()

        # 4. Subscribe to inbound topics to dispatch to plugins
        topic_prefix = self._config["mqtt"].get("topic_prefix", "meshbridge")
        await self._mqtt.subscribe(
            f"{topic_prefix}/inbound/trace_result/+",
            self._on_trace_result,
        )
        await self._mqtt.subscribe(
            f"{topic_prefix}/inbound/#",
            self._dispatch_to_plugins,
        )

        logger.info(
            "MeshBridge running with %d plugin(s). Device: %s",
            len(self._plugins),
            self._bridge.device_name,
        )

        # 5. Wait for shutdown signal
        await self._shutdown_event.wait()
        await self._shutdown()

    async def _shutdown(self) -> None:
        """Graceful shutdown in reverse order."""
        logger.info("Shutting down...")
        for plugin in reversed(self._plugins):
            try:
                await plugin.stop()
            except Exception:
                logger.exception("Error stopping plugin %s", plugin.plugin_name)
        if self._bridge:
            await self._bridge.stop()
        if self._mqtt:
            await self._mqtt.disconnect()
        logger.info("Shutdown complete")

    async def dispatch_event(self, event: MeshEvent) -> None:
        """Dispatch an event to all plugins.

        This is the central event hub. Any plugin can inject events from
        external sources (Discord, Slack, etc.) by calling this method.
        """
        for plugin in self._plugins:
            try:
                await plugin.on_mesh_event(event)
            except Exception:
                logger.exception(
                    "Plugin %s failed handling %s", plugin.plugin_name, event.event_type.name
                )

    @property
    def display_name(self) -> str:
        """Bridge display name used as sender for bridge-originated messages."""
        return self._config.get("bridge", {}).get("display_name", "MeshBridge")

    async def broadcast(self, text: str, channel: int = 0, source_plugin: str = "") -> None:
        """Send a message to mesh AND dispatch to all plugins.

        Use this when a response should reach every connected system
        (mesh radio, Discord, Slack, etc.) without the caller needing
        to know what those systems are.
        """
        await self.send_to_mesh(text, channel=channel, source_plugin=source_plugin)

        event = MeshEvent(
            event_type=EventType.CHANNEL_MESSAGE,
            source="meshbridge",
            text=text,
            channel=channel,
            source_plugin=source_plugin,
            sender_name=self.display_name,
        )
        await self.dispatch_event(event)

    async def _dispatch_to_plugins(self, topic: str, payload: bytes) -> None:
        """Dispatch an inbound MQTT message to all plugins as a MeshEvent."""
        try:
            data = json.loads(payload)
            event_type = EventType[data["event_type"]]
            event = MeshEvent(
                event_type=event_type,
                timestamp=data.get("timestamp", 0),
                source=data.get("source", "mesh"),
                text=data.get("text"),
                channel=data.get("channel"),
                sender_name=data.get("sender_name"),
                sender_key_prefix=data.get("sender_key_prefix"),
                sender_timestamp=data.get("sender_timestamp"),
                path_len=data.get("path_len"),
                telemetry=data.get("telemetry"),
                node_name=data.get("node_name"),
                source_plugin=data.get("source_plugin"),
                contact_name=data.get("contact_name"),
                raw=data.get("raw"),
            )
        except (json.JSONDecodeError, KeyError):
            logger.exception("Failed to parse inbound MQTT message on %s", topic)
            return

        await self.dispatch_event(event)

    async def send_to_mesh(self, text: str, channel: int = 0, source_plugin: str = "") -> None:
        """Publish a channel message to MQTT outbound (called by plugins)."""
        if not self._mqtt:
            return
        prefix = self._config["mqtt"].get("topic_prefix", "meshbridge")
        await self._mqtt.publish(
            f"{prefix}/outbound/channel/{channel}",
            json.dumps({"text": text, "source_plugin": source_plugin}),
        )

    async def request_trace(
        self, key_or_name: str, timeout: float = 30.0
    ) -> dict:
        """Ask the bridge to run a path discovery and return the result.

        Publishes to ``outbound/trace_request``, awaits the matching
        ``inbound/trace_result/{corr_id}`` message. Returns a dict with
        either ``path_text``/``hops``/``contact_name`` on success or ``error``.
        """
        if not self._mqtt:
            return {"error": "mqtt not connected"}

        corr_id = uuid.uuid4().hex
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending_traces[corr_id] = future

        prefix = self._config["mqtt"].get("topic_prefix", "meshbridge")
        await self._mqtt.publish(
            f"{prefix}/outbound/trace_request",
            json.dumps(
                {"corr_id": corr_id, "key_or_name": key_or_name, "timeout": timeout}
            ),
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout + 5.0)
        except asyncio.TimeoutError:
            return {"error": "trace timed out"}
        finally:
            self._pending_traces.pop(corr_id, None)

    async def _on_trace_result(self, topic: str, payload: bytes) -> None:
        """Resolve the pending trace future matching this correlation id."""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.exception("Malformed trace_result on %s", topic)
            return
        corr_id = data.get("corr_id", "")
        future = self._pending_traces.get(corr_id)
        if future and not future.done():
            future.set_result(data)

    async def send_direct_to_mesh(
        self,
        text: str,
        contact_name: str = "",
        source_plugin: str = "",
        contact_key: str = "",
    ) -> None:
        """Publish a direct message to MQTT outbound (called by plugins)."""
        if not self._mqtt:
            return
        prefix = self._config["mqtt"].get("topic_prefix", "meshbridge")
        identifier = contact_name or contact_key or "unknown"
        payload: dict[str, str] = {"text": text, "source_plugin": source_plugin}
        if contact_name:
            payload["contact_name"] = contact_name
        if contact_key:
            payload["contact_key"] = contact_key
        await self._mqtt.publish(
            f"{prefix}/outbound/direct/{identifier}",
            json.dumps(payload),
        )

    def _setup_logging(self) -> None:
        """Configure logging from config."""
        log_config = self._config.get("logging", {})
        level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)
        log_file = log_config.get("file")

        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if log_file:
            handlers.append(logging.FileHandler(log_file))

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=handlers,
        )
