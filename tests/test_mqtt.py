"""Tests for MQTT topic matching."""

import pytest

from meshbridge.mqtt import _topic_matches


@pytest.mark.parametrize(
    "pattern, topic, expected",
    [
        # Exact match
        ("meshbridge/inbound/channel/0", "meshbridge/inbound/channel/0", True),
        ("meshbridge/inbound/channel/0", "meshbridge/inbound/channel/1", False),
        # Single-level wildcard (+)
        ("meshbridge/inbound/channel/+", "meshbridge/inbound/channel/0", True),
        ("meshbridge/inbound/channel/+", "meshbridge/inbound/channel/5", True),
        ("meshbridge/outbound/+/+", "meshbridge/outbound/channel/0", True),
        ("meshbridge/+/channel/0", "meshbridge/inbound/channel/0", True),
        ("meshbridge/+/channel/0", "meshbridge/inbound/direct/0", False),
        # + doesn't match across levels
        ("meshbridge/+", "meshbridge/inbound/channel", False),
        # Multi-level wildcard (#)
        ("meshbridge/inbound/#", "meshbridge/inbound/channel/0", True),
        ("meshbridge/inbound/#", "meshbridge/inbound/direct/abc123", True),
        ("meshbridge/inbound/#", "meshbridge/inbound", True),
        ("meshbridge/#", "meshbridge/inbound/channel/0", True),
        ("#", "meshbridge/inbound/channel/0", True),
        # # doesn't match wrong prefix
        ("meshbridge/outbound/#", "meshbridge/inbound/channel/0", False),
        # Pattern longer than topic
        ("meshbridge/inbound/channel/0/extra", "meshbridge/inbound/channel/0", False),
        # Topic longer than pattern (no wildcards)
        ("meshbridge/inbound", "meshbridge/inbound/channel/0", False),
    ],
)
def test_topic_matches(pattern, topic, expected):
    assert _topic_matches(pattern, topic) is expected
