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
        await self._mqtt.subscribe(
            f"{self._topic_prefix}/outbound/trace_request",
            self._on_trace_request,
        )

        # Load the device's contact list and keep it fresh as nodes advertise.
        self._mc.auto_update_contacts = True
        try:
            await self._mc.ensure_contacts()
            logger.info("Loaded %d contact(s) from device", len(self._mc.contacts))
        except Exception:
            logger.exception("Failed to load contacts on startup")

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

            return MeshEvent(
                event_type=event_type,
                text=text,
                channel=payload.get("channel_idx"),
                sender_name=sender_name,
                sender_key_prefix=payload.get("pubkey_prefix"),
                sender_timestamp=payload.get("sender_timestamp"),
                path_len=payload.get("path_len"),
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

    # -- Trace (path discovery) --

    def _resolve_contact(self, key_or_name: str) -> dict | None:
        """Look up a contact by key prefix first, then by name."""
        if not self._mc or not key_or_name:
            return None
        return self._mc.get_contact_by_key_prefix(
            key_or_name
        ) or self._mc.get_contact_by_name(key_or_name)

    async def _on_trace_request(self, topic: str, payload: bytes) -> None:
        """Handle a trace request from a plugin.

        Issues a path discovery to the target contact, awaits the meshcore
        PATH_RESPONSE, and publishes the formatted path back on
        ``inbound/trace_result/{corr_id}``.
        """
        corr_id = ""
        try:
            data = json.loads(payload)
            corr_id = data["corr_id"]
            key_or_name = data.get("key_or_name") or ""
            timeout = float(data.get("timeout", 30.0))
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.exception("Malformed trace_request payload")
            return

        result = await self._run_trace(key_or_name, timeout)
        result["corr_id"] = corr_id
        await self._mqtt.publish(
            f"{self._topic_prefix}/inbound/trace_result/{corr_id}",
            json.dumps(result),
        )

    async def _run_trace(self, key_or_name: str, timeout: float) -> dict[str, Any]:
        """Resolve a contact and return a formatted trace result dict."""
        if not self._mc:
            return {"error": "bridge not connected"}

        contact = self._resolve_contact(key_or_name)
        if not contact:
            # Refresh the contact cache and retry once in case the sender
            # advertised after startup.
            try:
                await self._mc.commands.get_contacts()
            except Exception:
                logger.exception("Failed to refresh contacts for trace lookup")
            contact = self._resolve_contact(key_or_name)
        if not contact:
            return {"error": f"unknown contact '{key_or_name}'"}

        public_key = contact.get("public_key", "")
        if len(public_key) < 12:
            return {"error": "contact missing public key"}
        pubkey_pre = public_key[:12]

        try:
            await self._mc.commands.send_path_discovery(contact)
        except Exception as exc:
            logger.exception("send_path_discovery failed")
            return {"error": f"path discovery failed: {exc}"}

        event = await self._mc.wait_for_event(
            MCEventType.PATH_RESPONSE,
            attribute_filters={"pubkey_pre": pubkey_pre},
            timeout=timeout,
        )
        if event is None:
            return {"error": "trace timed out"}

        return _format_trace_event(event.payload, contact)


def _format_trace_event(payload: dict, contact: dict) -> dict[str, Any]:
    """Format a meshcore PATH_RESPONSE payload into a trace result dict."""
    out_path_len = int(payload.get("out_path_len") or 0)
    out_path_hex = str(payload.get("out_path") or "")
    contact_name = contact.get("adv_name")

    if out_path_len == 0:
        return {
            "path_text": "direct (0 hops)",
            "hops": 0,
            "contact_name": contact_name,
        }

    nodes = [out_path_hex[i : i + 2] for i in range(0, out_path_len * 2, 2)]
    return {
        "path_text": " > ".join(nodes),
        "hops": out_path_len,
        "contact_name": contact_name,
    }


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
