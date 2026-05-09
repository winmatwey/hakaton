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
  delay(1000);  // даём Serial время инициализироваться
  SPI.begin();
  rfid.PCD_Init();
  delay(100);
  Serial.println("READY");  // сигнал о готовности
}

void loop() {
  // Проверяем наличие новой карты
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) {
    delay(50);  // ✅ Добавлена задержка чтобы не жарить CPU
    return;
  }

  // Получаем UID карты
  String uid = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";  // Добавляем "0" для однозначных чисел
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();

  uint32_t now = millis();
  
  // ✅ ИСПРАВЛЕНА ЛОГИКА: используем && (И) вместо || (ИЛИ)
  // Отправляем если:
  // - UID другой (новая карта) ИЛИ
  // - тот же UID но прошло >= 2000ms (повторное сканирование)
  if (uid != lastUID || (uid == lastUID && now - lastReadTime >= 2000)) {
    Serial.println("UID:" + uid);
    lastUID      = uid;
    lastReadTime = now;
  }

  // Завершаем чтение
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  
  delay(100);  // небольшая задержка между циклами
}
