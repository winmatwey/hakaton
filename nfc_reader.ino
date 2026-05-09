/*
 * rfid_rc522.ino — Считыватель RFID RC522
 * =========================================
 *
 * ПОДКЛЮЧЕНИЕ — ТОЛЬКО 6 ПРОВОДОВ (RST не нужен!):
 *
 *   RC522       Arduino Uno/Nano
 *   ─────────   ────────────────
 *   3.3V    →   3.3V
 *   GND     →   GND
 *   SDA     →   D10
 *   SCK     →   D13
 *   MOSI    →   D11
 *   MISO    →   D12
 *   RST     →   3.3V  (просто соедините с 3.3V, не с Arduino)
 *   IRQ     →   не подключать
 *
 *  ┌──────────────────────────────────────────┐
 *  │  RC522 RST подключается к 3.3V модуля,  │
 *  │  а не к пину Arduino — провод не нужен! │
 *  └──────────────────────────────────────────┘
 *
 * БИБЛИОТЕКА:
 *   Arduino IDE → Инструменты → Управление библиотеками
 *   Найти: MFRC522  →  Установить (от Miguel Balboa)
 */

#include <SPI.h>
#include <MFRC522.h>

#define SS_PIN   10    // SDA  → D10
#define RST_PIN  UINT8_MAX  // RST подключён к 3.3V — пин не нужен

MFRC522 rfid(SS_PIN, RST_PIN);

String   lastUID      = "";
uint32_t lastReadTime = 0;

void setup() {
  Serial.begin(9600);
  SPI.begin();
  rfid.PCD_Init();
  Serial.println("READY");
}

void loop() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    return;
  }

  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();

  uint32_t now = millis();
  if (uid != lastUID || now - lastReadTime >= 2000) {
    Serial.println("UID:" + uid);
    lastUID      = uid;
    lastReadTime = now;
  }

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}
