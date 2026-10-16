#include <SPI.h>
#include <MFRC522.h>
#include <EEPROM.h>
#include "TinyMLModel.h"

#define SS_PIN 10
#define RST_PIN 9

MFRC522 rfid(SS_PIN, RST_PIN);

const uint8_t SLOT_COUNT = 12;
const uint8_t RECORD_MAGIC = 0xA5;
const int EEPROM_BASE = 0;

struct MessageRecord {
  uint8_t magic;
  uint8_t seq;
  char uid[11];        // Up to 10 UID hex characters plus null terminator
  uint8_t severity;    // 0 low, 1 medium, 2 high
  uint8_t deadline;    // 0 immediate, 1 soon, 2 later
  uint8_t eventType;   // 0 routine, 1 equipment, 2 critical alert
  uint8_t priority;    // 1 critical, 2 high, 3 normal
};

uint8_t nextSeq = 1;
String serialLine = "";
String lastUID = "";
unsigned long lastScanMillis = 0;

int recordAddress(uint8_t slot) {
  return EEPROM_BASE + (int)slot * (int)sizeof(MessageRecord);
}

bool readRecord(uint8_t slot, MessageRecord &record) {
  EEPROM.get(recordAddress(slot), record);
  return record.magic == RECORD_MAGIC;
}

int findFreeSlot() {
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    MessageRecord r;
    EEPROM.get(recordAddress(i), r);
    if (r.magic != RECORD_MAGIC) return i;
  }
  return -1;
}

uint8_t countQueued() {
  uint8_t count = 0;
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    MessageRecord r;
    if (readRecord(i, r)) count++;
  }
  return count;
}

const char* priorityName(uint8_t priority) {
  if (priority == 1) return "CRITICAL";
  if (priority == 2) return "HIGH";
  return "NORMAL";
}

// The UID selects a simulated event profile. The tag itself does not contain
// severity/deadline values; this is an educational prototype.

void assignDemoFeatures(const String &uid, uint8_t features[3]) {
  // Match the known UID prefixes because some cards/readers
  // may report additional trailing UID bytes.

  if (uid.startsWith("132E6129")) {
    // Critical alert
    features[0] = 2;
    features[1] = 0;
    features[2] = 2;

  } else if (uid.startsWith("1DD36FB210")) {
    // Equipment status
    features[0] = 1;
    features[1] = 1;
    features[2] = 1;

  } else if (uid.startsWith("04453E7266")) {
    // Routine telemetry
    features[0] = 0;
    features[1] = 2;
    features[2] = 0;

  } else {
    // Fallback for unrecognized tags
    uint8_t lastNibble = 0;

    if (uid.length() > 0) {
      char c = uid.charAt(uid.length() - 1);

      if (c >= '0' && c <= '9') {
        lastNibble = c - '0';
      } else if (c >= 'A' && c <= 'F') {
        lastNibble = 10 + c - 'A';
      }
    }

    uint8_t profile = lastNibble % 3;

    if (profile == 0) {
      features[0] = 2;
      features[1] = 0;
      features[2] = 2;
    } else if (profile == 1) {
      features[0] = 1;
      features[1] = 1;
      features[2] = 1;
    } else {
      features[0] = 0;
      features[1] = 2;
      features[2] = 0;
    }
  }
}


bool queueUID(const String &uid) {
  int slot = findFreeSlot();
  if (slot < 0) {
    Serial.println("FULL,EEPROM_QUEUE_FULL");
    return false;
  }

  uint8_t features[3] = {255, 255, 255};
  assignDemoFeatures(uid, features);

  // Diagnostic output: proves which UID and feature profile reached the model.
  Serial.print("DEBUG_UID,");
  Serial.println(uid);
  Serial.print("DEBUG_FEATURES,");
  Serial.print(features[0]); Serial.print(",");
  Serial.print(features[1]); Serial.print(",");
  Serial.println(features[2]);

  uint8_t priority = predictPriority(features);
  Serial.print("DEBUG_MODEL_CLASS,");
  Serial.println(priority);

  MessageRecord r;
  // Write the record body first and mark it valid last, reducing the chance
  // that a partial EEPROM write is mistaken for a valid record after power loss.
  r.magic = 0;
  r.seq = nextSeq++;
  if (nextSeq == 0) nextSeq = 1;
  memset(r.uid, 0, sizeof(r.uid));
  uid.substring(0, 10).toCharArray(r.uid, sizeof(r.uid));
  r.severity = features[0];
  r.deadline = features[1];
  r.eventType = features[2];
  r.priority = priority;

  EEPROM.put(recordAddress((uint8_t)slot), r);
  EEPROM.update(recordAddress((uint8_t)slot), RECORD_MAGIC);

  Serial.print("QUEUED,");
  Serial.print(r.seq); Serial.print(",");
  Serial.print(r.uid); Serial.print(",");
  Serial.print(priorityName(r.priority)); Serial.print(",");
  Serial.print(r.severity); Serial.print(",");
  Serial.print(r.deadline); Serial.print(",");
  Serial.println(r.eventType);
  return true;
}

void sendQueueInPriorityOrder() {
  bool sent[SLOT_COUNT];
  for (uint8_t i = 0; i < SLOT_COUNT; i++) sent[i] = false;

  Serial.println("BEGIN");
  for (uint8_t n = 0; n < SLOT_COUNT; n++) {
    int bestSlot = -1;
    MessageRecord best;
    for (uint8_t i = 0; i < SLOT_COUNT; i++) {
      if (sent[i]) continue;
      MessageRecord r;
      if (!readRecord(i, r)) continue;
      if (bestSlot < 0 || r.priority < best.priority ||
          (r.priority == best.priority && r.seq < best.seq)) {
        bestSlot = i;
        best = r;
      }
    }
    if (bestSlot < 0) break;
    sent[bestSlot] = true;

    Serial.print("MSG,");
    Serial.print(best.seq); Serial.print(",");
    Serial.print(best.uid); Serial.print(",");
    Serial.print(priorityName(best.priority)); Serial.print(",");
    Serial.print(best.severity); Serial.print(",");
    Serial.print(best.deadline); Serial.print(",");
    Serial.println(best.eventType);
  }
  Serial.println("END");
}

void acknowledgeSeq(uint8_t seq) {
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    MessageRecord r;
    if (readRecord(i, r) && r.seq == seq) {
      EEPROM.update(recordAddress(i), 0); // Clear magic byte after ACK
      Serial.print("ACKED,");
      Serial.println(seq);
      return;
    }
  }
  Serial.print("ACK_UNKNOWN,");
  Serial.println(seq);
}

void clearQueue() {
  uint8_t cleared = 0;
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    int address = recordAddress(i);
    if (EEPROM.read(address) == RECORD_MAGIC) {
      EEPROM.update(address, 0);
      cleared++;
    }
  }
  Serial.print("CLEARED,");
  Serial.println(cleared);
  Serial.print("STATUS,");
  Serial.println(countQueued());
}

void handleCommand(String cmd) {
  cmd.trim();
  if (cmd == "SYNC") {
    sendQueueInPriorityOrder();
  } else if (cmd == "STATUS") {
    Serial.print("STATUS,");
    Serial.println(countQueued());
  } else if (cmd == "CLEAR") {
    clearQueue();
  } else if (cmd.startsWith("ACK,")) {
    int comma = cmd.indexOf(',');
    int seq = cmd.substring(comma + 1).toInt();
    if (seq >= 0 && seq <= 255) acknowledgeSeq((uint8_t)seq);
  } else if (cmd == "HELP") {
    Serial.println("COMMANDS,SYNC|STATUS|CLEAR|ACK,<seq>");
  } else if (cmd.length() > 0) {
    Serial.println("ERROR,UNKNOWN_COMMAND; send HELP");
  }
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialLine.length() > 0) {
        handleCommand(serialLine);
        serialLine = "";
      }
    } else if (serialLine.length() < 40) {
      serialLine += c;
    }
  }
}

String readRFIDUID() {
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

void setup() {
  Serial.begin(9600);
  SPI.begin();
  rfid.PCD_Init();
  delay(500);

  // Resume sequence numbering from records that are still queued.
  for (uint8_t i = 0; i < SLOT_COUNT; i++) {
    MessageRecord r;
    if (readRecord(i, r) && r.seq >= nextSeq) {
      nextSeq = r.seq + 1;
      if (nextSeq == 0) nextSeq = 1;
    }
  }

  Serial.println("RFID_DTN_TINYML_READY");
  Serial.println("Scan tags to classify and queue; send SYNC after reconnecting.");
  Serial.println("Commands: STATUS, SYNC, CLEAR, ACK,<seq>, HELP");
}

void loop() {
  readSerialCommands();

  if (!rfid.PICC_IsNewCardPresent()) return;
  if (!rfid.PICC_ReadCardSerial()) return;

  String uid = readRFIDUID();
  if (uid != lastUID || millis() - lastScanMillis > 2500) {
    queueUID(uid);
    lastUID = uid;
    lastScanMillis = millis();
  }

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  delay(200);
}
