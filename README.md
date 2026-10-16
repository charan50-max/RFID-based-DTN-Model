# RFID-Based Delay-Tolerant Networking (DTN) with TinyML

A standalone hardware prototype demonstrating **edge-side priority classification, store-carry-forward message handling, and acknowledged delivery** using an Arduino Uno, an RC522 RFID reader, and a local Python dashboard.

> **Important scope note:** This is a small educational prototype inspired by delay-tolerant networking (DTN). It is **not** integrated with the separate Space DTN project and does not implement the paper's exact RFID air interface. The RC522 uses 13.56 MHz RFID/NFC (MFRC522-compatible cards/tags), whereas EPC Class 1 Gen 2 systems use a different RFID standard.

## Contents

- [1. Problem statement](#1-problem-statement)
- [2. Project idea in simple words](#2-project-idea-in-simple-words)
- [3. Objectives](#3-objectives)
- [4. System architecture](#4-system-architecture)
- [5. How the project works](#5-how-the-project-works)
- [6. TinyML and RFID data](#6-tinyml-and-rfid-data)
- [7. Hardware required](#7-hardware-required)
- [8. Wiring](#8-wiring)
- [9. Software and folder structure](#9-software-and-folder-structure)
- [10. Dashboard features](#10-dashboard-features)
- [11. Serial communication protocol](#11-serial-communication-protocol)
- [12. Queue storage and limitations](#12-queue-storage-and-limitations)
- [13. Testing the demonstration](#13-testing-the-demonstration)
- [14. Troubleshooting](#14-troubleshooting)
- [15. Limitations and future improvements](#15-limitations-and-future-improvements)
- [16. Run the project after cloning from GitHub](#16-run-the-project-after-cloning-from-github)

## 1. Problem statement

In a network with intermittent connectivity, a device may need to collect information when a computer or network is unavailable. If it attempts to send every message immediately, messages may be lost or delayed when the connection disappears.

This project demonstrates a local solution: the Arduino reads an RFID tag, assigns a demo event profile, runs a compact priority model on the device, and stores the resulting record in EEPROM. When the laptop reconnects, the dashboard automatically requests the stored records. The user can acknowledge each record individually, and the Arduino removes a record only after it processes the corresponding ACK command.

**In one sentence:** classify important messages at the edge, store them during disconnection, and deliver them after reconnection in priority order with acknowledgement.

## 2. Project idea in simple words

Imagine a field device collecting three kinds of events:

- **Critical alert** — must be handled first.
- **Equipment status** — important, but less urgent.
- **Routine telemetry** — can wait.

The Arduino and RFID reader act as the edge device. The laptop is the receiving station. While the laptop's USB serial connection is unavailable, the Arduino can continue reading tags and storing compact records, provided the Arduino and reader remain powered. When USB serial connectivity returns, the dashboard requests the stored queue and displays the messages from highest to lowest priority. The operator presses **ACK** for a message after reviewing it; the dashboard does not acknowledge messages automatically.

This models the DTN principle commonly called **store, carry, and forward**:

1. **Store:** retain a record locally when the receiver is unavailable.
2. **Carry:** keep the record on the device while disconnected.
3. **Forward:** transfer the record after connectivity returns.
4. **Acknowledge:** confirm receipt so the sender can remove the stored record.

## 3. Objectives

- Read RFID tag UIDs using an RC522 reader.
- Associate each known UID with a simulated event profile.
- Run embedded priority inference on the Arduino using `TinyMLModel.h`.
- Store queued records in Arduino EEPROM so they can survive a reset or power interruption, subject to EEPROM capacity and firmware behavior.
- Demonstrate laptop disconnection and reconnection without losing queued records.
- Automatically SYNC the Arduino queue when the dashboard establishes or restores the serial connection.
- Show queue counts, priority distribution, events, and individual ACK controls in a local dashboard.
- Remove a queued record only after the Arduino confirms its ACK.

## 4. System architecture

```text
        RFID card/tag
             |
             v
        RC522 reader
             |
             v
        Arduino Uno
        |           |
        |           +--> TinyML inference (TinyMLModel.h)
        |                     |
        |                     v
        |               Priority class
        |                     |
        +---------------------v
                   EEPROM queue
                         |
             USB serial reconnects
                         |
                         v
              Python / Flask dashboard
                         |
             Automatic STATUS -> SYNC
                         |
                         v
              Queue shown in browser
                         |
                   User presses ACK
                         |
                         v
              Arduino confirms ACKED
              and removes the record
```

The dashboard runs locally on the laptop. It does not require a cloud server or an external database. The Arduino performs the classification; the dashboard displays and coordinates transfer/acknowledgement.

## 5. How the project works

### Phase A — Scan and classify

1. The RC522 detects an RFID card or tag.
2. The Arduino reads its UID.
3. The firmware maps the UID to three **demo features**: severity, deadline, and event type.
4. `predictPriority()` from `TinyMLModel.h` evaluates those features and returns a priority class.
5. The Arduino stores a record containing a sequence number, UID, priority, and feature values in EEPROM.
6. The serial output may show a `QUEUED` record while the laptop is connected. If USB is disconnected, the record is stored on the Arduino and can be transferred later.

### Phase B — Operate offline

1. Close the dashboard's serial connection or disconnect the USB data cable.
2. Keep the Arduino powered using a suitable independent power source.
3. Scan tags. The Arduino continues classification and EEPROM queueing without the laptop.
4. If the Arduino loses all power, it cannot scan during that period. EEPROM can preserve previously written records, but it does not provide power to the device.

### Phase C — Reconnect and synchronize

1. Reconnect the Arduino's USB cable to the laptop.
2. The dashboard reconnects to the configured serial port and sends `STATUS`.
3. When it receives a `STATUS` response, it sends `SYNC` automatically.
4. The Arduino sends the stored queue between `BEGIN` and `END` markers.
5. The dashboard displays records ordered by priority: `CRITICAL`, then `HIGH`, then `NORMAL` (sequence number breaks ties).

### Phase D — Acknowledge delivery

1. Review a record in the dashboard.
2. Press **ACK** for that record.
3. The dashboard sends `ACK,<seq>` to the Arduino.
4. The Arduino should respond with `ACKED,<seq>` after processing the acknowledgement and removing that record from EEPROM.
5. The dashboard removes the record from its pending view only after it receives the confirmation.

SYNC and ACK are separate operations: **SYNC is automatic; ACK is manual.** If an acknowledgement is not confirmed, the record should remain available for retry rather than being treated as delivered.

## 6. TinyML and RFID data

An RFID UID is an identifier. It does **not** inherently contain severity, urgency, or sensor readings. This prototype uses a firmware mapping to assign demo features to known UIDs, then passes those features to the embedded model.

The feature meanings used by the sketch are:

| Feature | Values | Meaning |
|---|---|---|
| `severity` | `0`, `1`, `2` | Low, medium, high |
| `deadline` | `0`, `1`, `2` | Immediate, soon, later |
| `event_type` | `0`, `1`, `2` | Routine telemetry, equipment status, critical alert |

The currently configured known-tag profiles are intended to represent:

| Example event | Severity | Deadline | Event type | Expected interpretation |
|---|---:|---:|---:|---|
| Critical alert | 2 | 0 | 2 | Urgent/highest priority |
| Equipment status | 1 | 1 | 1 | Medium urgency |
| Routine telemetry | 0 | 2 | 0 | Can wait |

The exact class numbers and labels depend on the currently uploaded `TinyMLModel.h` and the firmware's label mapping. Verify them in the Arduino Serial Monitor using the `DEBUG_FEATURES` and `DEBUG_MODEL_CLASS` lines if debug output is enabled. Do not assume every RFID tag is automatically a critical event: if all tags are classified as critical, check that the UID mapping and model's feature/label mapping agree.

For this prototype, the model's inference executes on the Arduino. The dashboard does not run a second copy of the model. The feature values are simulated from UID mappings; this is not yet a sensor-based risk classifier.

## 7. Hardware required

- Arduino Uno (ATmega328P-based board)
- RC522 / MFRC522 RFID reader module
- Compatible 13.56 MHz RFID cards or tags
- USB cable for programming and serial communication
- Laptop running Windows, Linux, or macOS
- Optional independent power supply for offline operation
- Breadboard and jumper wires

## 8. Wiring

Use the following wiring for an Arduino Uno and RC522:

| RC522 pin | Arduino Uno pin |
|---|---|
| SDA / SS | D10 |
| SCK | D13 |
| MOSI | D11 |
| MISO | D12 |
| IRQ | Not connected |
| GND | GND |
| RST | D9 |
| 3.3V | 3.3V |

**Electrical safety:** the RC522 module is a 3.3 V device. Connect its power pin to **3.3 V, not 5 V**. Its signal pins are also 3.3 V logic; a bare RC522 module is not necessarily 5 V tolerant. Use appropriate level shifting if required by your particular module.

### Powering the Arduino while USB is disconnected

The Arduino and RC522 must remain powered for offline scanning. Use a suitable, regulated supply connected through a supported Arduino power input and verify the board's power requirements. The 12 V, 0.8 A adapter previously shown may be within the Uno's barrel-jack input range, but 12 V can cause the onboard regulator to heat up, especially with additional loads. Check the board's documentation and polarity before using it, and stop if the regulator or board becomes unusually hot. Do **not** connect a 12 V adapter directly to the 5 V or 3.3 V pin. Keep the RC522 on 3.3 V.

## 9. Software and folder structure

The project has two parts: Arduino firmware and the local dashboard. A typical GitHub repository layout is:

```text
rfid-dtn-tinyml-demo/
├── README.md
├── RFID_DTN_TinyML.ino
├── TinyMLModel.h
├── dashboard.py
├── requirements-dashboard.txt
└── templates/
    └── dashboard.html
```

Your repository may contain additional files, such as training scripts, generated datasets, metrics, or screenshots. Keep the Arduino sketch and `TinyMLModel.h` together in the Arduino sketch folder so the `#include "TinyMLModel.h"` statement resolves. Keep `dashboard.py`, `requirements-dashboard.txt`, and the `templates/` directory together for the dashboard.

### Arduino firmware

- `RFID_DTN_TinyML.ino`: reads RFID tags, assigns demo features, calls the model, writes records to EEPROM, handles serial commands, and removes records when acknowledged.
- `TinyMLModel.h`: contains the compact embedded inference logic/model called by the sketch.

### Dashboard

- `dashboard.py`: Flask web server, serial connection management, protocol parser, automatic reconnect/SYNC logic, queue state, and ACK handling.
- `templates/dashboard.html`: dashboard layout, styling, priority bars, queue table, device status, and event timeline.
- `requirements-dashboard.txt`: Python packages needed to run the dashboard (`Flask` and `pyserial`).

## 10. Dashboard features

- **Local-only interface:** opens in a browser at `http://127.0.0.1:5000`.
- **Connection status:** green for connected and red for disconnected.
- **System overview:** current visible queue count, confirmed ACK/delivery count, observed queued tags, and last SYNC batch size.
- **Priority distribution:** count of critical, high, and normal records.
- **Queued messages:** sequence number, UID, priority, feature values, and an individual ACK action.
- **Event timeline:** connection changes, automatic SYNC, records received, and ACK confirmations.
- **Automatic SYNC:** starts after the dashboard connects/reconnects and receives a valid status response.
- **Manual ACK:** each message remains pending until the Arduino confirms `ACKED,<seq>`.

The dashboard's counters are runtime dashboard statistics, not a permanent analytics database. Restarting the Python process may reset dashboard-only counters and event history. Records still in Arduino EEPROM are the durable queue source.

## 11. Serial communication protocol

The dashboard and firmware must use matching command and response formats. The current dashboard expects the following protocol:

| Direction | Line | Purpose |
|---|---|---|
| Arduino → dashboard | `RFID_DTN_TINYML_READY` | Firmware startup banner |
| Dashboard → Arduino | `STATUS` | Ask for current device/queue status |
| Arduino → dashboard | `STATUS,...` | Status response; triggers automatic SYNC |
| Dashboard → Arduino | `SYNC` | Request queued records |
| Arduino → dashboard | `BEGIN` | Start of queue transfer |
| Arduino → dashboard | `MSG,<seq>,<uid>,<priority>,<severity>,<deadline>,<event_type>` | A stored queue record |
| Arduino → dashboard | `END` | End of queue transfer |
| Arduino → dashboard | `QUEUED,<seq>,<uid>,<priority>,<severity>,<deadline>,<event_type>` | A newly scanned tag was queued while connected |
| Dashboard → Arduino | `ACK,<seq>` | Acknowledge one sequence number |
| Arduino → dashboard | `ACKED,<seq>` | ACK accepted and record removed |

Each command is sent as a line terminated by a newline. Serial baud rate defaults to **9600** in the current firmware/dashboard configuration. If your sketch uses different wording or field order, update the parser or firmware so both sides agree exactly.

**Serial-port rule:** only one program can normally own the Arduino COM port at a time. Close Arduino IDE Serial Monitor and Serial Plotter before starting the Python dashboard. If the dashboard reports `PermissionError(13, 'Access is denied')`, another program may still be using the port.

## 12. Queue storage and limitations

- Arduino Uno EEPROM is small (1 KB). The sketch's fixed record size and `SLOT_COUNT` determine how many records can be queued.
- When the queue is full, the firmware should report `FULL,EEPROM_QUEUE_FULL`; scan fewer tags or acknowledge records to free space.
- Repeatedly writing the same data to EEPROM can contribute to memory wear. The firmware should write only when necessary and avoid unnecessary queue rewrites.
- A USB disconnect does not stop the Arduino if it has a separate power source. A total power loss stops scanning, though already committed EEPROM records may remain.
- The dashboard can only display records after serial connectivity returns and SYNC succeeds.
- The current tag-to-feature mapping is a demonstration mechanism. It is not proof that the RFID tag itself carries event severity.
- This is a local prototype, not a production DTN implementation: it does not include radio links between mobile nodes, cryptographic authentication, encryption, or a distributed routing protocol.

## 13. Testing the demonstration

### Test 1 — Basic RFID scan

1. Upload the sketch to the Uno.
2. Open Serial Monitor at 9600 baud.
3. Scan a known tag.
4. Confirm that the UID and queued record appear.
5. If debug is enabled, inspect `DEBUG_UID`, `DEBUG_FEATURES`, and `DEBUG_MODEL_CLASS` to confirm that the expected profile reached the model.

### Test 2 — Offline queueing

1. Ensure the Arduino has independent power before disconnecting USB from the laptop.
2. Disconnect the USB data cable.
3. Scan the known tags.
4. Reconnect USB. Avoid opening Serial Monitor if you intend to use the dashboard.
5. Let the dashboard reconnect and issue automatic SYNC.
6. Confirm the stored records appear in the dashboard.

### Test 3 — Manual acknowledgement

1. With records visible, click ACK on one record.
2. Confirm the event timeline shows the ACK was sent and then confirmed.
3. Confirm that the acknowledged record disappears from the pending queue.
4. Click ACK on other records individually.
5. Run another SYNC to check that acknowledged records no longer appear in EEPROM.

### Test 4 — Priority order

Queue at least one critical, one high, and one normal profile. After SYNC, check that the dashboard lists records in the order `CRITICAL`, `HIGH`, `NORMAL`. If they all show the same priority, inspect the firmware feature mapping and model output before changing the dashboard.

## 14. Troubleshooting

### Dashboard says COM port is busy / access denied

- Close Arduino IDE Serial Monitor and Serial Plotter.
- Close any second Python script connected to the same COM port.
- Check **Tools → Port** in Arduino IDE or Device Manager to verify the correct port.
- Restart the dashboard after the port is released.

### Dashboard connects but does not SYNC

- Confirm the dashboard is running with the correct port and 9600 baud.
- Confirm the Arduino sketch prints `RFID_DTN_TINYML_READY` after reset.
- Confirm `STATUS` produces a line beginning with `STATUS`.
- Confirm `SYNC` produces `BEGIN`, zero or more `MSG,...` lines, and `END`.
- Check the dashboard event timeline for a serial error or automatic-SYNC event.
- Do not keep Serial Monitor open while the dashboard is using the port.
- Ensure the firmware protocol matches the formats in [Serial communication protocol](#11-serial-communication-protocol).

### Arduino keeps restarting on connection

The Uno commonly resets when the serial port is opened. The dashboard waits briefly after opening the port before requesting status. Startup banners may appear more than once; this alone does not necessarily indicate a fault. Ensure the automatic SYNC begins after the board's status response.

### Every tag is classified as CRITICAL

- Read the `DEBUG_UID` and verify the UID matches the mapping exactly.
- Compare `DEBUG_FEATURES` with the expected feature profile.
- Check the class returned by `predictPriority()` and how the sketch maps class numbers to text labels.
- Check that the version of `TinyMLModel.h` matches the current sketch.
- Re-upload the correct sketch after making changes.

### Queue reports FULL

The fixed EEPROM queue has reached its capacity. Connect the dashboard, SYNC the records, and ACK records that have been received and reviewed. Do not assume the Uno can store an unlimited number of messages.

### No RFID scan is detected

- Verify the wiring table, especially SS/SDA, RST, SCK, MOSI, MISO, GND, and 3.3 V.
- Ensure the card/tag is compatible with the RC522.
- Check the SPI and MFRC522 libraries and board selection.
- Avoid powering the RC522 from 5 V.

## 15. Limitations and future improvements

Possible future improvements include:

- Use actual sensor readings instead of fixed UID-to-feature demo profiles.
- Train and validate the priority classifier on a documented dataset and report precision, recall, confusion matrix, model size, and inference time.
- Add queue integrity checks and more robust recovery for interrupted transfers.
- Add a persistent dashboard database for long-term delivery metrics and audit history.
- Add message authentication and integrity protection before treating messages as trusted.
- Use a board with more RAM/flash/EEPROM or external non-volatile storage for larger queues.
- Add multiple communicating edge nodes and a real intermittent network link to demonstrate multi-hop DTN behavior.
- If the project is extended toward a specific RFID research paper, clearly document differences in RFID frequency, protocol, tag type, and memory-write mechanism.

## 16. Run the project after cloning from GitHub

This guide assumes the repository contains the Arduino sketch, `TinyMLModel.h`, `dashboard.py`, `requirements-dashboard.txt`, and `templates/dashboard.html` as described above.

### A. Install prerequisites

1. Install **Git**.
2. Install **Python 3.10 or newer**. During Windows installation, enable the option to add Python to PATH if offered.
3. Install **Arduino IDE 2.x**.
4. Connect the Arduino Uno and RC522 according to the wiring table.

### B. Clone the repository

Open PowerShell and run the repository's actual GitHub URL in place of `YOUR-USERNAME/YOUR-REPOSITORY`:

```powershell
git clone https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
cd YOUR-REPOSITORY
```

If your repository places the files in a subfolder, `cd` into the folder that contains `dashboard.py` and `requirements-dashboard.txt` before continuing.

### C. Set up the Python dashboard

From the directory containing `dashboard.py`, run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dashboard.txt
```

If PowerShell blocks virtual-environment activation, you can run the Python executable directly without activating it:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dashboard.txt
```

### D. Upload the Arduino firmware

1. Open Arduino IDE.
2. Open `RFID_DTN_TinyML.ino` from the sketch folder. Arduino IDE expects the `.ino` file to be inside a folder with the same name; if necessary, create a folder named `RFID_DTN_TinyML` and put `RFID_DTN_TinyML.ino` and `TinyMLModel.h` inside it.
3. Ensure `TinyMLModel.h` is in the same sketch folder.
4. In Library Manager, install **MFRC522** by Miguel Balboa if it is not already installed.
5. Select **Arduino Uno** under Tools → Board.
6. Select the correct serial port under Tools → Port.
7. Click **Verify** to compile, then **Upload**.
8. If you need to inspect startup or diagnostic messages, open Serial Monitor at **9600 baud**, test, and then close Serial Monitor before running the dashboard.

### E. Start the dashboard

Find the correct COM port in Arduino IDE's Tools → Port or Windows Device Manager. Replace `COM3` with that port if needed:

```powershell
python dashboard.py --port COM3
```

Then open this address in your browser:

```text
http://127.0.0.1:5000
```

Choose the correct port and baud rate in the interface if the dashboard offers those controls. Leave the PowerShell window running while using the dashboard.

### F. Run the complete offline demonstration

1. Start the dashboard and verify that the Arduino shows as connected.
2. Confirm automatic SYNC completes and the current EEPROM queue appears.
3. To demonstrate disconnection, keep the Arduino powered independently and disconnect its USB data connection from the laptop.
4. Scan RFID tags while the laptop is disconnected.
5. Reconnect USB. The dashboard should reconnect and automatically SYNC the stored records.
6. Review the priority order and manually ACK records one by one.
7. Confirm each record is removed from the pending queue only after the Arduino returns `ACKED,<seq>`.

### G. Before committing or sharing the repository

- Include the README, Arduino `.ino`, `TinyMLModel.h`, dashboard Python file, requirements file, and `templates/dashboard.html`.
- Do not commit `.venv/`, Python `__pycache__/`, or generated temporary files.
- Do not include private credentials or unrelated personal files.
- Test the documented commands from a fresh clone so the setup guide remains reproducible.

---

## Summary

This prototype demonstrates how an edge device can classify and queue events locally, continue working while the laptop is disconnected (as long as the device remains powered), synchronize the queue after reconnection, and use explicit acknowledgements to remove delivered records. Its core learning outcomes are RFID integration, embedded inference, EEPROM-backed queueing, serial protocol design, and a local monitoring dashboard.
