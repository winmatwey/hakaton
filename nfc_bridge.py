#!/usr/bin/env python3
"""
nfc_bridge.py — Мост между RC522 (Arduino/ESP) и Flask-сервером
================================================================

Запуск:
    python nfc_bridge.py              # авто-поиск COM-порта
    python nfc_bridge.py COM3         # явно указать порт

Установка (важно: именно pyserial, не serial!):
    pip uninstall serial               # удалить конфликтующий пакет
    pip install pyserial               # установить правильный
"""

import sys
import time
import urllib.request
import urllib.error
import json

# ── Проверка pyserial ─────────────────────────────────────────
try:
    import serial
except ImportError:
    print("=" * 55)
    print("ОШИБКА: pyserial не установлен!")
    print("Выполните в командной строке:")
    print("  pip install pyserial")
    print("=" * 55)
    sys.exit(1)

# Проверяем что это именно pyserial, а не пакет serial
if not hasattr(serial, 'Serial'):
    print("=" * 55)
    print("ОШИБКА: установлен неправильный пакет 'serial'!")
    print("Выполните:")
    print("  pip uninstall serial")
    print("  pip install pyserial")
    print("=" * 55)
    sys.exit(1)

# serial.tools доступен только в pyserial
try:
    import serial.tools.list_ports
    HAS_LIST_PORTS = True
except ImportError:
    HAS_LIST_PORTS = False

# ── Настройки ─────────────────────────────────────────────────
SERVER_URL   = "http://127.0.0.1:5000/api/nfc/push"
BAUD_RATE    = 9600
READ_TIMEOUT = 2      # сек
DEBOUNCE_S   = 2.0    # сек — не повторять тот же UID
RETRY_DELAY  = 5      # сек — пауза при ошибке

# ── Поиск COM-порта ───────────────────────────────────────────

def find_port():
    """Автоматически найти COM-порт с Arduino."""

    if not HAS_LIST_PORTS:
        # serial.tools недоступен — просим указать вручную
        print("[!] serial.tools.list_ports недоступен.")
        print("    Укажите порт вручную: python nfc_bridge.py COM3")
        return None

    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("[!] COM-порты не найдены. Убедитесь что Arduino подключена.")
        return None

    print("Найдены COM-порты:")
    for p in ports:
        # Исправка: используем :<15 для совместимости с длинными портами Linux
        print(f"  {p.device:<15} — {p.description}")

    keywords = [
        "arduino", "ch340", "ch341", "cp210", "ftdi",
        "usb serial", "usb-serial", "uart", "esp32", "esp8266",
    ]
    for p in ports:
        desc = (p.description or "").lower()
        dev  = (p.device or "").lower()
        if any(kw in desc or kw in dev for kw in keywords):
            print(f"→ Выбран: {p.device} ({p.description})")
            return p.device

    # Не нашли по ключевым словам — берём первый
    print(f"→ Берём первый: {ports[0].device}")
    return ports[0].device


def find_port_windows_fallback():
    """
    Запасной вариант для Windows если serial.tools недоступен:
    перебираем COM1..COM20 и пробуем открыть.
    """
    print("[..] Пробую COM-порты перебором (COM1–COM20)...")
    for i in range(1, 21):
        port = f"COM{i}"
        try:
            s = serial.Serial(port, BAUD_RATE, timeout=0.5)
            s.close()
            print(f"→ Найден: {port}")
            return port
        except serial.SerialException:
            pass
    return None

# ── Отправка на сервер ────────────────────────────────────────

def send_uid(uid: str) -> bool:
    payload = json.dumps({"uid": uid}).encode("utf-8")
    req = urllib.request.Request(
        SERVER_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode())
            return body.get("ok", False)
    except urllib.error.URLError as e:
        print(f"  [!] Сервер недоступен: {e.reason}")
        print(f"      Убедитесь что app.py запущен и работает на {SERVER_URL}")
        return False
    except Exception as e:
        print(f"  [!] Ошибка отправки: {e}")
        return False

# ── Основной цикл ─────────────────────────────────────────────

def wait_for_arduino_ready(ser, timeout=5):
    """
    Ожидает от Arduino сигнала READY с очисткой буфера.
    Возвращает True если Arduino готовой, False если timeout.
    """
    print("[Init] Ожидаю инициализации Arduino...")
    ser.reset_input_buffer()  # Очищаем буфер от мусора
    time.sleep(0.5)
    
    start = time.time()
    while time.time() - start < timeout:
        try:
            if ser.in_waiting:
                raw = ser.readline()
                try:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        print(f"[Init] {line}")
                        if "READY" in line:
                            print("[Init] ✓ Arduino готовой!")
                            return True
                except Exception:
                    pass
        except Exception as e:
            print(f"[!] Ошибка при ожидании READY: {e}")
        
        time.sleep(0.1)
    
    print("[!] Timeout: Arduino не ответил сигналом READY")
    return False


def run(port: str) -> bool:
    print(f"\n[RC522] Подключаюсь к {port} @ {BAUD_RATE} бод...")
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=READ_TIMEOUT)
    except serial.SerialException as e:
        print(f"[!] Не удалось открыть {port}: {e}")
        return False

    # Даём Arduino время на перезагрузку и инициализацию
    time.sleep(2)
    
    # Ожидаем сигнала READY
    if not wait_for_arduino_ready(ser, timeout=5):
        print("[!] Arduino не инициализировалась, переподключаюсь...")
        ser.close()
        return False

    print(f"[RC522] Подключён! Ожидаю карты...")
    print(f"        Поднесите карту к считывателю...\n")

    last_uid      = ""
    last_uid_time = 0.0
    no_data_count = 0  # счётчик timeout'ов
    MAX_TIMEOUTS  = 60  # если 60 раз подряд нет данных, переподключаемся

    try:
        while True:
            try:
                raw = ser.readline()
            except serial.SerialException as e:
                print(f"[!] Потеря связи с {port}: {e}")
                break

            if not raw:
                # Timeout - нет данных
                no_data_count += 1
                if no_data_count > MAX_TIMEOUTS:
                    print(f"[!] Слишком много timeout'ов ({MAX_TIMEOUTS}), переподключаюсь...")
                    break
                continue
            
            # Данные получены - сбрасываем счётчик
            no_data_count = 0

            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue

            if not line:
                continue

            print(f"[Serial] {line}")

            if not line.startswith("UID:"):
                continue

            uid = line[4:].strip().upper()
            if not uid:
                continue

            now = time.time()
            if uid == last_uid and (now - last_uid_time) < DEBOUNCE_S:
                print(f"  [~] Дублирование — пропускаю")
                continue

            print(f"  [->] Отправляю UID {uid} на сервер...")
            if send_uid(uid):
                print(f"  [OK] Принято: {uid}")
                last_uid      = uid
                last_uid_time = now
            else:
                print(f"  [X]  Не принято: {uid}")

    except KeyboardInterrupt:
        print("\n[RC522] Остановлен (Ctrl+C)")
    finally:
        ser.close()

    return True


def main():
    # Определяем порт
    if len(sys.argv) > 1:
        port = sys.argv[1]
        print(f"[RC522] Порт из аргумента: {port}")
    else:
        port = find_port()
        if not port:
            # Запасной вариант для Windows
            port = find_port_windows_fallback()
        if not port:
            print("\n[!] Не удалось найти порт автоматически.")
            print("    Запустите с явным указанием порта:")
            print("    python nfc_bridge.py COM3")
            print("\n    Какой порт? Arduino IDE → Инструменты → Порт")
            sys.exit(1)

    # Цикл переподключения
    while True:
        ok = run(port)
        if not ok:
            print(f"\n[RC522] Переподключение через {RETRY_DELAY} сек...")
            time.sleep(RETRY_DELAY)
        else:
            break


if __name__ == "__main__":
    main()
