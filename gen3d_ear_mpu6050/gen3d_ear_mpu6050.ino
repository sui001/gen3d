/*
 * PROJECT: gen3d / Birch as Cybernetic System
 * DEVICE:  Vibration-sense node -- the printer body becomes the sculptor hand
 *
 * Replaces the KY-038 microphone approach. A mic on a constant-speed circular
 * path picks up almost no variation -- the printer sound barely changes,
 * but its vibration does, because every direction change drives the frame
 * differently in X vs Y. The MPU-6050 reads that directly off the chassis.
 *
 * Samples acceleration magnitude at ~1kHz, removes the gravity/orientation
 * baseline via a running mean, computes RMS vibration intensity over each
 * window, then EMA-centres it so the output is bipolar: calmer than recent
 * average = negative (wall contracts), more violent = positive (expands).
 * Streams "freqHz,voice" CSV over USB serial every 100ms -- same format as
 * the old sketch, so main.py on the Pi needs no changes.
 *
 * WIRING:
 *   GY-521 / MPU-6050  VCC -> 3V3
 *   GY-521 / MPU-6050  GND -> GND
 *   GY-521 / MPU-6050  SCL -> GPIO9
 *   GY-521 / MPU-6050  SDA -> GPIO8
 *   GY-521 / MPU-6050  AD0 -> GND   (I2C address 0x68)
 *   GY-521 / MPU-6050  INT -> not connected
 *
 * MOUNTING: bolt or zip-tie directly to the printer frame/gantry -- not on
 * the print itself. Closer to the X/Y motors = stronger signal.
 *
 * ARDUINO IDE SETTINGS:
 *   Board:            ESP32S3 Dev Module
 *   USB CDC On Boot:  Enabled
 *   Flash Size:       4MB (32Mb)
 *   Partition Scheme: Default 4MB with spiffs
 *   Upload Mode:      UART0 / Hardware CDC
 *   Upload Speed:     921600
 */

#define VERSION       "1.0"

#include <Wire.h>

// -- config --
#define SDA_PIN        8
#define SCL_PIN        9
#define MPU_ADDR       0x68

#define SAMPLE_COUNT   128    // samples per window
#define SAMPLE_US      1000   // ~1kHz
#define AMP_SCALE      0.02   // rms-g deviation for full +/-1 output swing
#define SEND_INTERVAL  100    // ms between serial outputs

float samples[SAMPLE_COUNT];
float g_freqHz = 0.0f;
unsigned long lastSend = 0;

void mpuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  mpuWrite(0x6B, 0x00);  // PWR_MGMT_1 -- wake up, internal 8MHz clock
  delay(100);
}

// Reads accel XYZ, returns magnitude in g (raw, includes gravity)
float readAccelMagnitude() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);  // ACCEL_XOUT_H
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 6, true);

  int16_t ax = (Wire.read() << 8) | Wire.read();
  int16_t ay = (Wire.read() << 8) | Wire.read();
  int16_t az = (Wire.read() << 8) | Wire.read();

  // default sensitivity +/-2g -> 16384 LSB/g
  float fx = ax / 16384.0f;
  float fy = ay / 16384.0f;
  float fz = az / 16384.0f;

  return sqrtf(fx * fx + fy * fy + fz * fz);
}

float listenToMachine() {
  // collect samples
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    samples[i] = readAccelMagnitude();
    delayMicroseconds(SAMPLE_US);
  }

  // mean (gravity + steady orientation baseline)
  float sum = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) sum += samples[i];
  float mean = sum / SAMPLE_COUNT;

  // RMS of deviation from mean -- vibration intensity
  float variance = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    float d = samples[i] - mean;
    variance += d * d;
  }
  float rms = sqrtf(variance / SAMPLE_COUNT);

  // zero-crossing count -> rough vibration frequency (display only)
  int crossings = 0;
  for (int i = 1; i < SAMPLE_COUNT; i++) {
    if ((samples[i-1] - mean) * (samples[i] - mean) < 0) crossings++;
  }
  float windowSec = (float)(SAMPLE_COUNT * SAMPLE_US) / 1000000.0f;
  g_freqHz = (crossings / 2.0f) / windowSec;

  // EMA auto-calibrates centre to ambient vibration -- no manual tuning needed
  static float avg_rms = -1.0f;
  if (avg_rms < 0) avg_rms = rms;
  avg_rms = avg_rms * 0.95f + rms * 0.05f;  // ~20-sample window

  float voice = (rms - avg_rms) / (float)AMP_SCALE;  // +/- around mean
  return max(-1.0f, min(1.0f, voice));
}

void loop() {
  float voice = listenToMachine();

  unsigned long now = millis();
  if (now - lastSend >= SEND_INTERVAL) {
    Serial.print(g_freqHz, 1);
    Serial.print(',');
    Serial.println(voice, 4);
    lastSend = now;
  }
}