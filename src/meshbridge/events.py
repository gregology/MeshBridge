"""Event types and data structures for MeshBridge."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class EventType(Enum):
    """All event types flowing through MeshBridge.

    These are MeshBridge's own enum values, decoupled from the meshcore
    library's EventType to insulate plugins from upstream changes.
    """

    # Messages
    CHANNEL_MESSAGE = auto()
    CONTACT_MESSAGE = auto()

    # Telemetry & sensors
    TELEMETRY = auto()
    SENSOR_DATA = auto()

    # Node status
    NODE_ONLINE = auto()
    NODE_OFFLINE = auto()
    PATH_UPDATE = auto()

    # Device
    DEVICE_INFO = auto()
    BATTERY = auto()

    # Bridge lifecycle
    BRIDGE_CONNECTED = auto()
    BRIDGE_DISCONNECTED = auto()

    # Outbound (plugin -> mesh)
    OUTBOUND_CHANNEL = auto()
    OUTBOUND_DIRECT = auto()


@dataclass(frozen=True)
class MeshEvent:
    """Immutable event flowing through the MeshBridge system.

    All mesh events are normalized into this structure regardless of origin.
    """

    event_type: EventType
    timestamp: float = field(default_factory=time.time)

    # Origin: "mesh", "discord", "slack", etc.
    source: str = "mesh"

    # Message fields (CHANNEL_MESSAGE, CONTACT_MESSAGE)
    text: str | None = None
    channel: int | None = None
    sender_name: str | None = None
    sender_key_prefix: str | None = None
    sender_timestamp: int | None = None
    path_len: int | None = None
    path: list[str] | None = None

    # Telemetry fields (TELEMETRY, SENSOR_DATA)
    telemetry: dict[str, Any] | None = None
    node_name: str | None = None

    # Outbound fields (OUTBOUND_CHANNEL, OUTBOUND_DIRECT)
    source_plugin: str | None = None
    contact_name: str | None = None

    # Raw payload for extensibility
    raw: dict[str, Any] | None = None
