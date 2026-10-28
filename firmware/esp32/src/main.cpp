// BuildLoop AI ESP32 firmware - protocol v1 (newline-delimited JSON over USB serial, 115200 baud).
// Safe defaults: LED output LOW at boot, on stop, on host loss, and when an autonomous test ends.
// Whitelisted commands only: hello, heartbeat, get_capabilities, read_sensor, set_led, run_test, stop.
#include <Arduino.h>
#include <ArduinoJson.h>

#ifndef BL_LED_PIN
#define BL_LED_PIN 25
#endif
#ifndef BL_ADC_PIN
#define BL_ADC_PIN 34  // ADC1 channel, input-only, usable with WiFi
#endif
#ifndef BL_FW_VERSION
#define BL_FW_VERSION "0.1.0"
#endif

static const int PROTO = 1;
static const size_t MAX_LINE = 512;
static const uint32_t TELEMETRY_MS = 200;
static const uint32_t HOST_TIMEOUT_MS = 10000;  // no valid command for this long -> LED off

static char lineBuf[MAX_LINE];
static size_t lineLen = 0;
static bool ledState = false;
static uint32_t lastTelemetry = 0;
static uint32_t lastHost = 0;

static struct {
  bool active = false;
  int threshold = 0;
  bool darkIsLow = true;
  uint32_t endAt = 0;
} autoTest;

static void setLed(bool on) {
  ledState = on;
  digitalWrite(BL_LED_PIN, on ? HIGH : LOW);
}

static void send(JsonDocument &doc) {
  serializeJson(doc, Serial);
  Serial.print('\n');
}

static void reply(const char *id, const char *type, bool ok, JsonDocument &data) {
  JsonDocument d;
  d["v"] = PROTO;
  if (id) d["id"] = id;
  d["type"] = type;
  d["ok"] = ok;
  d["data"] = data;
  send(d);
}

static void replyError(const char *id, const char *code, const char *msg) {
  JsonDocument data;
  data["code"] = code;
  data["message"] = msg;
  reply(id, "error", false, data);
}

static void handleLine(const char *line) {
  JsonDocument in;
  if (deserializeJson(in, line) != DeserializationError::Ok) return;  // garbage ignored, never executed
  if ((int)(in["v"] | 0) != PROTO) { replyError(in["id"] | "", "bad_version", "protocol v1 only"); return; }
  const char *id = in["id"] | "";
  const char *cmd = in["cmd"] | "";
  JsonVariantConst args = in["args"];
  lastHost = millis();
  JsonDocument data;

  if (!strcmp(cmd, "hello")) {
    data["fw"] = "buildloop-esp32";
    data["fw_version"] = BL_FW_VERSION;
    data["board"] = "esp32-devkitc-v4";
    data["led_pin"] = BL_LED_PIN;
    data["adc_pin"] = BL_ADC_PIN;
    reply(id, "hello", true, data);
  } else if (!strcmp(cmd, "heartbeat")) {
    reply(id, "heartbeat", true, data);
  } else if (!strcmp(cmd, "get_capabilities")) {
    JsonArray c = data["commands"].to<JsonArray>();
    for (const char *n : {"hello", "heartbeat", "get_capabilities", "read_sensor", "set_led", "run_test", "stop"}) c.add(n);
    data["led_pin"] = BL_LED_PIN;
    data["adc_pin"] = BL_ADC_PIN;
    data["adc_bits"] = 12;
    reply(id, "capabilities", true, data);
  } else if (!strcmp(cmd, "read_sensor")) {
    data["adc"] = analogRead(BL_ADC_PIN);
    data["led"] = ledState;
    reply(id, "ack", true, data);
  } else if (!strcmp(cmd, "set_led")) {
    int pin = args["pin"] | -1;
    if (pin != BL_LED_PIN) { replyError(id, "pin_not_allowed", "pin is not the configured LED pin"); return; }
    if (args["on"].isNull()) { replyError(id, "bad_args", "on required"); return; }
    autoTest.active = false;
    setLed(args["on"].as<bool>());
    data["led"] = ledState;
    reply(id, "ack", true, data);
  } else if (!strcmp(cmd, "run_test")) {
    int th = args["threshold"] | -1;
    uint32_t dur = args["duration_ms"] | 0;
    const char *dm = args["dark_means"] | "";
    if (th < 0 || th > 4095 || dur < 500 || dur > 60000 || (strcmp(dm, "adc_low") && strcmp(dm, "adc_high"))) {
      replyError(id, "bad_args", "threshold/dark_means/duration_ms invalid");
      return;
    }
    autoTest.active = true;
    autoTest.threshold = th;
    autoTest.darkIsLow = !strcmp(dm, "adc_low");
    autoTest.endAt = millis() + dur;
    data["running"] = true;
    reply(id, "ack", true, data);
  } else if (!strcmp(cmd, "stop")) {
    autoTest.active = false;
    setLed(false);
    data["led"] = false;
    reply(id, "ack", true, data);
  } else {
    replyError(id, "unknown_command", "command not whitelisted");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(BL_LED_PIN, OUTPUT);
  setLed(false);
  analogReadResolution(12);
  analogSetPinAttenuation(BL_ADC_PIN, ADC_11db);  // ~0-3.1 V; never feed more than 3.3 V
  lastHost = millis();
}

void loop() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n') {
      lineBuf[lineLen] = 0;
      if (lineLen > 0) handleLine(lineBuf);
      lineLen = 0;
    } else if (ch != '\r') {
      if (lineLen < MAX_LINE - 1) lineBuf[lineLen++] = ch;
      else lineLen = 0;  // overflow: drop line
    }
  }

  uint32_t now = millis();
  int adc = analogRead(BL_ADC_PIN);

  if (autoTest.active) {
    bool dark = autoTest.darkIsLow ? (adc < autoTest.threshold) : (adc > autoTest.threshold);
    setLed(dark);
    if ((int32_t)(now - autoTest.endAt) >= 0) {
      autoTest.active = false;
      setLed(false);
      JsonDocument d, data;
      data["finished"] = true;
      d["v"] = PROTO;
      d["type"] = "test_result";
      d["data"] = data;
      send(d);
    }
  }

  if (ledState && now - lastHost > HOST_TIMEOUT_MS) {  // host lost: fail safe
    autoTest.active = false;
    setLed(false);
  }

  if (now - lastTelemetry >= TELEMETRY_MS) {
    lastTelemetry = now;
    JsonDocument d;
    d["v"] = PROTO;
    d["type"] = "telemetry";
    d["data"]["t"] = now;
    d["data"]["adc"] = adc;
    d["data"]["led"] = ledState;
    send(d);
  }
}
