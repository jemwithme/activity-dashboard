#include "Arduino.h"
#include "LoRa_E220.h"

#define E220_RX 16
#define E220_TX 17
#define E220_AUX 4

#define POWER_SENSE_PIN 34

// Hysteresis:
// - turn ON only when reading is clearly high
// - turn OFF only when reading is clearly low
#define POWER_ON_THRESHOLD 2700
#define POWER_OFF_THRESHOLD 2650

LoRa_E220 e220ttl(&Serial2, E220_AUX);

String nodeId = "LIGHT_NODE_01";
int counter = 0;

bool powerPresentBefore = false;
bool powerOffSent = false;

bool candidatePowerState = false;
unsigned long candidateSinceMs = 0;

unsigned long lastHeartbeatMs = 0;

const unsigned long HEARTBEAT_INTERVAL_MS = 5000;
const unsigned long POWER_DEBOUNCE_MS = 1000;


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
}


int readPowerSense() {
  int reading = analogRead(POWER_SENSE_PIN);

  Serial.print("GPIO34 reading: ");
  Serial.println(reading);

  return reading;
}


bool sensedPowerState(int reading) {
  if (powerPresentBefore) {
    return reading > POWER_OFF_THRESHOLD;
  }

  return reading >= POWER_ON_THRESHOLD;
}


void startPowerOnSequence() {
  counter = 0;
  powerPresentBefore = true;
  powerOffSent = false;
  lastHeartbeatMs = millis();

  sendEvent("power_on");
}


void setup() {
  Serial.begin(115200);
  delay(500);

  Serial2.begin(9600, SERIAL_8N1, E220_RX, E220_TX);
  e220ttl.begin();

  pinMode(POWER_SENSE_PIN, INPUT);

  Serial.println("ESP32 E220 power-sense node started");

  int initialReading = readPowerSense();
  powerPresentBefore = initialReading >= POWER_ON_THRESHOLD;
  candidatePowerState = powerPresentBefore;
  candidateSinceMs = millis();

  if (powerPresentBefore) {
    startPowerOnSequence();
  } else {
    Serial.println("Booted but power sense is LOW");
  }

  lastHeartbeatMs = millis();
}


void loop() {
  int reading = readPowerSense();
  bool powerPresentNow = sensedPowerState(reading);
  unsigned long nowMs = millis();

  // Debounce power state changes.
  // The reading must stay changed for 1 second before we accept it.
  if (powerPresentNow != candidatePowerState) {
    candidatePowerState = powerPresentNow;
    candidateSinceMs = nowMs;
  }

  bool candidateStable = nowMs - candidateSinceMs >= POWER_DEBOUNCE_MS;

  if (candidateStable && candidatePowerState != powerPresentBefore) {
    if (candidatePowerState) {
      startPowerOnSequence();
    } else {
      sendEvent("power_off");
      powerOffSent = true;
      powerPresentBefore = false;
      counter = 0;
    }
  }

  if (powerPresentBefore && nowMs - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    counter++;
    sendEvent("heartbeat");
    lastHeartbeatMs = nowMs;
  }

  delay(200);
}
