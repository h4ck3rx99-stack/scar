"""
Jarvis Mock WebSocket & Event Server
Simulates the Python AI Brain on port 8765 without requiring heavy local ML models.
Use this to test the Tauri shell and React Three Fiber frontend independently.
"""

import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

HOST = "127.0.0.1"
PORT = 8765

CONNECTED_CLIENTS = set()

async def broadcast_event(data: dict):
    """Broadcast JSON payload to all connected clients."""
    payload = json.dumps(data)
    logging.info(f"Broadcasting: {payload}")
    # In real websockets implementation:
    for ws in list(CONNECTED_CLIENTS):
        try:
            await ws.send(payload)
        except Exception as e:
            logging.error(f"Error sending to client: {e}")
            CONNECTED_CLIENTS.discard(ws)

def generate_voice_state(status: str) -> dict:
    assert status in ["listening", "processing", "speaking"]
    return {
        "event": "voice_state",
        "status": status
    }

def generate_os_command(action: str, target: str, x: int, y: int) -> dict:
    assert action in ["minimize", "resize", "click"]
    return {
        "event": "os_command",
        "action": action,
        "target_window": target,
        "coordinates": {"x": x, "y": y}
    }

def generate_ui_animation(anim_type: str, x: int, y: int) -> dict:
    assert anim_type in ["fly", "stretch", "idle"]
    return {
        "event": "ui_animation",
        "type": anim_type,
        "target": {"x": x, "y": y}
    }

async def mock_interaction_scenario():
    """Simulates a complete Jarvis command cycle and 5-second Alexa follow-up window."""
    logging.info("Starting mock simulation cycle in 3 seconds...")
    await asyncio.sleep(3)

    # 1. Wake word detected -> Listening
    logging.info("--- Step 1: User says 'Hey Jarvis' ---")
    await broadcast_event(generate_voice_state("listening"))
    await asyncio.sleep(2)

    # 2. User says 'Minimize Chrome' -> Processing
    logging.info("--- Step 2: Processing STT and LLM tool call ---")
    await broadcast_event(generate_voice_state("processing"))
    await asyncio.sleep(1)

    # 3. LLM emits tool call with target coordinates
    logging.info("--- Step 3: LLM emits os_command and UI flight animation ---")
    await broadcast_event(generate_ui_animation("fly", 1820, 15))
    await broadcast_event(generate_os_command("minimize", "chrome.exe", 1820, 15))

    # 4. Assistant speaks verbal confirmation
    logging.info("--- Step 4: Assistant speaks ---")
    await broadcast_event(generate_voice_state("speaking"))
    await asyncio.sleep(2)

    # 5. Assistant finishes speaking -> 5000ms Follow-Up Hot-Mic Window
    logging.info("--- Step 5: Follow-up hot mic active for 5000ms ---")
    for remaining_sec in range(5, 0, -1):
        logging.info(f"Hot mic active: {remaining_sec}s remaining (no wake word required)")
        await asyncio.sleep(1)

    # 6. Timeout -> Idle
    logging.info("--- Step 6: Follow-up expired -> Idle roam ---")
    await broadcast_event(generate_ui_animation("idle", 960, 540))

async def handle_mock_connection(reader, writer):
    logging.info("Client connected to mock server!")
    CONNECTED_CLIENTS.add(writer)
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            msg = line.decode().strip()
            logging.info(f"Received from client: {msg}")
    except Exception as e:
        logging.warning(f"Client disconnected: {e}")
    finally:
        CONNECTED_CLIENTS.discard(writer)
        writer.close()

async def main():
    server = await asyncio.start_server(handle_mock_connection, HOST, PORT)
    logging.info(f"Mock server running on {HOST}:{PORT}")
    sim_task = asyncio.create_task(mock_interaction_scenario())
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
