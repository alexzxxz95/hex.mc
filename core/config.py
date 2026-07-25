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
core/config.py
===============
Все, що стосується конфігурації застосунку: шляхи до файлів,
завантаження/збереження CONF, теми оформлення, статичні дані
(координати гексагонів, піксельний шрифт).

Ядро (core/app.py) та модулі (modules/*.py) читають/пишуть свій
стан через CONF["modules"][<key>] — окремий неймспейс на модуль,
щоб додавання нового модуля не вимагало правок тут.
"""
import copy
import os
import json
from datetime import datetime

# ---------------------------------------------------------------------------
# Шляхи
# ---------------------------------------------------------------------------
# BASE_DIR має вказувати на корінь проєкту (туди, де лежить main.pyw),
# а не на директорію core/ — тому піднімаємось на рівень вище від цього
# файла. Це відтворює поведінку старої однофайлової версії, де __file__
# збігався з коренем проєкту.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE        = os.path.join(BASE_DIR, "hexmc_config.json")
LEGACY_CONFIG_FILE = os.path.join(BASE_DIR, "cyber_config.json")   # стара назва — для одноразової міграції
ERROR_LOG_FILE      = os.path.join(BASE_DIR, "hexmc_error.log")


def _log_startup_error(msg: str):
    """У .pyw немає консолі, тож print() нікуди не виводиться і помилка губиться.
    Пишемо критичні помилки конфігу у текстовий файл поруч зі скриптом."""
    try:
        with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass  # якщо вже й це не вдається — далі допомогти нічим


# ---------------------------------------------------------------------------
# Координати гексагонів
# ---------------------------------------------------------------------------
ROW_CONFIG = [15, 16, 15, 16, 15]


def build_hex_coords(row_config, dx=52, dy=45):
    """Генерує координати центрів гексагонів для кожного рядка."""
    coords = []
    for r, count in enumerate(row_config):
        offset_x = 26 if (r % 2 == 0) else 0
        row = [(50 + offset_x + i * dx, 40 + r * dy) for i in range(count)]
        coords.append(row)
    return coords


HEX_COORDS = build_hex_coords(ROW_CONFIG)

# ---------------------------------------------------------------------------
# Піксельний шрифт 3×5 (використовується модулем "clock", але це статичні
# дані без побічної логіки — тримаємо в config поруч з іншими константами)
# ---------------------------------------------------------------------------
FONT_3x5 = {
    '0': [0b011, 0b101, 0b000, 0b101, 0b011],
    '1': [0b001, 0b000, 0b001, 0b000, 0b001],
    '2': [0b011, 0b001, 0b011, 0b100, 0b011],
    '3': [0b011, 0b001, 0b011, 0b001, 0b011],
    '4': [0b001, 0b010, 0b010, 0b111, 0b001],
    '5': [0b011, 0b100, 0b011, 0b001, 0b011],
    '6': [0b011, 0b100, 0b011, 0b101, 0b011],
    '7': [0b111, 0b010, 0b010, 0b000, 0b010],
    '8': [0b011, 0b101, 0b011, 0b101, 0b011],
    '9': [0b011, 0b101, 0b011, 0b001, 0b011],
    ':': [0b000, 0b100, 0b000, 0b100, 0b000],
    ' ': [0b000, 0b000, 0b000, 0b000, 0b000],
}

# Позиції годинника (без накладення з двокрапкою).
# Сітка 16 пікселів: H H _ H H : M M _ M M  (зсуви: 0,4,8=':',9,13)
CLOCK_POS = {
    "h0": 0, "h1": 4, "colon": 8, "m0": 10, "m1": 13
}

# ---------------------------------------------------------------------------
# Теми оформлення
# ---------------------------------------------------------------------------
THEMES = {
    "dark": {
        "bg":          "#201000",
        "panel_bg":    "#050200",
        "preview_bg":  "#201000",
        "accent":      "#FF7300",
        "accent_dim":  "#4D3500",
        "text_dark":   "#1A0F00",
        "alert":       "#FF3300",
        "glow":        "#664400",
    },
    "light": {
        "bg":          "#DCD6C9",
        "panel_bg":    "#CFC8B8",
        "preview_bg":  "#F4F1EA",
        "accent":      "#CC5200",
        "accent_dim":  "#E0B78F",
        "text_dark":   "#120A00",
        "alert":       "#B32200",
        "glow":        "#E8A876",
    },
}

# ---------------------------------------------------------------------------
# Конфіг за замовчуванням
# ---------------------------------------------------------------------------
# "modules" — неймспейс для стану вбудованих та підключних модулів.
# Кожен модуль читає/пише ЛИШЕ свою гілку через get_state()/set_state()
# (див. core/plugin_base.py). Ядро вміст цих гілок не інтерпретує.
DEFAULT_CONFIG = {
    "theme": copy.deepcopy(THEMES["dark"]),
    "ui": {
        "font_name":   "Courier New",
        "header_text": "PROFILE: HEXEL ONE",
        "logo_text":   "HEX.MC",
        "version":     "v0.6.2",
        "low_perf_mode": False,   # True → відключає glow-ефекти та важкий
                                   # пост-процесинг прев'ю (див. SETTINGS)
        "hide_preview": False,    # True → ховає в'юпорт прев'ю (SETTINGS),
                                   # так само як модуль може ховати SYSTEM_DATA_FEED
    },
    "serial": {
        "auto_connect": False,   # чи намагатись автопідключитись при старті
        "last_port":    "",      # останній використаний порт (для інформації)
    },
    "last_state": {
        "mode": "clock",
    },
    # Версія формату "profiles" — використовується для одноразового скидання
    # старих профілів (повний знімок стану) при переході на новий формат.
    "profiles_format": 2,
    "profiles": [None, None, None, None, None, None],
    # Прапорці вмикання/вимикання пунктів меню (SETTINGS → МОДУЛІ).
    # Ключі заповнюються динамічно (core/app.py) з реєстру модулів,
    # оскільки конкретний набір модулів config.py не знає — тут лише
    # порожній словник за замовчуванням.
    "modules_enabled": {},
    "modules": {
        "clock":   {},
        "screen":  {"folder": ""},
        "softbox": {},
        "gif":     {"folder": ""},
        "scripts": {"folder": ""},
    },
}


# ---------------------------------------------------------------------------
# Завантаження / збереження конфігу
# ---------------------------------------------------------------------------
def load_config() -> dict:
    conf = copy.deepcopy(DEFAULT_CONFIG)

    # Якщо новий файл ще не існує, але є старий (cyber_config.json) —
    # одноразово підхоплюємо налаштування з нього (міграція назви).
    source_file = None
    if os.path.exists(CONFIG_FILE):
        source_file = CONFIG_FILE
    elif os.path.exists(LEGACY_CONFIG_FILE):
        source_file = LEGACY_CONFIG_FILE

    if source_file:
        try:
            with open(source_file, "r", encoding="utf-8") as f:
                user_conf = json.load(f)

            # Старий формат "profiles" зберігав ПОВНИЙ знімок стану програми
            # (mode, softbox/gif слайдери тощо). Новий формат зберігає лише
            # параметри SCREEN. Якщо у файлі немає позначки нового формату —
            # профілі несумісні, обнуляємо слоти один раз.
            legacy_profiles = user_conf.get("profiles_format") != conf["profiles_format"]

            for k, v in user_conf.items():
                if k in conf and isinstance(conf[k], dict) and isinstance(v, dict):
                    conf[k].update(v)
                elif k in conf:
                    conf[k] = v

            if legacy_profiles:
                conf["profiles"]        = [None, None, None, None, None, None]
                conf["profiles_format"] = DEFAULT_CONFIG["profiles_format"]

            # Гарантуємо, що всі неймспейси модулів присутні навіть у
            # старому конфізі, де ключа "modules" ще не було.
            for mod_key, mod_default in DEFAULT_CONFIG["modules"].items():
                conf["modules"].setdefault(mod_key, copy.deepcopy(mod_default))

        except (json.JSONDecodeError, OSError) as e:
            _log_startup_error(f"Не вдалось завантажити конфіг ({source_file}): {e}")

    return conf


def save_config(conf: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
    except OSError as e:
        _log_startup_error(f"Не вдалось зберегти конфіг ({CONFIG_FILE}): {e}")


# ---------------------------------------------------------------------------
# CONF ініціалізується одразу при першому імпорті цього модуля (так само,
# як у монолітній версії) — усі інші файли (core/app.py, modules/*.py)
# роблять `from core.config import CONF` і отримують ЖИВИЙ словник:
# зміни, зроблені через CONF["..."] = ..., бачать усі імпортери одразу.
# ---------------------------------------------------------------------------
CONF = load_config()

if not os.path.exists(CONFIG_FILE):
    save_config(CONF)

for _key in THEMES["dark"]:
    if _key in CONF["theme"]:
        THEMES["dark"][_key] = CONF["theme"][_key]