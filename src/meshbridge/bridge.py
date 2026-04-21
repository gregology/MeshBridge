"""Bidirectional bridge between a MeshCore serial device and MQTT."""

from __future__ import annotations

import json
import logging
from typing import Any

from meshcore import EventType as MCEventType
from meshcore import MeshCore

from meshbridge.events import EventType, MeshEvent
from meshbridge.mqtt import MQTTClient

logger = logging.getLogger(__name__)

# Map meshcore library EventType -> MeshBridge EventType
_MC_EVENT_MAP: dict[MCEventType, EventType] = {
    MCEventType.CHANNEL_MSG_RECV: EventType.CHANNEL_MESSAGE,
    MCEventType.CONTACT_MSG_RECV: EventType.CONTACT_MESSAGE,
}

# Serializable fields on MeshEvent (excluding event_type and timestamp which
# are always included).
_SERIALIZE_FIELDS = (
    "source",
    "text",
    "channel",
    "sender_name",
    "sender_key_prefix",
    "sender_timestamp",
    "path_len",
    "path",
    "telemetry",
    "node_name",
    "source_plugin",
    "contact_name",
)


class Bridge:
    """Bidirectional bridge between MeshCore and MQTT.

    Inbound:  MeshCore events -> normalized MeshEvent -> MQTT inbound topics
    Outbound: MQTT outbound topics -> MeshCore serial commands
    """

    def __init__(self, config: dict, mqtt_client: MQTTClient) -> None:
        self._config = config
        self._mqtt = mqtt_client
        self._mc: MeshCore | None = None
        self._topic_prefix: str = config["mqtt"].get("topic_prefix", "meshbridge")

    async def start(self) -> None:
        """Connect to the MeshCore device and set up event flow."""
        device_cfg = self._config["device"]
        serial_port = device_cfg["serial_port"]
        baudrate = device_cfg.get("baudrate", 115200)

        logger.info("Connecting to MeshCore device on %s", serial_port)
        self._mc = await MeshCore.create_serial(serial_port, baudrate=baudrate)
        if self._mc is None:
            raise ConnectionError(
                f"Failed to connect to MeshCore device on {serial_port}. "
                "Check that the device is plugged in and is a serial companion."
            )
        logger.info("Connected: device=%s", self._mc.self_info.get("name", "unknown"))

        # Optionally set the radio device name from config and advertise
        # so other nodes on the mesh learn the name.
        device_name = self._config["device"].get("name")
        if device_name:
            try:
                await self._mc.commands.set_name(device_name)
                await self._mc.commands.send_advert()
                logger.info("Set device name to '%s' and sent advertisement", device_name)
            except Exception:
                logger.exception("Failed to set device name to '%s'", device_name)

        # Subscribe to inbound meshcore events
        for mc_event_type in _MC_EVENT_MAP:
            self._mc.subscribe(mc_event_type, self._on_meshcore_event)

        # Subscribe to MQTT outbound topics (plugin -> mesh)
        await self._mqtt.subscribe(
            f"{self._topic_prefix}/outbound/channel/+",
            self._on_outbound_channel_msg,
        )
        await self._mqtt.subscribe(
            f"{self._topic_prefix}/outbound/direct/+",
            self._on_outbound_direct_msg,
        )

        # Start auto-fetching messages from the device
        await self._mc.start_auto_message_fetching()

        # Publish bridge status
        await self._mqtt.publish(f"{self._topic_prefix}/status/bridge", "online")

        logger.info("Bridge running. Topic prefix: %s", self._topic_prefix)

    async def stop(self) -> None:
        """Disconnect from the MeshCore device."""
        if self._mc:
            await self._mc.stop_auto_message_fetching()
            await self._mc.disconnect()
            self._mc = None
        await self._mqtt.publish(f"{self._topic_prefix}/status/bridge", "offline")
        logger.info("Bridge stopped")

    @property
    def device_name(self) -> str | None:
        if self._mc and self._mc.self_info:
            return self._mc.self_info.get("name")
        return None

    # -- Inbound: MeshCore -> MQTT --

    async def _on_meshcore_event(self, event: Any) -> None:
        """Handle an event from the meshcore library."""
        mc_type = getattr(event, "type", None)
        bridge_type = _MC_EVENT_MAP.get(mc_type)
        if bridge_type is None:
            logger.debug("Unmapped meshcore event type: %s", mc_type)
            return

        payload = getattr(event, "payload", {})
        mesh_event = self._build_mesh_event(bridge_type, payload)
        await self._publish_inbound(mesh_event)

    def _build_mesh_event(self, event_type: EventType, payload: dict) -> MeshEvent:
        """Convert a meshcore payload into a MeshEvent."""
        if event_type in (EventType.CHANNEL_MESSAGE, EventType.CONTACT_MESSAGE):
            text = payload.get("text")
            sender_name = payload.get("sender_name", payload.get("adv_name"))

            # DM payloads don't include sender_name — resolve from contacts list
            if not sender_name and self._mc:
                pubkey = payload.get("pubkey_prefix")
                if pubkey:
                    contact = self._mc.get_contact_by_key_prefix(pubkey)
                    if contact:
                        sender_name = contact.get("adv_name")

            # MeshCore radio firmware prepends the sender name to channel
            # message text as "SenderName: message".  When the payload has no
            # explicit sender_name field (typical for CHANNEL_MSG_RECV), split
            # it out so downstream plugins see clean text.
            if (
                event_type == EventType.CHANNEL_MESSAGE
                and not sender_name
                and text
                and ": " in text
            ):
                sender_name, text = text.split(": ", 1)

            path = payload.get("path") or payload.get("out_path")

            return MeshEvent(
                event_type=event_type,
                text=text,
                channel=payload.get("channel_idx"),
                sender_name=sender_name,
                sender_key_prefix=payload.get("pubkey_prefix"),
                sender_timestamp=payload.get("sender_timestamp"),
                path_len=payload.get("path_len"),
                path=path if isinstance(path, list) else None,
                raw=payload,
            )
        elif event_type == EventType.TELEMETRY:
            return MeshEvent(
                event_type=event_type,
                telemetry=payload,
                node_name=payload.get("node_name"),
                raw=payload,
            )
        else:
            return MeshEvent(event_type=event_type, raw=payload)

    async def _publish_inbound(self, event: MeshEvent) -> None:
        """Publish a MeshEvent to the appropriate MQTT inbound topic."""
        topic = self._inbound_topic_for(event)
        payload_json = _serialize_event(event)
        await self._mqtt.publish(topic, payload_json)
        logger.debug("Published to %s", topic)

    def _inbound_topic_for(self, event: MeshEvent) -> str:
        """Determine the MQTT topic for an inbound event."""
        p = self._topic_prefix
        match event.event_type:
            case EventType.CHANNEL_MESSAGE:
                return f"{p}/inbound/channel/{event.channel or 0}"
            case EventType.CONTACT_MESSAGE:
                return f"{p}/inbound/direct/{event.sender_key_prefix or 'unknown'}"
            case EventType.TELEMETRY:
                return f"{p}/inbound/telemetry/{event.node_name or 'unknown'}"
            case EventType.NODE_ONLINE:
                return f"{p}/inbound/node/online"
            case _:
                return f"{p}/inbound/{event.event_type.name.lower()}"

    # -- Outbound: MQTT -> MeshCore --

    async def _on_outbound_channel_msg(self, topic: str, payload: bytes) -> None:
        """Handle an outbound channel message from a plugin."""
        try:
            data = json.loads(payload)
            text = data["text"]
            channel_idx = int(topic.rsplit("/", 1)[-1])
            source = data.get("source_plugin", "unknown")
            logger.info("Outbound ch%d from %s: %s", channel_idx, source, text[:80])
            if self._mc:
                await self._mc.commands.send_chan_msg(channel_idx, text)
        except Exception:
            logger.exception("Failed to process outbound channel message")

    async def _on_outbound_direct_msg(self, topic: str, payload: bytes) -> None:
        """Handle an outbound direct message from a plugin."""
        try:
            data = json.loads(payload)
            text = data["text"]
            contact_name = data.get("contact_name")
            contact_key = data.get("contact_key")
            source = data.get("source_plugin", "unknown")
            logger.info(
                "Outbound DM to %s from %s: %s",
                contact_name or contact_key,
                source,
                text[:80],
            )
            if not self._mc:
                return
            # Resolve destination: try contact name, then key prefix lookup,
            # then send directly to the raw key prefix (meshcore accepts hex strings).
            dest = None
            if contact_name:
                dest = self._mc.get_contact_by_name(contact_name)
            if not dest and contact_key:
                dest = self._mc.get_contact_by_key_prefix(contact_key)
            if not dest and contact_key:
                dest = contact_key
            if dest:
                await self._mc.commands.send_msg(dest, text)
            else:
                logger.warning("No destination for DM: name=%s key=%s", contact_name, contact_key)
        except Exception:
            logger.exception("Failed to process outbound direct message")


def _serialize_event(event: MeshEvent) -> str:
    """Serialize a MeshEvent to JSON for MQTT."""
    data: dict[str, Any] = {
        "event_type": event.event_type.name,
        "timestamp": event.timestamp,
    }
    for attr in _SERIALIZE_FIELDS:
        val = getattr(event, attr, None)
        if val is not None:
            data[attr] = val
    if event.raw:
        data["raw"] = event.raw
    return json.dumps(data)
