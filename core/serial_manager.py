"""
# Copyright (C) 2026 Artem Honcharov(Hon4) / OWN.LAB
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
=================
core/serial_manager.py
=======================
Ізольована робота з COM-портом. Логіка перенесена 1:1 з монолітної
версії — модулі (screen/softbox/gif/...) отримують екземпляр цього
класу через ModuleContext.serial_mgr і не працюють з serial напряму.
"""
import threading
import time

import serial
import serial.tools.list_ports


class SerialManager:
    """Управляє підключенням до серійного порту."""

    BAUD_RATE  = 921600
    FPS_LIMIT  = 0.0333   # ~30 FPS

    # Espressif native-USB VID (ESP32-C3/S3 з вбудованим USB-CDC).
    # PID навмисно не фільтруємо — залежить від конкретної збірки плати.
    TARGET_VID        = 0x303A
    HANDSHAKE_TX       = b'P'
    HANDSHAKE_RX       = b'K'
    HANDSHAKE_BAUD     = 921600
    HANDSHAKE_TIMEOUT  = 0.3   # с — час очікування відповіді від пристрою

    def __init__(self):
        self._ser            : serial.Serial | None = None
        self._lock           = threading.Lock()
        self._last_send_time = 0.0
        self._rx_buffer      = b""   # накопичувач для неповних рядків телеметрії

    # --- автовизначення пристрою --------------------------------------------

    @classmethod
    def _candidate_ports(cls) -> list[str]:
        """Повертає порти, що відповідають VID Espressif.
        Якщо жоден порт не підпадає під фільтр (інша плата/драйвер),
        повертаємо повний список — хай handshake розбереться сам."""
        all_ports = list(serial.tools.list_ports.comports())
        vid_match = [p.device for p in all_ports if p.vid == cls.TARGET_VID]
        return vid_match if vid_match else [p.device for p in all_ports]

    @classmethod
    def _handshake(cls, port: str) -> bool:
        """Коротко відкриває порт і перевіряє відповідь 'K' на пінг 'P'."""
        try:
            with serial.Serial(port, cls.HANDSHAKE_BAUD, timeout=cls.HANDSHAKE_TIMEOUT) as s:
                time.sleep(1.6)          # час на reset/boot ESP32 після відкриття порту
                s.reset_input_buffer()
                s.write(cls.HANDSHAKE_TX)
                s.flush()
                reply = s.read(1)
                return reply == cls.HANDSHAKE_RX
        except Exception:
            return False

    @classmethod
    def find_device_port(cls) -> str | None:
        """Шукає HEX_MC серед доступних портів: спочатку фільтр по VID,
        потім підтвердження handshake'ом. Повертає ім'я порту або None."""
        for port in cls._candidate_ports():
            if cls._handshake(port):
                return port
        return None

    # --- публічний інтерфейс -----------------------------------------------

    def connect(self, port: str) -> str:
        """Повертає рядок статусу."""
        with self._lock:
            self._close_unsafe()
            try:
                self._ser = serial.Serial(port, self.BAUD_RATE, timeout=0.01)
                return f"HW_LINK_OK: {port}"
            except Exception as e:
                self._ser = None
                return f"LINK_ERROR: {str(e)[:60]}"

    def disconnect(self):
        with self._lock:
            self._close_unsafe()

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._ser is not None and self._ser.is_open

    def send(self, data: bytearray) -> bool:
        """Надсилає дані з обмеженням FPS. Повертає True якщо відправлено."""
        now = time.time()
        if now - self._last_send_time < self.FPS_LIMIT:
            return False
        with self._lock:
            if self._ser is None or not self._ser.is_open:
                return False
            try:
                self._ser.write(data)
                self._ser.flush()
                self._last_send_time = now
                return True
            except Exception:
                return False

    def readline_nowait(self) -> str | None:
        """Неблокуюче читання одного повного рядка (до '\\n') від пристрою.
        Призначено для зворотних даних (телеметрія: температура,
        освітленість, акселерометр тощо). Повертає None, якщо повного
        рядка ще не накопичено, або якщо порт не підключено."""
        with self._lock:
            if self._ser is None or not self._ser.is_open:
                return None
            try:
                waiting = self._ser.in_waiting
                if waiting:
                    self._rx_buffer += self._ser.read(waiting)
            except Exception:
                return None

            if b"\n" not in self._rx_buffer:
                return None
            line, _, rest = self._rx_buffer.partition(b"\n")
            self._rx_buffer = rest
            try:
                return line.decode("utf-8", errors="ignore").strip()
            except Exception:
                return None

    # --- приватне ----------------------------------------------------------

    def _close_unsafe(self):
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._rx_buffer = b""
