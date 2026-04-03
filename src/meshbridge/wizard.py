"""Interactive setup wizard for MeshBridge."""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def run_wizard(config_path: str | None = None) -> None:
    """Walk through all config sections and generate a config file."""
    print("=" * 50)
    print("  MeshBridge Setup Wizard")
    print("=" * 50)
    print()

    config: dict = {}

    config["device"] = _setup_device()
    config["mqtt"] = _setup_mqtt()
    config["logging"] = _setup_logging()
    config["plugins"] = {"discord": _setup_discord()}

    target = Path(config_path or _default_config_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print()
    print(f"Config written to: {target}")
    print()
    print("Next steps:")
    print(f"  Review config:  cat {target}")
    print("  Start bridge:   meshbridge run")
    print("  Or with systemd: sudo systemctl start meshbridge")


def _default_config_path() -> str:
    """Pick a sensible default config path."""
    if os.geteuid() == 0:
        return "/etc/meshbridge/config.yaml"
    return str(Path.home() / ".config" / "meshbridge" / "config.yaml")


def _setup_device() -> dict:
    print("[1/4] MeshCore Device")
    print("-" * 30)

    serial_dir = Path("/dev/serial/by-id")
    devices = sorted(serial_dir.glob("*")) if serial_dir.exists() else []

    if devices:
        print("Detected serial devices:")
        for i, dev in enumerate(devices, 1):
            print(f"  [{i}] {dev.name}")
        choice = input(f"Select device [1-{len(devices)}] (default 1): ").strip()
        idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(devices) else 0
        serial_port = str(devices[idx])
    else:
        print("No serial devices detected in /dev/serial/by-id/")
        serial_port = input("Enter serial port path: ").strip()

    return {"serial_port": serial_port, "baudrate": 115200}


def _setup_mqtt() -> dict:
    print()
    print("[2/4] MQTT Broker")
    print("-" * 30)

    # Check for pre-populated password from install.sh
    default_pass = os.environ.get("MESHBRIDGE_MQTT_PASS", "")

    broker = input("MQTT broker address [127.0.0.1]: ").strip() or "127.0.0.1"
    port = input("MQTT port [1883]: ").strip() or "1883"
    username = input("MQTT username [meshbridge]: ").strip() or "meshbridge"
    if default_pass:
        print("  (Using auto-generated password from installer)")
        password = default_pass
    else:
        password = input("MQTT password: ").strip()
    topic_prefix = input("MQTT topic prefix [meshbridge]: ").strip() or "meshbridge"

    return {
        "broker": broker,
        "port": int(port),
        "username": username,
        "password": password,
        "topic_prefix": topic_prefix,
    }


def _setup_discord() -> dict:
    print()
    print("[3/4] Discord Plugin")
    print("-" * 30)

    enable = input("Enable Discord plugin? [Y/n]: ").strip().lower() != "n"
    if not enable:
        return {"enabled": False}

    webhook_url = input("Discord webhook URL: ").strip()
    bot_username = input("Bot display name [MeshBridge]: ").strip() or "MeshBridge"

    return {
        "enabled": True,
        "webhook_url": webhook_url,
        "bot_username": bot_username,
        "include_metadata": True,
        "channels": [0],
        "event_types": ["CHANNEL_MESSAGE", "CONTACT_MESSAGE"],
    }


def _setup_logging() -> dict:
    print()
    print("[4/4] Logging")
    print("-" * 30)

    level = input("Log level (DEBUG/INFO/WARNING/ERROR) [INFO]: ").strip().upper() or "INFO"

    return {"level": level, "file": None}
