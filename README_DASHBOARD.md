# RFID DTN + TinyML Dashboard

A local, minimal dashboard for the existing Arduino Uno + RC522 prototype. It uses the cream/cinnamon/nutmeg palette and green/red connection and ACK indicators.

## Files
- `dashboard.py` — local Flask server and serial bridge
- `templates/dashboard.html` — complete dashboard UI
- `requirements-dashboard.txt` — Python dependencies

## 1. Install dependencies
Open PowerShell in your existing `rfid_dtn_tinyml_demo` folder:

```powershell
python -m pip install -r requirements-dashboard.txt
```

If you prefer not to create the file separately, install directly:

```powershell
python -m pip install flask pyserial
```

## 2. Close Arduino Serial Monitor
Only one program can own COM3 at a time. Close the Serial Monitor in Arduino IDE before connecting from the dashboard.

## 3. Start the dashboard
Copy `dashboard.py`, the `templates` folder, and `requirements-dashboard.txt` into your project folder. Then run:

```powershell
python dashboard.py --port COM3
```

If the dashboard starts but COM3 was busy or the port is different, start without auto-connecting:

```powershell
python dashboard.py --no-auto-connect
```

Open `http://127.0.0.1:5000` in your browser and choose the Arduino COM port.

## 4. Demo flow
1. Keep the Arduino powered. Connect to it from the dashboard.
2. To demonstrate disruption, unplug only the USB data/power cable if you have a separate safe power supply keeping the Uno powered. The dashboard turns red when the serial connection loss is detected.
3. Scan RFID tags. The Arduino runs its TinyML model and stores records in EEPROM. The dashboard cannot see scans while disconnected; that is expected.
4. Reconnect USB, connect again if needed, then click **Sync & deliver queue**.
5. The Arduino sends the queued records in priority order. The dashboard receives the complete batch, then sends `ACK,<seq>` commands one at a time and waits for `ACKED,<seq>` before proceeding.
6. Confirm the event timeline shows green ACK events and the queue count decreases.

## Protocol expectations
This dashboard expects the current firmware serial protocol:
- `STATUS` command, response beginning `STATUS`
- `SYNC` command
- `BEGIN` and `END` around a sync batch
- `MSG,<seq>,<uid>,<priority>,<severity>,<deadline>,<event_type>` records
- `ACK,<seq>` command and `ACKED,<seq>` response
- `QUEUED,<seq>,<uid>,<priority>,<severity>,<deadline>,<event_type>` on tag scans

If your firmware uses a different message format, keep your existing working firmware and adjust the parser in `dashboard.py` rather than replacing the sketch.

## Important notes
- This is a local dashboard; it does not use cloud services.
- The browser polls the local Flask server once per second.
- The dashboard only knows about messages after they reach the laptop. It cannot display new scans live while the laptop is disconnected.
- Arduino Uno EEPROM is small. The firmware's queue capacity remains the limit.
