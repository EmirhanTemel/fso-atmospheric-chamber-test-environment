#include <OneWire.h>
#include <DallasTemperature.h>
#include <DHT.h>

// ===== CONFIG =====
const bool ACTIVE_LOW = true;

const int HEAT_PIN = 23;
const int FOG_PIN  = 24;

const int ONE_WIRE_BUS = 2;     // DS18B20 data
const int DHT_PIN = 3;          // DHT22 data
#define DHTTYPE DHT22

const unsigned long SENS_PERIOD = 1000;

// ===== GP2Y1010 (Dust) CONFIG =====
const int DUST_LED_PIN = 5;   
const int DUST_AIN_PIN = A0;  

const int DUST_LED_ON_DELAY_US = 280;
const int DUST_LED_ON_TIME_US  = 40;
const int DUST_LED_OFF_TIME_US = 9680;

const float DUST_V0 = 0.15;   
const float DUST_K  = 0.50;   


// ===== INTERNAL =====
unsigned long lastSend = 0;

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature ds(&oneWire);
DHT dht(DHT_PIN, DHTTYPE);

void writeOut(int pin, int on) {
  if (ACTIVE_LOW) digitalWrite(pin, on ? LOW : HIGH);
  else           digitalWrite(pin, on ? HIGH : LOW);
}

int readDustAdcOnce() {
  // LED active LOW
  digitalWrite(DUST_LED_PIN, LOW);                 // LED ON
  delayMicroseconds(DUST_LED_ON_DELAY_US);

  int adc = analogRead(DUST_AIN_PIN);

  delayMicroseconds(DUST_LED_ON_TIME_US);
  digitalWrite(DUST_LED_PIN, HIGH);                // LED OFF
  delayMicroseconds(DUST_LED_OFF_TIME_US);

  return adc;
}

int readDustAdcAvg(uint8_t n = 5) {
  long sum = 0;
  for (uint8_t i = 0; i < n; i++) sum += readDustAdcOnce();
  return (int)(sum / (long)n);
}

float dustMgM3FromVoltage(float v) {
  float mg = (v - DUST_V0) / DUST_K;
  if (mg < 0) mg = 0;
  return mg;
}

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(20);

  pinMode(HEAT_PIN, OUTPUT);
  pinMode(FOG_PIN, OUTPUT);

  writeOut(HEAT_PIN, 0);
  writeOut(FOG_PIN, 0);

  ds.begin();
  ds.setResolution(10);
  dht.begin();

  // GP2Y1010
  pinMode(DUST_LED_PIN, OUTPUT);
  digitalWrite(DUST_LED_PIN, HIGH); // LED OFF

  Serial.println("READY;IO");
}

void handleCommand(String line) {
  // IO;HEAT=0 | IO;FOG=1
  if (!line.startsWith("IO;")) { Serial.println("ERR;UNKNOWN_PREFIX"); return; }

  int eq = line.indexOf('=');
  if (eq < 0) { Serial.println("ERR;BAD_FMT"); return; }

  String key = line.substring(3, eq);
  int val = line.substring(eq + 1).toInt();
  val = val ? 1 : 0;

  if (key == "HEAT") writeOut(HEAT_PIN, val);
  else if (key == "FOG")  writeOut(FOG_PIN, val);
  else { Serial.println("ERR;BAD_KEY"); return; }

  Serial.println("OK");
}

void publishSensors() {
  ds.requestTemperatures();

  int count = ds.getDeviceCount();
  float t1 = (count >= 1) ? ds.getTempCByIndex(0) : NAN;
  float t2 = (count >= 2) ? ds.getTempCByIndex(1) : NAN;
  float t3 = (count >= 3) ? ds.getTempCByIndex(2) : NAN;
  float t4 = (count >= 4) ? ds.getTempCByIndex(3) : NAN;

  float tamb = dht.readTemperature();
  float h = dht.readHumidity();

  // Dust
  int dustAdc = readDustAdcAvg(10);
  float dustV = dustAdc * (5.0 / 1023.0);
  float dustMg = dustMgM3FromVoltage(dustV);

  Serial.print("SENS;DSN=");
  Serial.print(count);

  Serial.print(";T1=");
  if (isnan(t1)) Serial.print("nan"); else Serial.print(t1, 1);

  Serial.print(";T2=");
  if (isnan(t2)) Serial.print("nan"); else Serial.print(t2, 1);

  Serial.print(";T3=");
  if (isnan(t3)) Serial.print("nan"); else Serial.print(t3, 1);

  Serial.print(";T4=");
  if (isnan(t4)) Serial.print("nan"); else Serial.print(t4, 1);



  Serial.print(";TAMB=");
  if (isnan(tamb)) Serial.print("nan"); else Serial.print(tamb, 1);

  Serial.print(";H=");
  if (isnan(h)) Serial.print("nan"); else Serial.print(h, 1);

  
  Serial.print(";DUSTMG=");
  Serial.print(dustMg, 3);

  Serial.print(";DUSTV=");
  Serial.print(dustV, 3);

  Serial.print(";DUSTADC=");
  Serial.print(dustAdc);

  Serial.println();
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) handleCommand(line);
  }

  if (millis() - lastSend >= SENS_PERIOD) {
    lastSend = millis();
    publishSensors();
  }
}
