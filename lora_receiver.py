"""Read JSON messages from the LoRa serial port and forward them to FastAPI."""

import json
from datetime import datetime
import time

import requests
import serial


SERIAL_PORT = "/dev/serial0"
BAUD_RATE = 9600
FASTAPI_URL = "http://127.0.0.1:8000/heartbeat"


def extract_json_messages(line: str) -> list[dict]:
    """Extract all JSON objects from a serial read.

    LoRa packets can arrive back-to-back, for example:
    {...}{...}{...}
    """
    messages = []
    decoder = json.JSONDecoder()
    position = 0

    while position < len(line):
        start = line.find("{", position)
        if start == -1:
            break

        try:
            message, end = decoder.raw_decode(line, start)
        except json.JSONDecodeError:
            # Skip a damaged opening brace and look for the next packet.
            position = start + 1
            continue

        if isinstance(message, dict):
            messages.append(message)
        position = end

    return messages


def forward_message(message: dict) -> None:
    """Convert one decoded LoRa message into a heartbeat request."""
    try:
        payload = {
            "device_id": message.get("node", "unknown"),
            "event_type": message.get("event", "heartbeat"),
            "count": message.get("count", 1),
            "raw_event": message,
        }

        response = requests.post(FASTAPI_URL, json=payload, timeout=3)
        print("POST status:", response.status_code)
    except requests.exceptions.ConnectionError:
        print("FastAPI not running or unreachable")
    except requests.RequestException as exc:
        print("HTTP error:", exc)
    except Exception as exc:
        # Keep the receiver alive if one malformed message causes an error.
        print("Error:", exc)


def main() -> None:
    print("Pi LoRa receiver started...")

    while True:
        try:
            with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as serial_port:
                while True:
                    line = serial_port.readline().decode(errors="replace").strip()
                    if not line:
                        continue

                    received_at = datetime.now().isoformat(timespec="seconds")
                    print(f"[{received_at}] LoRa:", line)

                    messages = extract_json_messages(line)
                    if not messages:
                        # repr() exposes hidden serial characters.
                        print(f"No complete JSON packet found: {line!r}")
                        continue

                    for message in messages:
                        forward_message(message)
        except serial.SerialException as exc:
            print(f"Serial port error: {exc}. Retrying in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    main()
