/*
  Sportstracker+ Satellit: ESP32 mit DHT22 (Temperatur, Luftfeuchte) und MQ-135 (Luftqualität, Rohwert)

  Sendet alle INTERVAL_MS Sekunden ein JSON  {"temperature":22.4,"humidity":45.1,"gas":812}
    - per HTTP-POST an den Sportstracker-Pi   (USE_HTTP = 1)
    - und/oder per MQTT an Mosquitto          (USE_MQTT = 1), samt Home-Assistant-Discovery

  Bibliotheken (Arduino-IDE, Bibliotheksverwalter):
    - "DHT sensor library" von Adafruit  (dazu "Adafruit Unified Sensor")
    - "PubSubClient" von Nick O'Leary    (nur für MQTT)
  Board: "ESP32 Dev Module" (ESP32-Core 2.x oder 3.x von Espressif).

  NICHT auf einem echten Gerät getestet: erst mit dem Serial Monitor (115200 Baud) prüfen.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <DHT.h>

// ============================ Einstellungen ============================

const char* WIFI_SSID = "MEIN-WLAN";
const char* WIFI_PASS = "MEIN-WLAN-KENNWORT";

// Kennung und Name wie auf der Backend-Seite "Satelliten" (Kennung: a-z, 0-9, - und _)
const char* SAT_KEY  = "wohnzimmer";
const char* SAT_NAME = "Wohnzimmer";

// --- HTTP an den Sportstracker+ Pi ---
#define USE_HTTP 1
const char* PI_HOST   = "192.168.22.22";
const uint16_t PI_PORT = 8080;
const char* SAT_TOKEN = "TOKEN-AUS-DEM-BACKEND";      // Seite "Satelliten", Feld "Token"

// --- MQTT (Mosquitto, z. B. auf dem Home-Assistant-Pi) ---
#define USE_MQTT 1
const char* MQTT_HOST = "192.168.22.30";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER = "";                            // leer = ohne Anmeldung
const char* MQTT_PASS = "";
const char* MQTT_BASE = "sportstracker";               // wie "Basis-Topic" im Backend
const char* HA_PREFIX = "homeassistant";               // Discovery-Präfix von Home Assistant

// --- Sensoren ---
#define DHT_TYPE DHT22
const uint8_t DHT_PIN = 4;                             // DHT22-Datenleitung (mit 10 kOhm nach 3,3 V, falls kein Modul)
const uint8_t MQ_PIN  = 34;                            // MQ-135 AO ueber Spannungsteiler (ADC1-Pin, siehe README)
const uint32_t INTERVAL_MS  = 30000;                   // Sendeintervall
const uint32_t MQ_WARMUP_MS = 180000;                  // MQ-135: Gaswert erst nach 3 Minuten Aufwaermzeit senden

// =======================================================================

DHT dht(DHT_PIN, DHT_TYPE);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

char stateTopic[96];
char statusTopic[96];
uint32_t lastSend = 0;
bool discoverySent = false;

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("WLAN %s ...", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " verbunden" : " nicht verbunden");
  if (WiFi.status() == WL_CONNECTED) Serial.println(WiFi.localIP());
}

int readGas() {
  long sum = 0;
  for (int i = 0; i < 16; i++) {
    sum += analogRead(MQ_PIN);
    delay(5);
  }
  return (int)(sum / 16);                              // 0 .. 4095 (12 Bit)
}

// Baut das JSON; Werte, die nicht gelesen werden konnten, fehlen einfach.
void buildPayload(char* out, size_t size) {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  bool warm = millis() >= MQ_WARMUP_MS;
  int pos = snprintf(out, size, "{");
  bool first = true;
  if (!isnan(t) && !isnan(h)) {
    pos += snprintf(out + pos, size - pos, "\"temperature\":%.1f,\"humidity\":%.1f", t, h);
    first = false;
  }
  if (warm) {
    pos += snprintf(out + pos, size - pos, "%s\"gas\":%d", first ? "" : ",", readGas());
    first = false;
  }
  snprintf(out + pos, size - pos, "}");
  if (first) out[0] = 0;                               // nichts zu senden
}

void sendHttp(const char* payload) {
#if USE_HTTP
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  char url[128];
  snprintf(url, sizeof(url), "http://%s:%u/api/satellite/%s", PI_HOST, PI_PORT, SAT_KEY);
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + SAT_TOKEN);
  int code = http.POST((uint8_t*)payload, strlen(payload));
  Serial.printf("HTTP %s -> %d\n", url, code);
  http.end();
#endif
}

// ----- MQTT mit Home-Assistant-Discovery -----

void publishDiscovery(const char* objectId, const char* label, const char* valueKey, const char* unit,
                      const char* deviceClass) {
  char topic[128];
  char json[640];
  snprintf(topic, sizeof(topic), "%s/sensor/%s_%s/config", HA_PREFIX, SAT_KEY, objectId);
  int pos = snprintf(json, sizeof(json),
    "{\"name\":\"%s %s\",\"unique_id\":\"sportstracker_%s_%s\",\"state_topic\":\"%s\","
    "\"value_template\":\"{{ value_json.%s }}\",\"availability_topic\":\"%s\","
    "\"payload_available\":\"online\",\"payload_not_available\":\"offline\",\"state_class\":\"measurement\"",
    SAT_NAME, label, SAT_KEY, objectId, stateTopic, valueKey, statusTopic);
  if (unit && unit[0]) pos += snprintf(json + pos, sizeof(json) - pos, ",\"unit_of_measurement\":\"%s\"", unit);
  if (deviceClass && deviceClass[0]) pos += snprintf(json + pos, sizeof(json) - pos, ",\"device_class\":\"%s\"", deviceClass);
  snprintf(json + pos, sizeof(json) - pos,
    ",\"device\":{\"identifiers\":[\"sportstracker_%s\"],\"name\":\"Sportstracker+ %s\","
    "\"model\":\"ESP32 DHT22 MQ-135\",\"manufacturer\":\"Selbstbau\"}}", SAT_KEY, SAT_NAME);
  mqtt.publish(topic, json, true);                     // retained: Home Assistant findet den Sensor nach jedem Neustart
}

bool connectMqtt() {
#if USE_MQTT
  if (mqtt.connected()) return true;
  if (WiFi.status() != WL_CONNECTED) return false;
  String clientId = String("sportstracker-") + SAT_KEY;
  bool ok = (MQTT_USER[0])
    ? mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS, statusTopic, 0, true, "offline")
    : mqtt.connect(clientId.c_str(), nullptr, nullptr, statusTopic, 0, true, "offline");
  if (!ok) {
    Serial.printf("MQTT fehlgeschlagen, Status %d\n", mqtt.state());
    return false;
  }
  mqtt.publish(statusTopic, "online", true);
  if (!discoverySent) {
    publishDiscovery("temperature", "Temperatur", "temperature", "°C", "temperature");
    publishDiscovery("humidity", "Luftfeuchte", "humidity", "%", "humidity");
    publishDiscovery("gas", "Gaswert MQ-135", "gas", "", "");
    discoverySent = true;
  }
  return true;
#else
  return false;
#endif
}

void setup() {
  Serial.begin(115200);
  delay(200);
  analogReadResolution(12);
  analogSetPinAttenuation(MQ_PIN, ADC_11db);           // Messbereich bis ca. 3,3 V
  dht.begin();
  snprintf(stateTopic, sizeof(stateTopic), "%s/%s/state", MQTT_BASE, SAT_KEY);
  snprintf(statusTopic, sizeof(statusTopic), "%s/%s/status", MQTT_BASE, SAT_KEY);
#if USE_MQTT
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(768);
#endif
  connectWifi();
  Serial.printf("Satellit %s (%s) gestartet, MQ-135 waermt %lu s auf\n", SAT_KEY, SAT_NAME, MQ_WARMUP_MS / 1000);
}

void loop() {
  connectWifi();
#if USE_MQTT
  if (connectMqtt()) mqtt.loop();
#endif
  if (lastSend == 0 || millis() - lastSend >= INTERVAL_MS) {
    lastSend = millis() ? millis() : 1;
    char payload[128];
    buildPayload(payload, sizeof(payload));
    if (payload[0]) {
      Serial.println(payload);
      sendHttp(payload);
#if USE_MQTT
      if (mqtt.connected()) mqtt.publish(stateTopic, payload);
#endif
    } else {
      Serial.println("Keine Werte (DHT22-Lesefehler, MQ-135 waermt noch auf)");
    }
  }
  delay(50);
}
