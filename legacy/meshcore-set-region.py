import asyncio
from meshcore import MeshCore

SERIAL_PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"

async def main():
    mc = await MeshCore.create_serial(SERIAL_PORT)
    print(f"Current frequency: {mc.self_info['radio_freq']} MHz")

    # Set to US/Canada preset (915 MHz, BW 125, SF 11)
    print("\n🔧 Setting radio to US/Canada preset...")
    try:
        result = await mc.commands.set_radio(
            freq=910.525,      # US/Canada frequency
            bw=62.5,        # Bandwidth 125 kHz
            sf=7,           # Spreading factor 11
            cr=5             # Coding rate 4/5
        )
        print(f"✓ Radio configured: {result}")
        print("\n⏳ Rebooting device for changes to take effect...")
        await mc.commands.reboot()
        await asyncio.sleep(5)
        print("✓ Done! Device should now be on 915 MHz")

    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
