#include <Arduino.h>

// ===== FAN DRIVER (4x L298N, 2 fans per board) =====
// Each L298N has two channels:
//   Channel A: OUT1/OUT2 + ENA + IN1/IN2
//   Channel B: OUT3/OUT4 + ENB + IN3/IN4
//
// Command format (backward compatible):
//   F1=120 F2=200 ... F8=255   (0..255)
//   P1=128                     (optional pump PWM)
//
// Notes:
// - PWM is 0..255 (analogWrite)
// - Direction is fixed to "forward" per channel (INx=HIGH/LOW)

#define DRIVER_COUNT 4
#define FAN_COUNT (DRIVER_COUNT * 2)

struct DriverPins {
  // Channel A
  uint8_t ENA;   // PWM
  uint8_t IN1;
  uint8_t IN2;
  // Channel B
  uint8_t ENB;   // PWM
  uint8_t IN3;
  uint8_t IN4;
};

// Driver1: Fan1 (ENA=2 IN1=22 IN2=23) + Fan2 (ENB=3 IN3=24 IN4=25)
// Driver2: Fan3 (ENA=4 IN1=26 IN2=27) + Fan4 (ENB=5 IN3=28 IN4=29)
// Driver3: Fan5 (ENA=6 IN1=30 IN2=31) + Fan6 (ENB=7 IN3=32 IN4=33)
// Driver4: Fan7 (ENA=8 IN1=34 IN2=35) + Fan8 (ENB=9 IN3=36 IN4=37)
DriverPins DRV[DRIVER_COUNT] = {
  { 2, 22, 23,  3, 24, 25 },  // Driver1 -> Fan1 + Fan2
  { 4, 26, 27,  5, 28, 29 },  // Driver2 -> Fan3 + Fan4
  { 6, 30, 31,  7, 32, 33 },  // Driver3 -> Fan5 + Fan6
  { 8, 34, 35,  9, 36, 37 }   // Driver4 -> Fan7 + Fan8
};

const uint8_t PUMP_EN  = 44;   // PWM
const uint8_t PUMP_IN1 = 38;
const uint8_t PUMP_IN2 = 39;

int fanPWM[FAN_COUNT] = {0,0,0,0,0,0,0,0};
int pumpPWM = 0;

static inline void setForwardA(const DriverPins &d) {
  digitalWrite(d.IN1, HIGH);
  digitalWrite(d.IN2, LOW);
}

static inline void setForwardB(const DriverPins &d) {
  digitalWrite(d.IN3, HIGH);
  digitalWrite(d.IN4, LOW);
}

static inline void applyOutputs() {
  // Fans
  for (int i = 0; i < DRIVER_COUNT; i++) {
    int pwmA = constrain(fanPWM[2*i],     0, 255); // Fan (2*i+1)
    int pwmB = constrain(fanPWM[2*i + 1], 0, 255); // Fan (2*i+2)
    analogWrite(DRV[i].ENA, pwmA);
    analogWrite(DRV[i].ENB, pwmB);
  }

  // Pump
  pumpPWM = constrain(pumpPWM, 0, 255);
  analogWrite(PUMP_EN, pumpPWM);
}

static void parseToken(const String &tok) {
  if (tok.length() < 4) return;

  if (tok[0] == 'F') {
    int eq = tok.indexOf('=');
    if (eq <= 1) return;

    int idx = tok.substring(1, eq).toInt();   // 1..8
    if (idx < 1 || idx > FAN_COUNT) return;

    int val = tok.substring(eq + 1).toInt();
    fanPWM[idx - 1] = val;
    return;
  }

  if (tok.startsWith("P1=")) {
    int val = tok.substring(3).toInt();
    pumpPWM = val;
    return;
  }
}

static void parseLine(String line) {
  line.trim();
  if (line.length() == 0) return;

  int start = 0;
  while (start < (int)line.length()) {
    while (start < (int)line.length() && (line[start] == ' ' || line[start] == '\t')) start++;
    if (start >= (int)line.length()) break;

    int end = line.indexOf(' ', start);
    if (end < 0) end = line.length();

    String tok = line.substring(start, end);
    tok.trim();
    if (tok.length() > 0) parseToken(tok);

    start = end + 1;
  }
}

void setup() {
  Serial.begin(9600);

  // Init drivers (4 boards, 2 channels each)
  for (int i = 0; i < DRIVER_COUNT; i++) {
    pinMode(DRV[i].ENA, OUTPUT);
    pinMode(DRV[i].IN1, OUTPUT);
    pinMode(DRV[i].IN2, OUTPUT);

    pinMode(DRV[i].ENB, OUTPUT);
    pinMode(DRV[i].IN3, OUTPUT);
    pinMode(DRV[i].IN4, OUTPUT);

    setForwardA(DRV[i]);
    setForwardB(DRV[i]);

    analogWrite(DRV[i].ENA, 0);
    analogWrite(DRV[i].ENB, 0);
  }

  pinMode(PUMP_EN, OUTPUT);
  pinMode(PUMP_IN1, OUTPUT);
  pinMode(PUMP_IN2, OUTPUT);
  digitalWrite(PUMP_IN1, HIGH);
  digitalWrite(PUMP_IN2, LOW);
  analogWrite(PUMP_EN, 0);

  Serial.println("READY;FAN");
  Serial.println("CMD: F1=.. F2=.. F3=.. F4=.. F5=.. F6=.. F7=.. F8=.. P1=..");
}

void loop() {
  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  parseLine(line);
  applyOutputs();
}
