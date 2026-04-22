"""Bidirectional bridge between a MeshCore serial device and MQTT."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
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

        Publishes the formatted path back on ``inbound/trace_result/{corr_id}``.
        """
        corr_id = ""
        try:
            data = json.loads(payload)
            corr_id = data["corr_id"]
            key_or_name = data.get("key_or_name") or ""
            timeout = float(data.get("timeout", 30.0))
            inbound_path_len = data.get("inbound_path_len")
            if inbound_path_len is not None:
                inbound_path_len = int(inbound_path_len)
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.exception("Malformed trace_request payload")
            return

        result = await self._run_trace(key_or_name, timeout, inbound_path_len)
        result["corr_id"] = corr_id
        await self._mqtt.publish(
            f"{self._topic_prefix}/inbound/trace_result/{corr_id}",
            json.dumps(result),
        )

    async def _run_trace(
        self,
        key_or_name: str,
        timeout: float,
        inbound_path_len: int | None = None,
    ) -> dict[str, Any]:
        """Resolve a contact and return a formatted trace result dict.

        Order of preference:
        1. Cached ``out_path`` on the contact record (instant).
        2. Probe experiment: race PATH_RESPONSE, PATH_UPDATE, ADVERTISEMENT
           against two probes (path_discovery + statusreq).
        3. Fallback: report ``inbound_path_len`` hop count if we have it.
        """
        if not self._mc:
            return {"error": "bridge not connected"}

        contact = self._resolve_contact(key_or_name)
        if not contact:
            # Refresh the contact cache and retry once in case the sender
            # advertised after startup.
            logger.info("Trace lookup '%s': cache miss, refreshing contacts", key_or_name)
            try:
                await self._mc.commands.get_contacts()
            except Exception:
                logger.exception("Failed to refresh contacts for trace lookup")
            contact = self._resolve_contact(key_or_name)
        if not contact:
            logger.info("Trace lookup '%s': contact still not found after refresh", key_or_name)
            return {"error": f"unknown contact '{key_or_name}'"}

        out_path_len = int(contact.get("out_path_len", -1))
        public_key = contact.get("public_key", "") or ""
        logger.info(
            "Trace lookup '%s': pubkey=%s out_path_len=%d inbound_path_len=%s",
            contact.get("adv_name"),
            public_key[:16],
            out_path_len,
            inbound_path_len,
        )

        if out_path_len >= 0:
            logger.info(
                "Using cached path for '%s': %s",
                contact.get("adv_name"),
                contact.get("out_path"),
            )
            return _format_cached_path(contact)

        if len(public_key) < 12:
            return {"error": "contact missing public key"}

        probe_result = await self._probe_path(contact, timeout)
        if probe_result is not None:
            return probe_result

        # All probes exhausted. Fall back to inbound hop count if known.
        if inbound_path_len is not None and inbound_path_len > 0:
            logger.info(
                "Probe experiment exhausted; falling back to inbound_path_len=%d",
                inbound_path_len,
            )
            return {
                "path_text": f"~{inbound_path_len} hops inbound (no return path)",
                "hops": inbound_path_len,
                "contact_name": contact.get("adv_name"),
            }

        return {"error": "no cached path; discovery failed"}

    async def _probe_path(self, contact: dict, timeout: float) -> dict | None:
        """Race multiple discovery mechanisms to learn a path for ``contact``.

        Listeners (all filtered to this contact / our trace tag):
            PATH_RESPONSE, PATH_UPDATE, ADVERTISEMENT, TRACE_DATA

        Probes fired in parallel:
            send_path_discovery, send_statusreq, send_trace (no path, random tag)

        Also installs a broad "observe" subscription for the probe window so
        every meshcore event we hear gets logged for debugging.

        Returns a formatted result dict if any listener produces a usable
        path, or None on overall timeout.
        """
        mc = self._mc
        if mc is None:
            return None

        public_key = contact["public_key"]
        pubkey_pre = public_key[:12]
        adv_name = contact.get("adv_name")
        trace_tag = random.randint(1, 0xFFFFFFFF)

        logger.info(
            "=== Probe experiment START name='%s' pubkey=%s timeout=%.1fs trace_tag=0x%08x ===",
            adv_name,
            public_key,
            timeout,
            trace_tag,
        )
        logger.info("Contact record: %s", _redact_contact(contact))

        # Install a broad observer that logs every meshcore event during the
        # probe window. Very useful for seeing unexpected traffic.
        observer_sub = mc.subscribe(None, self._observe_event)

        # Create listeners BEFORE sending probes so no event is missed.
        response_task = asyncio.create_task(
            mc.wait_for_event(
                MCEventType.PATH_RESPONSE,
                attribute_filters={"pubkey_pre": pubkey_pre},
                timeout=timeout,
            ),
            name="wait_path_response",
        )
        update_task = asyncio.create_task(
            mc.wait_for_event(
                MCEventType.PATH_UPDATE,
                attribute_filters={"public_key": public_key},
                timeout=timeout,
            ),
            name="wait_path_update",
        )
        advert_task = asyncio.create_task(
            mc.wait_for_event(
                MCEventType.ADVERTISEMENT,
                attribute_filters={"public_key": public_key},
                timeout=timeout,
            ),
            name="wait_advertisement",
        )
        trace_task = asyncio.create_task(
            mc.wait_for_event(
                MCEventType.TRACE_DATA,
                attribute_filters={"tag": trace_tag},
                timeout=timeout,
            ),
            name="wait_trace_data",
        )
        listeners = [response_task, update_task, advert_task, trace_task]
        logger.info(
            "Listeners armed: PATH_RESPONSE[pubkey_pre=%s] PATH_UPDATE[public_key=%s] "
            "ADVERTISEMENT[public_key=%s] TRACE_DATA[tag=0x%08x]",
            pubkey_pre,
            public_key,
            public_key,
            trace_tag,
        )

        probes = [
            ("send_path_discovery", lambda: mc.commands.send_path_discovery(contact)),
            ("send_statusreq", lambda: mc.commands.send_statusreq(contact)),
            ("send_trace", lambda: mc.commands.send_trace(tag=trace_tag)),
        ]

        t_start = time.monotonic()
        for name, fn in probes:
            t0 = time.monotonic()
            try:
                result = await fn()
                dt_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    "Probe %s -> sent in %.1fms; result type=%s payload=%s attrs=%s",
                    name,
                    dt_ms,
                    getattr(result, "type", None),
                    getattr(result, "payload", None),
                    getattr(result, "attributes", None),
                )
            except Exception:
                logger.exception("Probe %s raised", name)

        try:
            done, pending = await asyncio.wait(
                listeners, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in listeners:
                if not task.done():
                    task.cancel()
            observer_sub.unsubscribe()

        elapsed_ms = (time.monotonic() - t_start) * 1000
        logger.info(
            "asyncio.wait returned after %.1fms: %d done, %d pending",
            elapsed_ms,
            len(done),
            len(pending),
        )

        result: dict | None = None
        for task in done:
            if task.cancelled():
                continue
            try:
                event = task.result()
            except Exception:
                logger.exception("Listener %s raised", task.get_name())
                continue
            if event is None:
                logger.info("Listener %s finished with None (timeout)", task.get_name())
                continue

            mc_type = getattr(event, "type", None)
            attrs = getattr(event, "attributes", None)
            payload = getattr(event, "payload", None)
            logger.info(
                "Listener %s FIRED: type=%s attributes=%s payload=%s",
                task.get_name(),
                mc_type,
                attrs,
                payload,
            )

            if result is not None:
                continue  # already have a result, but keep logging others

            if mc_type == MCEventType.PATH_RESPONSE:
                result = _format_trace_payload(payload or {}, contact)
                logger.info("Resolved via PATH_RESPONSE: %s", result)
                continue

            if mc_type == MCEventType.TRACE_DATA:
                result = _format_trace_data(payload or {}, contact)
                logger.info("Resolved via TRACE_DATA: %s", result)
                continue

            # PATH_UPDATE / ADVERTISEMENT don't carry path directly. Refresh
            # the contact cache to pick up any newly-learned out_path.
            try:
                await mc.commands.get_contacts()
            except Exception:
                logger.exception("get_contacts after %s failed", mc_type)

            refreshed = self._resolve_contact(public_key[:12]) or contact
            refreshed_len = int(refreshed.get("out_path_len", -1))
            logger.info(
                "After %s, contact '%s' out_path_len=%d out_path=%s",
                mc_type,
                adv_name,
                refreshed_len,
                refreshed.get("out_path"),
            )
            if refreshed_len >= 0:
                result = _format_cached_path(refreshed)
                logger.info("Resolved via cache refresh: %s", result)

        if result is None:
            logger.info("=== Probe experiment END name='%s' NO RESULT ===", adv_name)
        else:
            logger.info("=== Probe experiment END name='%s' -> %s ===", adv_name, result)
        return result

    async def _observe_event(self, event) -> None:
        """Log every meshcore event seen during a probe window."""
        try:
            payload = getattr(event, "payload", None)
            logger.info(
                "observed event: type=%s attributes=%s payload=%s",
                getattr(event, "type", None),
                getattr(event, "attributes", None),
                _summarize_payload(payload),
            )
        except Exception:
            logger.exception("observer failed to format event")


def _format_cached_path(contact: dict) -> dict[str, Any]:
    """Format the cached ``out_path`` on a contact record into a result dict."""
    out_path_len = int(contact.get("out_path_len", 0))
    out_path_hex = str(contact.get("out_path") or "")
    return _build_result(out_path_len, out_path_hex, contact.get("adv_name"))


def _format_trace_payload(payload: dict, contact: dict) -> dict[str, Any]:
    """Format a PATH_RESPONSE payload into a result dict."""
    out_path_len = int(payload.get("out_path_len") or 0)
    out_path_hex = str(payload.get("out_path") or "")
    return _build_result(out_path_len, out_path_hex, contact.get("adv_name"))


def _format_trace_data(payload: dict, contact: dict) -> dict[str, Any]:
    """Format a TRACE_DATA payload (hop hashes + per-hop SNR) into a result dict."""
    path_nodes = payload.get("path") or []
    hops = [n for n in path_nodes if "hash" in n]
    if not hops:
        return {
            "path_text": "direct (0 hops)",
            "hops": 0,
            "contact_name": contact.get("adv_name"),
        }
    path_text = " > ".join(
        f"{n['hash']}@{n.get('snr'):.1f}dB" if n.get("snr") is not None else n["hash"]
        for n in hops
    )
    return {
        "path_text": path_text,
        "hops": len(hops),
        "contact_name": contact.get("adv_name"),
    }


def _redact_contact(contact: dict) -> dict:
    """Copy of contact with potentially noisy/large fields dropped for logging."""
    keep = (
        "adv_name",
        "public_key",
        "type",
        "flags",
        "out_path_len",
        "out_path",
        "lastmod",
    )
    return {k: contact.get(k) for k in keep if k in contact}


def _summarize_payload(payload) -> str:
    """Short string representation of an event payload for logging."""
    if payload is None:
        return "None"
    if isinstance(payload, dict):
        parts = []
        for k, v in payload.items():
            if isinstance(v, (bytes, bytearray)):
                parts.append(f"{k}=<bytes:{v.hex()}>")
            elif isinstance(v, str) and len(v) > 80:
                parts.append(f"{k}=<str:{len(v)}ch>")
            elif isinstance(v, (list, dict)) and len(str(v)) > 120:
                parts.append(f"{k}=<{type(v).__name__}:{len(v)}>")
            else:
                parts.append(f"{k}={v!r}")
        return "{" + ", ".join(parts) + "}"
    return repr(payload)


def _build_result(out_path_len: int, out_path_hex: str, contact_name) -> dict[str, Any]:
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
