  #include "Arduino.h"
  #include "LoRa_E220.h"
#include <Preferences.h>
#include <esp_system.h>


  #define E220_RX 16
  #define E220_TX 17
  #define E220_AUX 4


  #define POWER_SENSE_PIN 34
  #define POWER_PRESENT_THRESHOLD 2400


  LoRa_E220 e220ttl(&Serial2, E220_AUX);


  String nodeId = "LIGHT_NODE_02";
  int counter = 0;


  bool powerPresentBefore = false;
  bool powerOffSent = false;


  unsigned long lastHeartbeatMs = 0;
  const unsigned long HEARTBEAT_INTERVAL_MS = 5000;



const char *LOG_VERSION = "node02-serial-log-20261029-v2";
const uint32_t LOG_LINES = 48;
Preferences savedLog;
bool savedLogReady = false;
uint32_t savedSequence = 0, savedBoot = 0;
char logCommand[16];
uint8_t logCommandLength = 0;

struct SerialLogRecord {
  uint32_t sequence, boot, uptime;
  char line[128];
};
struct PendingLogLine {
  uint32_t uptime;
  char line[128];
};
PendingLogLine pendingLines[8];
uint8_t pendingCount = 0;

// Queue after the original Serial.print calls; write flash after radio work.
void rememberSerialLine(const String &line) {
  if (pendingCount >= 8) {
    Serial.println("LOG ERROR: pending buffer full");
    return;
  }
  pendingLines[pendingCount].uptime = millis();
  line.toCharArray(pendingLines[pendingCount].line, sizeof(pendingLines[0].line));
  pendingCount++;
}

void flushSerialLog() {
  if (!savedLogReady) { pendingCount = 0; return; }
  for (uint8_t i = 0; i < pendingCount; i++) {
    SerialLogRecord record = {};
    record.sequence = savedSequence + 1;
    record.boot = savedBoot;
    record.uptime = pendingLines[i].uptime;
    memcpy(record.line, pendingLines[i].line, sizeof(record.line));
    char key[12];
    snprintf(key, sizeof(key), "r%lu", (unsigned long)(savedSequence % LOG_LINES));
    if (savedLog.putBytes(key, &record, sizeof(record)) == sizeof(record))
      savedSequence++;
    else Serial.println("LOG ERROR: flash write failed");
  }
  pendingCount = 0;
}

void initializeSerialLog() {
  savedLogReady = savedLog.begin("serial29v2", false);
  if (!savedLogReady) {
    Serial.println("LOG ERROR: storage unavailable");
    pendingCount = 0;
    return;
  }
  savedBoot = savedLog.getUInt("boot", 0) + 1;
  if (!savedLog.putUInt("boot", savedBoot))
    Serial.println("LOG ERROR: boot counter write failed");
  for (uint32_t i = 0; i < LOG_LINES; i++) {
    char key[12];
    snprintf(key, sizeof(key), "r%lu", (unsigned long)i);
    SerialLogRecord record = {};
    if (savedLog.getBytesLength(key) == sizeof(record) &&
        savedLog.getBytes(key, &record, sizeof(record)) == sizeof(record) &&
        record.sequence > savedSequence) savedSequence = record.sequence;
  }
  // Boot metadata follows the original startup output in the saved transcript.
  rememberSerialLine(String("BOOT fw=") + LOG_VERSION +
    " reset_reason=" + String((int)esp_reset_reason()));
  flushSerialLog();
  Serial.println("Saved serial log ready. Send logs at 115200 baud + Newline.");
}

void dumpSerialLog() {
  if (!savedLogReady) { Serial.println("LOG ERROR: storage unavailable"); return; }
  Serial.println("--- SAVED SERIAL OUTPUT: oldest first ---");
  uint32_t first = savedSequence > LOG_LINES ? savedSequence - LOG_LINES : 0;
  for (uint32_t i = first; i < savedSequence; i++) {
    char key[12];
    snprintf(key, sizeof(key), "r%lu", (unsigned long)(i % LOG_LINES));
    SerialLogRecord record = {};
    if (savedLog.getBytesLength(key) != sizeof(record) ||
        savedLog.getBytes(key, &record, sizeof(record)) != sizeof(record) ||
        record.sequence != i + 1) {
      Serial.println("[missing log line]");
      continue;
    }
    record.line[sizeof(record.line) - 1] = 0;
    Serial.printf("[boot=%lu up=%lums] %s\n",
      (unsigned long)record.boot, (unsigned long)record.uptime, record.line);
  }
  Serial.println("--- END SAVED SERIAL OUTPUT ---");
}

void serviceSerialLog() {
  flushSerialLog();
  for (int i = 0; i < 64 && Serial.available(); i++) {
    char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (!logCommandLength) continue;
      logCommand[logCommandLength] = 0;
      if (!strcmp(logCommand, "logs")) dumpSerialLog();
      else Serial.println("Command: logs (115200 baud, Newline)");
      logCommandLength = 0;
    } else if (logCommandLength < sizeof(logCommand) - 1) {
      logCommand[logCommandLength++] = c;
    }
  }
}


  void sendEvent(String eventType) {
    String msg = "{";
    msg += "\"node\":\"" + nodeId + "\",";
    msg += "\"event\":\"" + eventType + "\",";
    msg += "\"count\":";
    msg += String(counter);
    msg += "}";


    ResponseStatus rs = e220ttl.sendMessage(msg);


    Serial.print("Sent: ");
    Serial.print(msg);
    Serial.print(" | ");
    Serial.println(rs.getResponseDescription());
    rememberSerialLine(String("Sent: ") + msg + " | " + rs.getResponseDescription());
  }


  bool isPowerPresent() {
    int reading = analogRead(POWER_SENSE_PIN);


    Serial.print("GPIO34 reading: ");
    Serial.println(reading);
    rememberSerialLine(String("GPIO34 reading: ") + String(reading));


    return reading > POWER_PRESENT_THRESHOLD;
  }


  void setup() {
    Serial.begin(115200);
    delay(500);


    Serial2.begin(9600, SERIAL_8N1, E220_RX, E220_TX);
    e220ttl.begin();


    pinMode(POWER_SENSE_PIN, INPUT);


    Serial.println("ESP32 E220 power-sense node started");
    rememberSerialLine("ESP32 E220 power-sense node started");


    powerPresentBefore = isPowerPresent();


    if (powerPresentBefore) {
      sendEvent("power_on");
    } else {
      Serial.println("Booted but power sense is LOW");
      rememberSerialLine("Booted but power sense is LOW");
    }


    lastHeartbeatMs = millis();
    initializeSerialLog();
  }


  void loop() {
    bool powerPresentNow = isPowerPresent();


    if (powerPresentNow && !powerPresentBefore) {
      powerPresentBefore = true;
      powerOffSent = false;
      sendEvent("power_on");
    }


    if (!powerPresentNow && powerPresentBefore && !powerOffSent) {
      sendEvent("power_off");
      powerOffSent = true;
      powerPresentBefore = false;
    }


    if (powerPresentNow && millis() - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
      sendEvent("heartbeat");
      counter++;
      lastHeartbeatMs = millis();
    }


    serviceSerialLog();
    delay(5000);
  }
