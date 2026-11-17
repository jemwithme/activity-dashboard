# Raspberry Pi Elderly Activity Monitor

## ESP32 diagnostics

The sender sketch is `esp32_sender/esp32_sender.ino` (node `LIGHT_NODE_02`,
GPIO34 threshold `2400`). It saves the latest 48 serial lines in flash:
startup/reset information, sensor readings, and transmitted packets with their
local send results. Records survive resets and show boot number and uptime.

To retrieve them, open Arduino Serial Monitor at **115200 baud**, select
**Newline**, and send `logs`. Allow one loop (about five seconds, longer if
the radio times out) for the command to run. Older lines are overwritten
automatically. A sudden loss of power may prevent the final queued lines from
being saved. A local send success does not confirm Pi reception.

This project receives LoRa events through `lora_receiver.py`, stores them in a
local SQLite database, and serves a simple monitoring dashboard with FastAPI.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run

Start the API and dashboard:

```bash
.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Then, in another terminal, start the LoRa serial receiver:

```bash
.venv/bin/python lora_receiver.py
```

Open `http://<raspberry-pi-ip>:8000/` from a browser on the local network.

## Simulate an event

```bash
curl -X POST http://127.0.0.1:8000/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"device_id":"living-room-1","event_type":"power_on","count":1,"raw_event":{"node":"living-room-1","event":"power_on"}}'
```

The SQLite file `events.db` is created automatically beside `app.py`.

## API routes

- `POST /heartbeat`, `POST /event`, or `POST /events` records an event.
- `GET /events` returns recent events.
- `GET /events/latest` returns the latest event from every node.
- `GET /nodes/status` returns dashboard node totals and statuses.
- `GET /status` retains compatibility with the original status route.
- `GET /health` checks the API and SQLite database.

The dashboard watches two conditions:

- `No heartbeat`: the switch has not been on and no heartbeat is arriving.
- `Switch on`: repeated heartbeats show that the switch has remained on.

For either condition, the flag is green for under two minutes, amber from two
through three minutes, and red after three minutes. A `power_off` event resets
the continuous-heartbeat timer.
