import paho.mqtt.client as mqtt
import requests
import json

MQTT_BROKER = "10.0.0.142"
MQTT_PORT = 1883
MQTT_USER = "user"
MQTT_PASS = "password"
MQTT_TOPIC = "meshcore/BOQ/channel/0"

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1468443751591448747/jS-VVl6UImT35C6L9MHAHdc0YpYU0jONc57-56JCCHz0ZWDUcmV1ViNVf4vJ_fyaIw4M"

def on_connect(client, userdata, flags, reason_code, properties):
    print(f"✓ Connected to MQTT broker")
    client.subscribe(MQTT_TOPIC)
    print(f"✓ Subscribed to {MQTT_TOPIC}")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        text = payload.get('text', 'Unknown message')
        timestamp = payload.get('timestamp', '')

        print(f"📨 Received: {text}")

        # Post to Discord
        discord_data = {
            "content": f"{text}",
            "username": "MeshCore Bridge"
        }

        response = requests.post(DISCORD_WEBHOOK_URL, json=discord_data)
        if response.status_code == 204:
            print(f"✓ Posted to Discord")
        else:
            print(f"⚠ Discord error: {response.status_code}")

    except Exception as e:
        print(f"Error: {e}")

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

print("🔌 Connecting to MQTT broker...")
mqtt_client.connect(MQTT_BROKER, MQTT_PORT)
mqtt_client.loop_forever()
