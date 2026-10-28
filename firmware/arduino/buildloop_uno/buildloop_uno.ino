// BuildLoop AI - Arduino Uno R3 variant of the protocol v1 firmware (UNVERIFIED on hardware; compile check pending).
// Requires ArduinoJson 7. Uno has a 10-bit ADC; thresholds from the host (0-4095) are scaled to 0-1023.
#include <ArduinoJson.h>

const int LED_PIN = 9;
const int ADC_PIN = A0;
const int PROTO = 1;
const size_t MAX_LINE = 160;  // Uno SRAM is tight
const unsigned long TELEMETRY_MS = 250;
const unsigned long HOST_TIMEOUT_MS = 10000;

char lineBuf[MAX_LINE];
size_t lineLen = 0;
bool ledState = false;
unsigned long lastTelemetry = 0, lastHost = 0;
bool autoActive = false, darkIsLow = true;
int autoThreshold = 0;
unsigned long autoEnd = 0;

void setLed(bool on) { ledState = on; digitalWrite(LED_PIN, on ? HIGH : LOW); }

void reply(const char *id, const char *type, bool ok, JsonDocument &data) {
  JsonDocument d;
  d["v"] = PROTO;
  if (id && id[0]) d["id"] = id;
  d["type"] = type;
  d["ok"] = ok;
  d["data"] = data;
  serializeJson(d, Serial);
  Serial.print('\n');
}

void replyError(const char *id, const char *code, const char *msg) {
  JsonDocument data;
  data["code"] = code;
  data["message"] = msg;
  reply(id, "error", false, data);
}

void handleLine(const char *line) {
  JsonDocument in;
  if (deserializeJson(in, line) != DeserializationError::Ok) return;
  const char *id = in["id"] | "";
  const char *cmd = in["cmd"] | "";
  if ((int)(in["v"] | 0) != PROTO) { replyError(id, "bad_version", "protocol v1 only"); return; }
  JsonVariantConst args = in["args"];
  lastHost = millis();
  JsonDocument data;
  if (!strcmp(cmd, "hello")) {
    data["fw"] = "buildloop-uno";
    data["fw_version"] = "0.1.0";
    data["board"] = "arduino-uno";
    data["led_pin"] = LED_PIN;
    data["adc_pin"] = 14;
    reply(id, "hello", true, data);
  } else if (!strcmp(cmd, "heartbeat")) {
    reply(id, "heartbeat", true, data);
  } else if (!strcmp(cmd, "get_capabilities")) {
    data["led_pin"] = LED_PIN;
    data["adc_pin"] = 14;
    data["adc_bits"] = 10;
    reply(id, "capabilities", true, data);
  } else if (!strcmp(cmd, "read_sensor")) {
    data["adc"] = analogRead(ADC_PIN);
    data["led"] = ledState;
    reply(id, "ack", true, data);
  } else if (!strcmp(cmd, "set_led")) {
    if ((int)(args["pin"] | -1) != LED_PIN) { replyError(id, "pin_not_allowed", "not the LED pin"); return; }
    autoActive = false;
    setLed(args["on"].as<bool>());
    data["led"] = ledState;
    reply(id, "ack", true, data);
  } else if (!strcmp(cmd, "run_test")) {
    int th = args["threshold"] | -1;
    unsigned long dur = args["duration_ms"] | 0;
    const char *dm = args["dark_means"] | "";
    if (th < 0 || th > 4095 || dur < 500 || dur > 60000) { replyError(id, "bad_args", "invalid"); return; }
    autoThreshold = (int)((long)th * 1023L / 4095L);
    darkIsLow = !strcmp(dm, "adc_low");
    autoEnd = millis() + dur;
    autoActive = true;
    data["running"] = true;
    reply(id, "ack", true, data);
  } else if (!strcmp(cmd, "stop")) {
    autoActive = false;
    setLed(false);
    data["led"] = false;
    reply(id, "ack", true, data);
  } else {
    replyError(id, "unknown_command", "not whitelisted");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  setLed(false);
  lastHost = millis();
}

void loop() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n') { lineBuf[lineLen] = 0; if (lineLen) handleLine(lineBuf); lineLen = 0; }
    else if (ch != '\r') { if (lineLen < MAX_LINE - 1) lineBuf[lineLen++] = ch; else lineLen = 0; }
  }
  unsigned long now = millis();
  int adc = analogRead(ADC_PIN);
  if (autoActive) {
    setLed(darkIsLow ? adc < autoThreshold : adc > autoThreshold);
    if ((long)(now - autoEnd) >= 0) { autoActive = false; setLed(false); }
  }
  if (ledState && now - lastHost > HOST_TIMEOUT_MS) { autoActive = false; setLed(false); }
  if (now - lastTelemetry >= TELEMETRY_MS) {
    lastTelemetry = now;
    JsonDocument d;
    d["v"] = PROTO;
    d["type"] = "telemetry";
    d["data"]["t"] = now;
    d["data"]["adc"] = adc * 4;  // coarse scale to 12-bit for host parity
    d["data"]["led"] = ledState;
    serializeJson(d, Serial);
    Serial.print('\n');
  }
}
