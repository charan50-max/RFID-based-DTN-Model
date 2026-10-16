"""Local RFID DTN + TinyML dashboard.

Auto-connects to the selected Arduino serial port, automatically requests SYNC
when the Arduino is detected/reconnected, and lets the user ACK each queued
record individually. Run: python dashboard.py --port COM3
"""
from __future__ import annotations
import argparse
import threading
import time
from collections import deque
from datetime import datetime
from flask import Flask, jsonify, render_template, request
import serial
from serial.tools import list_ports

app = Flask(__name__, template_folder="templates", static_folder="static")
STATE_LOCK = threading.RLock()
SERIAL_LOCK = threading.RLock()
CONNECT_LOCK = threading.Lock()
SERIAL_PORT = None
SERIAL_NAME = None
SERIAL_BAUD = 9600
DESIRED_PORT = None
DESIRED_BAUD = 9600
READER_STARTED = False
RECONNECT_STARTED = False
STOP_EVENT = threading.Event()
SYNC_ACTIVE = False
SYNC_MESSAGES = []
PENDING_ACKS = set()
LAST_AUTO_SYNC_AT = 0.0

STATE = {
    "connected": False, "port": None, "baud": 9600, "queue": {},
    "delivered": 0, "acknowledged_total": 0, "scanned_total": 0,
    "events": deque(maxlen=120), "last_line": "", "last_seen": None,
    "syncing": False, "last_sync_total": 0, "error": None,
    "arduino_status": "Waiting for connection", "auto_sync_count": 0,
}

def now_text(): return datetime.now().strftime("%H:%M:%S")

def add_event(message, kind="info"):
    with STATE_LOCK:
        STATE["events"].appendleft({"time": now_text(), "message": str(message), "kind": kind})

def priority_rank(priority):
    return {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2}.get(str(priority).upper(), 3)

def set_connection_error(message):
    global SYNC_ACTIVE, SYNC_MESSAGES
    # A disconnect during SYNC must not leave the global sync flag stuck True;
    # otherwise the next STATUS response will never trigger automatic SYNC.
    SYNC_ACTIVE = False
    SYNC_MESSAGES = []
    with STATE_LOCK:
        was_connected = STATE["connected"]
        STATE["connected"] = False
        STATE["error"] = str(message)
        STATE["arduino_status"] = "Connection disrupted"
        STATE["syncing"] = False
    if was_connected:
        add_event(f"Connection disrupted: {message}", "error")

def parse_record(parts):
    # Current firmware protocol: MSG/QUEUED,seq,uid,priority,severity,deadline,event_type
    if len(parts) < 7: return None
    try:
        return {"seq": int(parts[1]), "uid": parts[2].strip().upper(),
                "priority": parts[3].strip().upper(), "severity": int(parts[4]),
                "deadline": int(parts[5]), "event_type": int(parts[6])}
    except (ValueError, IndexError): return None

def write_command(command):
    with SERIAL_LOCK:
        port = SERIAL_PORT
        if port is None or not port.is_open: return False
        port.write((command.strip() + "\n").encode("ascii"))
        port.flush()
        return True

def request_sync(automatic=False):
    global SYNC_ACTIVE, SYNC_MESSAGES, LAST_AUTO_SYNC_AT
    with SERIAL_LOCK:
        port = SERIAL_PORT
        if port is None or not port.is_open: return False
        SYNC_ACTIVE = True
        SYNC_MESSAGES = []
        with STATE_LOCK: STATE["syncing"] = True
        port.write(b"SYNC\n")
        port.flush()
    if automatic:
        LAST_AUTO_SYNC_AT = time.monotonic()
        with STATE_LOCK: STATE["auto_sync_count"] += 1
        add_event("Arduino reconnected — automatic SYNC started", "success")
    else:
        add_event("SYNC started — waiting for Arduino queue", "info")
    return True

def handle_line(line):
    global SYNC_ACTIVE, SYNC_MESSAGES
    if not line: return
    with STATE_LOCK:
        STATE["last_line"] = line
        STATE["last_seen"] = now_text()
        if line.startswith("ERROR,"): STATE["error"] = line
    parts = line.split(",")
    prefix = parts[0].strip().upper()

    if line == "RFID_DTN_TINYML_READY":
        with STATE_LOCK: STATE["arduino_status"] = "Arduino ready"
        add_event("Arduino firmware is ready", "success")
        return

    if prefix == "QUEUED":
        record = parse_record(parts)
        if record:
            with STATE_LOCK:
                STATE["queue"][str(record["seq"])] = record
                STATE["scanned_total"] += 1
                STATE["arduino_status"] = "Scanning / queueing"
            add_event(f"Tag {record['uid']} queued as {record['priority']} (seq {record['seq']})", "queue")
        return

    if prefix == "STATUS":
        with STATE_LOCK: STATE["arduino_status"] = line
        # Status response is our signal that the board has finished booting.
        # Automatically sync after each newly opened serial connection.
        if time.monotonic() - LAST_AUTO_SYNC_AT > 1.0 and not SYNC_ACTIVE:
            try: request_sync(automatic=True)
            except Exception as exc: add_event(f"Automatic SYNC failed: {exc}", "error")
        return

    if line == "BEGIN":
        SYNC_ACTIVE = True
        SYNC_MESSAGES = []
        with STATE_LOCK: STATE["syncing"] = True
        add_event("Arduino is transferring its stored queue", "info")
        return

    if line == "END":
        SYNC_ACTIVE = False
        with STATE_LOCK:
            STATE["syncing"] = False
            STATE["last_sync_total"] = len(SYNC_MESSAGES)
            for record in SYNC_MESSAGES:
                STATE["queue"][str(record["seq"])] = record
        add_event(f"SYNC complete — {len(SYNC_MESSAGES)} record(s) available for ACK", "success" if SYNC_MESSAGES else "info")
        if not SYNC_MESSAGES: add_event("Arduino queue is empty", "info")
        return

    if prefix == "MSG":
        record = parse_record(parts)
        if record:
            if SYNC_ACTIVE: SYNC_MESSAGES.append(record)
            with STATE_LOCK: STATE["queue"][str(record["seq"])] = record
        return

    if prefix == "ACKED" and len(parts) >= 2:
        try: seq = int(parts[1])
        except ValueError: return
        PENDING_ACKS.discard(seq)
        with STATE_LOCK:
            STATE["queue"].pop(str(seq), None)
            STATE["delivered"] += 1
            STATE["acknowledged_total"] += 1
        add_event(f"ACK confirmed — message {seq} removed from Arduino EEPROM", "success")
        return

    if prefix == "FULL": add_event("EEPROM queue is full — cannot store more messages", "error")
    elif prefix == "CLEARED":
        add_event(line.replace(",", ": "), "warning")
        with STATE_LOCK: STATE["queue"].clear()
    elif prefix == "ERROR": add_event(line.replace(",", ": "), "error")
    elif prefix.startswith("DEBUG_"): return

def serial_reader():
    global SERIAL_PORT
    while not STOP_EVENT.is_set():
        port = SERIAL_PORT
        if port is None:
            time.sleep(0.15); continue
        try:
            if not port.is_open:
                time.sleep(0.15); continue
            raw = port.readline()
            if raw: handle_line(raw.decode("utf-8", errors="replace").strip())
        except (serial.SerialException, OSError) as exc:
            try:
                with SERIAL_LOCK:
                    if SERIAL_PORT is not None:
                        SERIAL_PORT.close()
                    SERIAL_PORT = None
            except Exception: SERIAL_PORT = None
            set_connection_error(exc)
            time.sleep(0.4)
        except Exception as exc:
            add_event(f"Serial reader warning: {exc}", "warning")
            time.sleep(0.2)

def connect_serial(port_name, baud, automatic=False):
    global SERIAL_PORT, SERIAL_NAME, SERIAL_BAUD, READER_STARTED, SYNC_ACTIVE, SYNC_MESSAGES
    with CONNECT_LOCK:
        # Start every new serial session with a clean sync state.
        SYNC_ACTIVE = False
        SYNC_MESSAGES = []
        with STATE_LOCK:
            STATE["syncing"] = False
        with SERIAL_LOCK:
            if SERIAL_PORT is not None:
                try: SERIAL_PORT.close()
                except Exception: pass
                SERIAL_PORT = None
            try:
                port = serial.Serial(port_name, baudrate=baud, timeout=0.15, write_timeout=1)
                SERIAL_PORT = port
                SERIAL_NAME, SERIAL_BAUD = port_name, baud
                time.sleep(1.7)  # Uno commonly resets when serial is opened.
                try: port.reset_input_buffer()
                except Exception: pass
            except Exception as exc:
                SERIAL_PORT = None
                set_connection_error(exc)
                return False, str(exc)
        with STATE_LOCK:
            STATE["connected"] = True; STATE["port"] = port_name; STATE["baud"] = baud
            STATE["error"] = None; STATE["arduino_status"] = "Connected — checking device"
        add_event(("Automatically reconnected" if automatic else "Connected") + f" to Arduino on {port_name}", "success")
        if not READER_STARTED:
            threading.Thread(target=serial_reader, daemon=True, name="serial-reader").start()
            READER_STARTED = True
        time.sleep(0.15)
        try:
            if not write_command("STATUS"):
                raise serial.SerialException("Serial port closed before STATUS could be sent")
        except Exception as exc:
            set_connection_error(exc)
            return False, str(exc)
        return True, "Connected; checking Arduino status and starting automatic SYNC"

def reconnect_monitor():
    while not STOP_EVENT.is_set():
        if DESIRED_PORT and SERIAL_PORT is None:
            try: connect_serial(DESIRED_PORT, DESIRED_BAUD, automatic=True)
            except Exception: pass
        time.sleep(1.5)

@app.route("/")
def index(): return render_template("dashboard.html")

@app.get("/api/state")
def api_state():
    with STATE_LOCK:
        queue = sorted(STATE["queue"].values(), key=lambda r: (priority_rank(r["priority"]), r["seq"]))
        return jsonify({**{k: v for k, v in STATE.items() if k != "events"}, "queue": queue,
                       "queue_count": len(queue), "events": list(STATE["events"]),
                       "pending_acks": sorted(PENDING_ACKS),
                       "available_ports": [{"device": p.device, "description": p.description} for p in list_ports.comports()]})

@app.post("/api/connect")
def api_connect():
    global DESIRED_PORT, DESIRED_BAUD
    data = request.get_json(silent=True) or {}
    port_name = str(data.get("port", "")).strip()
    try: baud = int(data.get("baud", 9600))
    except (TypeError, ValueError): baud = 9600
    if not port_name: return jsonify({"ok": False, "error": "Choose a COM port first."}), 400
    DESIRED_PORT, DESIRED_BAUD = port_name, baud
    ok, message = connect_serial(port_name, baud)
    return jsonify({"ok": ok, "message": message, "error": None if ok else message}), 200 if ok else 400

@app.post("/api/disconnect")
def api_disconnect():
    global SERIAL_PORT, DESIRED_PORT
    DESIRED_PORT = None
    with SERIAL_LOCK:
        if SERIAL_PORT is not None:
            try: SERIAL_PORT.close()
            except Exception: pass
            SERIAL_PORT = None
    with STATE_LOCK:
        STATE["connected"] = False; STATE["arduino_status"] = "Disconnected by user"; STATE["syncing"] = False
    add_event("Serial connection closed by dashboard; auto-reconnect paused", "warning")
    return jsonify({"ok": True})

@app.post("/api/ack")
def api_ack():
    data = request.get_json(silent=True) or {}
    try: seq = int(data.get("seq"))
    except (TypeError, ValueError): return jsonify({"ok": False, "error": "Invalid sequence number."}), 400
    with STATE_LOCK:
        if str(seq) not in STATE["queue"]: return jsonify({"ok": False, "error": "This message is not in the dashboard queue."}), 404
    if seq in PENDING_ACKS: return jsonify({"ok": False, "error": "ACK already pending for this message."}), 409
    try:
        if not write_command(f"ACK,{seq}"): return jsonify({"ok": False, "error": "Arduino is disconnected."}), 400
        PENDING_ACKS.add(seq)
        add_event(f"ACK sent for message {seq} — waiting for Arduino confirmation", "info")
        return jsonify({"ok": True, "message": f"ACK sent for seq {seq}."})
    except Exception as exc:
        set_connection_error(exc)
        return jsonify({"ok": False, "error": str(exc)}), 500

@app.post("/api/clear-events")
def api_clear_events():
    with STATE_LOCK: STATE["events"].clear()
    return jsonify({"ok": True})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RFID DTN TinyML local dashboard")
    parser.add_argument("--port", default="COM3", help="Arduino serial port, e.g. COM3")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--no-auto-connect", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=5000)
    args = parser.parse_args()
    if not args.no_auto_connect:
        DESIRED_PORT, DESIRED_BAUD = args.port, args.baud
        ok, message = connect_serial(args.port, args.baud)
        if not ok: print(f"[dashboard] Could not connect to {args.port}: {message}; automatic retry will continue")
        threading.Thread(target=reconnect_monitor, daemon=True, name="serial-reconnect-monitor").start()
        RECONNECT_STARTED = True
    else:
        print("[dashboard] Auto-connect disabled; use Connect device in the browser.")
    print(f"[dashboard] Open http://127.0.0.1:{args.web_port}")
    app.run(host=args.host, port=args.web_port, debug=False, threaded=True, use_reloader=False)
