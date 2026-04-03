import asyncio
from meshcore import MeshCore, EventType
import paho.mqtt.client as mqtt
import json

SERIAL_PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"
MQTT_BROKER = "10.0.0.142"
MQTT_PORT = 1883
MQTT_USER = "user"
MQTT_PASS = "password"
MQTT_TOPIC_PREFIX = "meshcore/BOQ"

# Setup MQTT
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT)
mqtt_client.loop_start()

async def on_channel_msg(event):
    payload = event.payload
    print(f"📨 Channel message: {payload['text']}")

    # Publish to MQTT
    topic = f"{MQTT_TOPIC_PREFIX}/channel/{payload['channel_idx']}"
    mqtt_payload = json.dumps({
        "text": payload['text'],
        "channel": payload['channel_idx'],
        "timestamp": payload['sender_timestamp'],
        "path_len": payload['path_len']
    })
    mqtt_client.publish(topic, mqtt_payload)
    print(f"✓ Published to {topic}")

async def main():
    print("🔌 Connecting to MeshCore...")
    mc = await MeshCore.create_serial(SERIAL_PORT)
    print(f"✓ Connected! Device: {mc.self_info['name']}")

    # Subscribe to channel messages
    mc.subscribe(EventType.CHANNEL_MSG_RECV, on_channel_msg)

    # Start auto message fetching
    print("📬 Starting auto message fetching...")
    await mc.start_auto_message_fetching()

    print(f"✓ Bridge running! Publishing to MQTT topic: {MQTT_TOPIC_PREFIX}/channel/0")
    print("Press Ctrl+C to stop\n")

    await asyncio.Event().wait()

asyncio.run(main())
