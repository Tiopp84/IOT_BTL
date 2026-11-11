#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_SHT31.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>

// ================== CẤU HÌNH ==================

// 1. Wi-Fi
const char* ssid     = "Pixel_4168";
const char* password = "88888888";


// 2. MQTT (Mosquitto trong Docker)
const char* mqtt_server = "dxs.local";          // nên dùng IP máy chạy Docker
const int   mqtt_port   = 1883;
const char* mqtt_topic  = "fire/node01/telemetry";  // ESP32 -> backend
const char* alert_topic = "fire/node01/alert";      // backend -> ESP32
const char* cmd_topic   = "fire/node01/cmd";        // dashboard -> ESP32
const char* device_id   = "node01";

// 3. Cảm biến lửa / còi
const int FLAME_ANALOG_PIN   = 34;
const int BUZZER_PIN         = 19;
const int FLAME_ACTIVE_LEVEL = 2000;   // analog < ngưỡng => có lửa

// 4. OLED DST-015 (SSD1306 128x64) - bus Wire1
#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1
#define OLED_ADDR     0x3C             // thử 0x3D nếu không hiện

// 5. PMS5003 - bus Serial2
const int PMS_RX_PIN = 16;             // ESP32 RX2 <- PMS TX
const int PMS_TX_PIN = 17;             // không dùng
const unsigned long PMS_STALE_MS = 3000;

// 6. Thời gian cảnh báo
const unsigned long ALERT_STALE_MS = 15000;

// ================== ĐỐI TƯỢNG ==================

WiFiClient espClient;
PubSubClient client(espClient);

Adafruit_SHT31 sht31;                                                   // Wire  21/22
Adafruit_SSD1306 oled(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire1, OLED_RESET); // Wire1 26/25

char bootId[20];
uint32_t seq = 0;
uint32_t irStartMs = 0;
unsigned long lastMsg = 0;
bool oledOk = false;
bool irActiveState = false;

// PMS5003
uint8_t pmsBuf[32];
uint8_t pmsIdx = 0;
uint16_t pm1 = 0, pm25 = 0, pm10 = 0;
unsigned long pmsLastMs = 0;
bool pmsSeen = false;

// Trạng thái nhận từ model
String riskLevel = "";
String healthState = "";
float pFire = NAN;
int alertVotes = 0;
int alertWindow = 5;
unsigned long lastAlertMs = 0;
bool buzzerEnabled = true;
unsigned long buzzerBeepUntil = 0;

// ================== TIỆN ÍCH ==================

void scanI2C(TwoWire &bus, const char *name) {
  Serial.printf("Quet I2C (%s):\n", name);
  for (uint8_t addr = 1; addr < 127; addr++) {
    bus.beginTransmission(addr);
    if (bus.endTransmission() == 0) Serial.printf("  0x%02X\n", addr);
  }
}

void onMqttMessage(char *topic, byte *payload, unsigned int length) {
  bool isAlert = strcmp(topic, alert_topic) == 0;
  bool isCmd   = strcmp(topic, cmd_topic) == 0;
  if (!isAlert && !isCmd) return;

  JsonDocument doc;   // ArduinoJson v7 (v6: DynamicJsonDocument doc(512);)
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("JSON loi tren %s: %s\n", topic, err.c_str());
    return;
  }

  if (isCmd) {
    if (doc["buzzer_enabled"].is<bool>()) {
      bool next = doc["buzzer_enabled"].as<bool>();
      if (next && !buzzerEnabled) buzzerBeepUntil = millis() + 150;  // bíp xác nhận khi bật
      buzzerEnabled = next;
      Serial.printf("Lenh loa: %s\n", buzzerEnabled ? "BAT" : "TAT");
    }
    return;
  }

  riskLevel   = (const char *)(doc["risk_level"] | "");
  healthState = (const char *)(doc["health"] | "?");
  pFire       = (float)(doc["p_fire"] | NAN);
  alertVotes  = doc["votes"] | 0;
  alertWindow = doc["window"] | 5;
  lastAlertMs = millis();

  Serial.printf("Alert: risk=%s health=%s p=%.3f votes=%d/%d\n",
                riskLevel.c_str(), healthState.c_str(), pFire,
                alertVotes, alertWindow);
}

void setup_wifi() {
  Serial.printf("\nDang ket noi Wi-Fi: %s\n", ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  WiFi.setSleep(false);
  Serial.printf("\nWiFi OK, IP: %s\n", WiFi.localIP().toString().c_str());
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Dang ket noi MQTT...");
    if (client.connect("esp32-node01")) {
      Serial.println(" OK");
      client.subscribe(alert_topic, 1);
      client.subscribe(cmd_topic, 1);
    } else {
      Serial.printf(" that bai rc=%d, thu lai sau 5s\n", client.state());
      delay(5000);
    }
  }
}

// ================== PMS5003 ==================

void pollPms() {
  while (Serial2.available()) {
    uint8_t b = Serial2.read();

    if (pmsIdx == 0 && b != 0x42) continue;
    if (pmsIdx == 1 && b != 0x4D) { pmsIdx = 0; continue; }

    pmsBuf[pmsIdx++] = b;
    if (pmsIdx < 32) continue;
    pmsIdx = 0;

    uint16_t sum = 0;
    for (uint8_t i = 0; i < 30; i++) sum += pmsBuf[i];
    uint16_t checksum = (uint16_t)(pmsBuf[30] << 8) | pmsBuf[31];
    if (sum != checksum) continue;

    // Giá trị atmospheric (phù hợp trong nhà)
    pm1  = (uint16_t)(pmsBuf[10] << 8) | pmsBuf[11];
    pm25 = (uint16_t)(pmsBuf[12] << 8) | pmsBuf[13];
    pm10 = (uint16_t)(pmsBuf[14] << 8) | pmsBuf[15];

    pmsLastMs = millis();
    pmsSeen = true;
  }
}

// ================== OLED ==================

const char *riskText(bool fresh) {
  if (lastAlertMs == 0) return "CHO MODEL";
  if (!fresh) return "OFFLINE";
  if (riskLevel == "FIRE_ALERT") return "CHAY!";
  if (riskLevel == "ATTENTION")  return "CHU Y";
  if (riskLevel == "SAFE")       return "AN TOAN";
  if (healthState == "SENSOR_ERROR") return "LOI CB";
  if (healthState == "OFFLINE")      return "MAT DL";
  return "THEO DOI";
}

void updateOled(int flame, uint32_t irDuration, float t, float h,
                bool sensorOk, bool pmsFresh) {
  if (!oledOk) return;

  bool fresh = lastAlertMs && (millis() - lastAlertMs < ALERT_STALE_MS);
  bool remoteFire = fresh && riskLevel == "FIRE_ALERT";

  oled.invertDisplay(remoteFire);
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);

  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("Fire Monitor");
  oled.setCursor(92, 0);
  if (!buzzerEnabled) oled.print("MUTE");
  else oled.print(client.connected() ? "MQTT" : "OFF");

  oled.setTextSize(2);
  oled.setCursor(0, 10);
  oled.print(riskText(fresh));

  oled.setTextSize(1);
  oled.setCursor(0, 30);
  if (isnan(pFire)) oled.print("p=--");
  else              oled.printf("p=%.2f", pFire);
  oled.setCursor(70, 30);
  oled.printf("v=%d/%d", alertVotes, alertWindow);

  oled.setCursor(0, 40);
  if (sensorOk) oled.printf("%.1fC  %.0f%%", t, h);
  else          oled.print("SHT31 ERR");

  oled.setCursor(0, 52);
  if (irActiveState) {
    oled.printf("IR:%d FIRE%3lus", flame, (unsigned long)(irDuration / 1000));
  } else if (pmsFresh) {
    oled.printf("PM2.5:%u IR:%d", pm25, flame);
  } else {
    oled.printf("PM2.5:-- IR:%d", flame);
  }

  oled.display();
}

// ================== CÒI ==================

void updateBuzzer() {
  bool fresh = lastAlertMs && (millis() - lastAlertMs < ALERT_STALE_MS);
  bool remoteFire = fresh && riskLevel == "FIRE_ALERT";

  if (!buzzerEnabled) {
    digitalWrite(BUZZER_PIN, HIGH);
    return;
  }

  if (millis() < buzzerBeepUntil || irActiveState || remoteFire) {
    digitalWrite(BUZZER_PIN, LOW);   // hú khi còn cảnh báo cháy
  } else if (fresh && riskLevel == "ATTENTION") {
    digitalWrite(BUZZER_PIN, (millis() / 500) % 2 ? HIGH : LOW);  // chíp 0,5 s
  } else {
    digitalWrite(BUZZER_PIN, HIGH);
  }
}

// ================== SETUP ==================

void setup() {
  Serial.begin(115200);

  pinMode(FLAME_ANALOG_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);

  snprintf(bootId, sizeof(bootId), "boot-%08lx", (unsigned long)esp_random());

  // SHT31: Wire 21/22
  Wire.begin(21, 22);
  // OLED: Wire1 26/25
  Wire1.begin(26, 25);
  // PMS5003: Serial2 9600 8N1, RX = GPIO16
  Serial2.begin(9600, SERIAL_8N1, PMS_RX_PIN, PMS_TX_PIN);

  scanI2C(Wire,  "SHT31 21/22");
  scanI2C(Wire1, "OLED 26/25");

  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (!oledOk) {
    Serial.println("Khong tim thay OLED! Thu OLED_ADDR = 0x3D");
  } else {
    oled.clearDisplay();
    oled.setTextSize(1);
    oled.setTextColor(SSD1306_WHITE);
    oled.setCursor(0, 0);
    oled.println("Fire Monitor");
    oled.println("Khoi dong...");
    oled.display();
  }

  bool sensorOk = sht31.begin(0x44);
  Serial.println(sensorOk ? "SHT31 san sang" : "Khong tim thay SHT31!");

  setup_wifi();

  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(onMqttMessage);
  client.setBufferSize(1024);   // đủ cho telemetry và alert JSON
  client.setKeepAlive(15);

  Serial.printf("boot_id=%s\n", bootId);
  Serial.println("Cho PMS5003 warm-up (~30s)...");
}

// ================== LOOP ==================

void loop() {
  if (!client.connected()) reconnect();
  client.loop();
  pollPms();
  updateBuzzer();

  unsigned long now = millis();
  if (now - lastMsg < 1000) return;   // 1 Hz
  lastMsg = now;

  // --- Cảm biến lửa ---
  int flameAnalog = analogRead(FLAME_ANALOG_PIN);
  bool irActive = flameAnalog < FLAME_ACTIVE_LEVEL;
  irActiveState = irActive;

  if (irActive) {
    if (irStartMs == 0) irStartMs = now;
  } else {
    irStartMs = 0;
  }
  uint32_t irDuration = irStartMs ? (now - irStartMs) : 0;

  // --- SHT31 ---
  float tempC    = sht31.readTemperature();
  float humidity = sht31.readHumidity();
  bool sensorOk  = !isnan(tempC) && !isnan(humidity);
  if (!sensorOk) {
    tempC = 0.0;
    humidity = 0.0;
  }

  // --- PMS5003 ---
  bool pmsFresh = pmsSeen && (now - pmsLastMs < PMS_STALE_MS);

  const char* status = "OK";
  if (!sensorOk)      status = "SHT31_FAIL";
  else if (!pmsFresh) status = "PMS5003_FAIL";

  // --- Payload đúng schema backend ---
  char payload[420];
  snprintf(payload, sizeof(payload),
    "{\"schema_version\":1,\"device_id\":\"%s\",\"boot_id\":\"%s\","
    "\"seq\":%lu,\"uptime_ms\":%lu,"
    "\"temperature_c\":%.2f,\"humidity_pct\":%.2f,"
    "\"pm1_ug_m3\":%u,\"pm25_ug_m3\":%u,"
    "\"ir_active\":%s,\"ir_active_duration_ms\":%lu,"
    "\"ir_raw\":%d,\"buzzer_enabled\":%s,"
    "\"sensor_status\":\"%s\"}",
    device_id, bootId,
    (unsigned long)++seq, (unsigned long)now,
    tempC, humidity,
    (unsigned)pm1, (unsigned)pm25,
    irActive ? "true" : "false",
    (unsigned long)irDuration,
    flameAnalog,
    buzzerEnabled ? "true" : "false",
    status);

  bool ok = client.publish(mqtt_topic, payload);
  Serial.printf("publish=%d PM1=%u PM2.5=%u PM10=%u %s\n",
                ok, pm1, pm25, pm10, payload);

  updateOled(flameAnalog, irDuration, tempC, humidity, sensorOk, pmsFresh);
}