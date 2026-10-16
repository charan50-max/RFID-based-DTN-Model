"""
RFID DTN + TinyML demonstration.
Simulation: python dtn_demo.py --simulate
Hardware sync: python dtn_demo.py --port COM5   (Windows example)
Install serial support: python -m pip install pyserial
"""
import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "tinyml_model.json"

def predict(features, node):
    if "class" in node:
        return node["class"]
    if features[node["feature"]] <= node["threshold"]:
        return predict(features, node["left"])
    return predict(features, node["right"])

def priority_name(code):
    return {1: "CRITICAL", 2: "HIGH", 3: "NORMAL"}.get(int(code), "UNKNOWN")

def demo_features(tag_name):
    # Simulated event profiles. Real RFID UIDs do not encode these features.
    return {
        "TAG-CRITICAL": ("A1B2C3D0", [2, 0, 2]),
        "TAG-EQUIPMENT": ("A1B2C3D1", [1, 1, 1]),
        "TAG-ROUTINE": ("A1B2C3D2", [0, 2, 0]),
    }[tag_name]

def run_simulation(model_tree):
    queue = []
    seq = 1
    print("\n=== RFID DTN + TinyML: OFFLINE SIMULATION ===")
    print("Laptop link: DISCONNECTED")
    print("Arduino power: ON (simulated)")
    for tag in ["TAG-ROUTINE", "TAG-EQUIPMENT", "TAG-CRITICAL"]:
        uid, features = demo_features(tag)
        pred = predict(features, model_tree)
        record = {
            "seq": seq, "uid": uid, "priority": pred,
            "severity": features[0], "deadline": features[1], "event_type": features[2],
            "delivered": False,
        }
        queue.append(record)
        print(f"\nScanned {tag} | UID={uid}")
        print(f"  Features: severity={features[0]}, deadline={features[1]}, event_type={features[2]}")
        print(f"  TinyML prediction: {priority_name(pred)}")
        print(f"  Stored in simulated persistent queue. Queue size={len(queue)}")
        seq += 1
        time.sleep(0.4)

    print("\nLaptop reconnects...")
    print("Sending queued records in priority order (CRITICAL -> HIGH -> NORMAL).")
    queue.sort(key=lambda r: (r["priority"], r["seq"]))
    for record in queue:
        print(f"DELIVERED seq={record['seq']} UID={record['uid']} priority={priority_name(record['priority'])}")
        record["delivered"] = True
        time.sleep(0.35)
    print(f"\nResult: {sum(r['delivered'] for r in queue)}/{len(queue)} records delivered.")
    print("This simulation runs entirely on the laptop; the Arduino firmware performs the same model inference on hardware.")

def run_hardware(port, baud):
    try:
        import serial
    except ImportError:
        print("pyserial is missing. Install it with: python -m pip install pyserial")
        return
    print(f"Opening {port} at {baud} baud...")
    try:
        with serial.Serial(port, baud, timeout=1) as ser:
            time.sleep(2)  # Uno often resets when serial port opens
            ser.reset_input_buffer()
            ser.write(b"STATUS\n")
            time.sleep(0.3)
            status = []
            while ser.in_waiting:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    status.append(line)
            print("Arduino:", " | ".join(status) if status else "No status line received.")
            input("\nKeep the Arduino connected and press Enter to request queued messages...")
            ser.reset_input_buffer()
            ser.write(b"SYNC\n")
            messages = []
            started = time.time()
            while time.time() - started < 15:
                raw = ser.readline().decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                if raw == "BEGIN":
                    print("\nQueued messages (Arduino sends highest priority first):")
                elif raw == "END":
                    break
                elif raw.startswith("MSG,"):
                    parts = raw.split(",")
                    if len(parts) >= 7:
                        _, seq, uid, priority, severity, deadline, event_type = parts[:7]
                        print(f"  seq={seq} UID={uid} priority={priority} "
                              f"features=(severity={severity}, deadline={deadline}, event_type={event_type})")
                        messages.append((seq, uid, priority))

            # ACK only after the Arduino has finished sending the queue. This avoids
            # overflowing the Uno's small serial receive buffer while it is transmitting.
            acked = 0
            for seq, uid, priority in messages:
                ser.write(f"ACK,{seq}\n".encode("ascii"))
                ack_deadline = time.time() + 2
                while time.time() < ack_deadline:
                    response = ser.readline().decode("utf-8", errors="replace").strip()
                    if response == f"ACKED,{seq}":
                        print(f"  ACK confirmed for seq={seq}")
                        acked += 1
                        break
                    elif response:
                        print("Arduino:", response)

            print(f"\nAcknowledged and removed {acked}/{len(messages)} record(s) from the EEPROM queue.")
            print("If any record was not acknowledged, run SYNC again to retry it.")
    except Exception as exc:
        print("Could not communicate with Arduino:", exc)
        print("Check the COM port, USB cable, 9600 baud, and close Arduino Serial Monitor.")

def main():
    parser = argparse.ArgumentParser(description="RFID DTN TinyML demo")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--simulate", action="store_true", help="run laptop-only end-to-end simulation")
    group.add_argument("--port", help="serial port, e.g. COM5 or /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=9600)
    args = parser.parse_args()
    if not MODEL_PATH.exists():
        print("Model JSON not found. Run: python train_model.py")
        return
    model_data = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    if args.simulate:
        run_simulation(model_data["tree"])
    else:
        run_hardware(args.port, args.baud)

if __name__ == "__main__":
    main()
