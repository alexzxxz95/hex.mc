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
===========
core/app.py
===========
MatrixApp — рушій HEX_MC. Володіє вікном, боковою панеллю, прев'ю,
з'єднанням по COM-порту, системним логом і треєм. Про конкретні режими
(FEED/SCREEN/SOFTBOX/GIF/SCRIPTS) нічого не знає — вони підключаються
через discover_modules() (core/plugin_base.py) і кожен сам будує свою
панель керування та генерує кадр.

Додати новий режим = покласти новий файл у modules/, який реєструє
себе через register_module(). Цей файл більше НЕ редагується.
"""
import os
import queue
import threading
import time
from datetime import datetime

import customtkinter as ctk
import numpy as np
import serial.tools.list_ports
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk

from core.config import CONF, THEMES, save_config
from core.plugin_base import HexModuleBase, ModuleContext, discover_modules
from core.preview_renderer import PreviewRenderer
from core.serial_manager import SerialManager
from core.theme import GlowLabel

try:
    import pystray
    PYSTRAY_AVAILABLE = True
except Exception:
    # На деяких системах (напр. Linux без GTK/AppIndicator) pystray може
    # впасти не з ImportError, а з іншим винятком під час імпорту бекенду.
    # У такому разі трей просто вимикається, програма працює як раніше.
    PYSTRAY_AVAILABLE = False


def build_tray_icon_image() -> Image.Image:
    """Генерує просту іконку для трею (без залежності від зовнішніх файлів)."""
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(18, 18, 18, 255), outline=(255, 115, 0, 255), width=4)
    draw.regular_polygon((size // 2, size // 2, size // 3), n_sides=6, fill=(255, 115, 0, 255))
    return img


class MatrixApp(ctk.CTk):


    DEFAULT_FEED_HEIGHT = 180   # стандартна висота системного логу; модулі можуть
                                # звузити її через HexModuleBase.log_feed_height

    def __init__(self):
        super().__init__()

        self.current_theme = "dark"
        self.title(f"HEX.MC {CONF['ui']['version']}")
        self.geometry("1100x900")
        self.overrideredirect(True)   # прибирає стандартну рамку/титульний рядок ОС;
                                       # переміщення вікна тепер за верхню помаранчеву
                                       # смужку (header) — див. _start_move/_do_move нижче.
        self.configure(fg_color=THEMES["dark"]["bg"])
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        self.running     = True
        self._paused     = False   # True, коли вікно згорнуте в трей — ставиться на паузу
                                    # лише генерація прев'ю; рендер і serial-відправка тривають
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.blink_state = True
        self.last_logs: list[str] = []

        # Черги міжпотокової комунікації
        self.image_queue  = queue.Queue(maxsize=2)
        self.log_queue    = queue.Queue(maxsize=12)
        self.sensor_queue = queue.Queue(maxsize=4)
        self.sensor_data: dict = {}

        # Перебивання активного режиму (напр. сповіщення notify.py) — будь-
        # який модуль може на короткий час показати свій кадр ПОВЕРХ того,
        # що зараз рендерить активний режим, без перемикання меню.
        self._interrupt_lock  = threading.Lock()
        self._interrupt_grid  = None
        self._interrupt_until = 0.0

        # Ядрові менеджери
        self.serial_mgr       = SerialManager()
        self.preview_renderer = PreviewRenderer()

        # --- Модулі ----------------------------------------------------
        # discover_modules() сканує modules/ і повертає готові екземпляри.
        # Порядок реєстрації = порядок пунктів меню.
        modules_list   = discover_modules()
        self.modules: dict[str, HexModuleBase] = {m.key: m for m in modules_list}
        self.module_order: list[str] = [m.key for m in modules_list]
        self._module_panels: dict[str, ctk.CTkFrame] = {}   # кеш build_ui() на модуль

        # Прапорці вмикання/вимикання пунктів меню (SETTINGS → МОДУЛІ).
        # Заповнюємо відсутні ключі значенням True (усі модулі увімкнені
        # за замовчуванням), щоб додавання нового modules/*.py файла в
        # майбутньому не вимагало правок конфігу.
        CONF.setdefault("modules_enabled", {})
        for key in self.module_order:
            CONF["modules_enabled"].setdefault(key, True)
        # Модулі, для яких on_engine_ready() вже було викликано — щоб не
        # запускати фоновий потік вдруге, якщо модуль вимкнули й знову
        # увімкнули за час роботи застосунку.
        self._engine_ready_called: set[str] = set()

        enabled_order = [k for k in self.module_order if CONF["modules_enabled"].get(k, True)]
        self.mode = enabled_order[0] if enabled_order else (self.module_order[0] if self.module_order else None)
        self.active_module = None

        # CONNECTION і SETTINGS тепер НЕ окремі спливаючі вікна, а панелі,
        # що показуються у controls_box — там само, де зазвичай сидить
        # панель параметрів активного модуля (єдиний, цілісний робочий
        # простір замість зайвих вікон). self._active_view відстежує, що
        # зараз показано: None → панель активного модуля, "connection" /
        # "settings" → одна з цих псевдо-панелей. Самі панелі будуються
        # лише один раз (лениво, при першому відкритті) і далі живуть
        # завжди — просто ховаються/показуються через pack/pack_forget.
        self._active_view = None
        self.conn_panel        = None
        self.port_menu        = None
        self.autodetect_btn   = None
        self.connect_btn      = None
        self.auto_connect_chk = None
        self.port_var          = ctk.StringVar()
        self.auto_connect_var  = tk.BooleanVar(value=CONF.get("serial", {}).get("auto_connect", False))

        self.settings_panel      = None
        self.low_perf_var        = tk.BooleanVar(value=CONF["ui"].get("low_perf_mode", False))
        self.hide_preview_var    = tk.BooleanVar(value=CONF["ui"].get("hide_preview", False))
        self._experimental_win   = None   # CTkToplevel — попап EXPERIMENTAL, будується лениво
        self._module_chk_vars: dict[str, tk.BooleanVar] = {}
        self._module_chks: dict[str, ctk.CTkCheckBox] = {}
        self.low_perf_chk        = None
        self.hide_preview_chk    = None

        # Дебаунс автозбереження стану (mode + get_state() кожного модуля)
        self._last_state_save_job = None

        self.setup_ui()

        # Відновлюємо режим і стан кожного модуля з конфігу
        for key, module in self.modules.items():
            module.set_state(CONF["modules"].get(key, {}))
        start_mode = CONF.get("last_state", {}).get("mode", self.mode)
        if start_mode not in self.modules or not CONF["modules_enabled"].get(start_mode, True):
            start_mode = self.mode
        if start_mode is not None:
            self.set_mode(start_mode)

        # on_engine_ready() — для КОЖНОГО УВІМКНЕНОГО зареєстрованого модуля,
        # незалежно від того, чи він активний. Модулі, яким треба працювати
        # у фоні постійно (напр. слухати системні події — modules/notify.py),
        # стартують свій потік саме тут, а не в on_activate(). Вимкнені в
        # SETTINGS модулі пропускаються — їхній фоновий потік узагалі не
        # стартує (див. set_module_enabled() для вмикання під час роботи).
        ready_ctx = self._build_ctx()
        for key, module in self.modules.items():
            if not CONF["modules_enabled"].get(key, True):
                continue
            try:
                module.on_engine_ready(ready_ctx)
                self._engine_ready_called.add(key)
            except Exception as e:
                self.log_process(f"MODULE_ENGINE_READY_ERR ({key}): {str(e)[:60]}")

        self._worker = threading.Thread(target=self._video_processing, daemon=True)
        self._worker.start()

        self._serial_reader = threading.Thread(target=self._serial_read_worker, daemon=True)
        self._serial_reader.start()

        self._refresh_ui_elements()
        self._blink_loop()

        # Автопідключення при старті (якщо прапорець увімкнено й збережений
        # у конфігу). Виконуємо із затримкою, щоб вікно встигло промалюватись.
        if CONF.get("serial", {}).get("auto_connect", False):
            self.after(500, self.autodetect_serial)

        # --- Системний трей -------------------------------------------
        self._tray_icon = None
        if PYSTRAY_AVAILABLE:
            self.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)  # хрестик згортає, не закриває
            self.bind("<Unmap>", self._on_minimize_event)
        else:
            self.log_process("TRAY: pystray не встановлено (pip install pystray)")

    # =======================================================================
    # Контекст для модулів
    # =======================================================================
    def _build_ctx(self) -> ModuleContext:
        return ModuleContext(
            serial_mgr=self.serial_mgr,
            conf=CONF,
            log=self.log_process,
            theme=THEMES[self.current_theme],
            font_name=CONF["ui"]["font_name"],
            workspace_size=(self.workspace.winfo_width(), self.workspace.winfo_height()),
            request_redraw=self._on_module_frame,
            sensor_data=self.sensor_data,
            interrupt_display=self.interrupt_display,
        )

    def interrupt_display(self, grid: np.ndarray, duration: float):
        """Показує `grid` ПОВЕРХ активного режиму на `duration` секунд,
        незалежно від того, який режим зараз обрано в меню. Викликається
        будь-яким модулем (типово з фонового потоку, стартованого в
        on_engine_ready) — потокобезпечно. Після завершення `_video_processing`
        сам повертається до звичайного рендеру активного модуля."""
        with self._interrupt_lock:
            self._interrupt_grid  = grid
            self._interrupt_until = time.time() + max(0.0, duration)

    # =======================================================================
    # Побудова UI (каркас — без деталей режимів)
    # =======================================================================
    def setup_ui(self):
        f = CONF["ui"]["font_name"]
        t = THEMES[self.current_theme]

        # --- Зовнішня "рамка" застосунку --------------------------------

        # кутах немає: ілюзія скругленості без реальної прозорості.
        self.configure(fg_color=t["bg"])

        self.app_shell = ctk.CTkFrame(
            self, corner_radius=6, border_width=1, border_color=t["accent"],
            fg_color=t["bg"],
        )
        self.app_shell.pack(fill="both", expand=True)

        # --- Бокова панель ---------------------------------------------
        self.sidebar = ctk.CTkFrame(
            self.app_shell, width=240, corner_radius=0,
            fg_color=t["bg"], border_width=1, border_color=t["accent_dim"]
        )
        self.sidebar.pack(side="left", fill="y", padx=2, pady=2)

        self.logo_glow = GlowLabel(
            self.sidebar, text=CONF["ui"]["logo_text"],
            font=(f, 18, "bold")
        )
        self.logo_glow.pack(pady=40, padx=20)

        self.nav_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.nav_frame.pack(fill="x", padx=10)

        # Пункти меню будуються з реєстру модулів — жодного хардкоду режимів.
        for mode_key in self.module_order:
            label = self.modules[mode_key].label
            btn = ctk.CTkButton(
                self.nav_frame, text=label, anchor="w",
                corner_radius=0, border_width=0,
                fg_color="transparent", text_color=t["accent"],
                hover_color=t["accent_dim"], font=(f, 13, "bold"),
                command=lambda m=mode_key: self.set_mode(m)
            )
            self.nav_buttons[mode_key] = btn
            if CONF["modules_enabled"].get(mode_key, True):
                btn.pack(fill="x", pady=2)
            # інакше лишається непризначеним (не запакованим) — прихований,
            # доки користувач не увімкне модуль у SETTINGS

        # --- Нижня частина бокової панелі -------------------------------
        self.bottom_sidebar = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.bottom_sidebar.pack(side="bottom", fill="x", pady=20, padx=10)

        self.status_label = ctk.CTkLabel(
            self.bottom_sidebar, text="● OFFLINE", anchor="w",
            font=(f, 11, "bold"), text_color=t["alert"]
        )
        self.status_label.pack(fill="x", pady=(0, 6))

        self.sensor_lbl = tk.Label(
            self.bottom_sidebar, text="T:-- L:-- \nACC:--,--,--",
            font=(f, 8), fg=t["accent_dim"], bg=t["bg"], justify="left"
        )
        self.sensor_lbl.pack(anchor="w", pady=(0, 8))

        self.conn_settings_btn = ctk.CTkButton(
            self.bottom_sidebar, text="🔗  CONNECTION",
            corner_radius=0, border_width=1, border_color=t["accent_dim"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 11, "bold"), command=self._open_connection_settings
        )
        self.conn_settings_btn.pack(fill="x", pady=(0, 6))

        self.app_settings_btn = ctk.CTkButton(
            self.bottom_sidebar, text="⚙️  SETTINGS",
            corner_radius=0, border_width=1, border_color=t["accent_dim"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 11, "bold"), command=self._open_app_settings
        )
        self.app_settings_btn.pack(fill="x")

        # --- Робоча область ----------------------------------------------
        self.workspace = ctk.CTkFrame(self.app_shell, corner_radius=0, fg_color=t["bg"])
        self.workspace.pack(side="right", expand=True, fill="both", padx=10, pady=10)

        header = ctk.CTkFrame(self.workspace, corner_radius=0, fg_color=t["accent"], height=35)
        header.pack(fill="x", pady=(0, 10))

        self.header_label = ctk.CTkLabel(
            header, text=CONF["ui"]["header_text"],
            font=(f, 12, "bold"), text_color=t["text_dark"]
        )
        self.header_label.pack(side="left", padx=15)

        self.blink_dot = ctk.CTkLabel(header, text="●", font=(f, 14), text_color=t["alert"])
        self.blink_dot.pack(side="left")

        # Без стандартної рамки ОС вікно саме по собі не тягається — робимо
        # це вручну по цій смужці (клік і перетягування миші).
        for drag_widget in (header, self.header_label, self.blink_dot):
            drag_widget.bind("<ButtonPress-1>", self._start_move)
            drag_widget.bind("<B1-Motion>", self._do_move)

        self.close_btn = ctk.CTkButton(
            header, text="✕", width=32, height=28,
            corner_radius=0, border_width=0,
            fg_color="transparent", text_color=t["text_dark"], hover_color=t["alert"],
            font=(f, 13, "bold"), command=self._full_quit
        )
        self.close_btn.pack(side="right", padx=(0, 10))

        self.tray_btn = ctk.CTkButton(
            header, text="🗕", width=32, height=28,
            corner_radius=0, border_width=0,
            fg_color="transparent", text_color=t["text_dark"], hover_color=t["bg"],
            font=(f, 13, "bold"), command=self.minimize_to_tray
        )
        self.tray_btn.pack(side="right")

        # --- Прев'ю --------------------------------------------------------
        preview_box = ctk.CTkFrame(
            self.workspace, corner_radius=0,
            border_width=1, border_color=t["accent"], fg_color="#030100"
        )
        self.preview_box = preview_box
        self.preview_canvas = tk.Canvas(
            preview_box,
            width=PreviewRenderer.WIDTH, height=PreviewRenderer.HEIGHT,
            bg=t["preview_bg"], highlightthickness=0
        )
        self.preview_canvas.pack(pady=10)
        if not CONF["ui"].get("hide_preview", False):
            preview_box.pack(fill="x", pady=5)

        # --- Контейнер під панель активного модуля -------------------------
        # Сам порожній: модулі пакують сюди свій build_ui() у set_mode().
        self.controls_box = ctk.CTkFrame(
            self.workspace, corner_radius=0,
            border_width=1, border_color=t["accent_dim"], fg_color="transparent"
        )
        self.controls_box.pack(fill="both", expand=True, pady=5)

        # --- Лог -------------------------------------------------------
        self.feed_container = ctk.CTkFrame(
            self.workspace, corner_radius=0,
            border_width=1, border_color=t["accent_dim"],
            fg_color=t["panel_bg"], height=self.DEFAULT_FEED_HEIGHT
        )
        self.feed_container.pack(fill="x", side="bottom", pady=(10, 0))
        self.feed_container.pack_propagate(False)

        self.feed_title_lbl = tk.Label(
            self.feed_container, text="[ SYSTEM_DATA_FEED ]",
            font=(f, 8), fg=t["accent_dim"], bg=t["panel_bg"]
        )
        self.feed_title_lbl.pack(anchor="nw", padx=10, pady=2)

        self.log_canvas_frame = tk.Frame(self.feed_container, bg=t["panel_bg"])
        self.log_canvas_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.log_widgets: list[GlowLabel] = []

    # =======================================================================
    # Автопідключення (чекбокс у попапі з'єднання)
    # =======================================================================
    def _on_auto_connect_toggle(self):
        CONF["serial"]["auto_connect"] = self.auto_connect_var.get()
        save_config(CONF)
        self.log_process(f"AUTO_CONNECT: {'ON' if CONF['serial']['auto_connect'] else 'OFF'}")

    # =======================================================================
    # Теми
    # =======================================================================
    def apply_theme(self, theme_name: str):
        self.current_theme = theme_name
        t = THEMES[theme_name]

        # Якщо попап EXPERIMENTAL відкритий — синхронізуємо прапорець теми,
        # незалежно від того, звідки прийшла зміна (шапка чи сам попап).
        if self._experimental_win is not None and self._experimental_win.winfo_exists():
            self._experimental_light_var.set(theme_name == "light")

        self.configure(fg_color=t["bg"])
        self.app_shell.configure(fg_color=t["bg"], border_color=t["accent"])
        self.sidebar.configure(fg_color=t["bg"], border_color=t["accent_dim"])
        self.workspace.configure(fg_color=t["bg"])

        self.preview_canvas.configure(bg=t["preview_bg"])
        self.preview_canvas.master.configure(fg_color=t["preview_bg"], border_color=t["accent"])
        self.controls_box.configure(border_color=t["accent_dim"])

        self.feed_container.configure(fg_color=t["panel_bg"], border_color=t["accent_dim"])
        self.feed_title_lbl.configure(fg=t["accent_dim"], bg=t["panel_bg"])
        self.log_canvas_frame.configure(bg=t["panel_bg"])

        self._restyle_nav_style_btn(self.conn_settings_btn, t, active=(self._active_view == "connection"))
        self._restyle_nav_style_btn(self.app_settings_btn, t, active=(self._active_view == "settings"))
        if self.settings_panel is not None:
            self.settings_panel.configure(fg_color=t["bg"])
            for key, chk in self._module_chks.items():
                chk.configure(
                    text_color=t["accent_dim"], fg_color=t["accent"],
                    hover_color=t["accent"], border_color=t["accent_dim"],
                )
            if self.low_perf_chk is not None:
                self.low_perf_chk.configure(
                    text_color=t["accent_dim"], fg_color=t["accent"],
                    hover_color=t["accent"], border_color=t["accent_dim"],
                )
            if self.hide_preview_chk is not None:
                self.hide_preview_chk.configure(
                    text_color=t["accent_dim"], fg_color=t["accent"],
                    hover_color=t["accent"], border_color=t["accent_dim"],
                )
        if self.port_menu is not None:
            self.port_menu.configure(fg_color=t["bg"], button_color=t["accent_dim"], text_color=t["accent"])
        self._update_connect_btn(connected=self.serial_mgr.is_connected)
        if self.autodetect_btn is not None:
            self.autodetect_btn.configure(border_color=t["accent"], fg_color=t["accent"], text_color=t["text_dark"])
        if self.conn_panel is not None:
            self.conn_panel.configure(fg_color=t["bg"])
        if self.auto_connect_chk is not None:
            self.auto_connect_chk.configure(
                text_color=t["accent_dim"], fg_color=t["accent"],
                hover_color=t["accent"], border_color=t["accent_dim"]
            )
        self.tray_btn.configure(text_color=t["text_dark"], hover_color=t["bg"])
        self.close_btn.configure(text_color=t["text_dark"], hover_color=t["alert"])
        self.sensor_lbl.configure(fg=t["accent_dim"], bg=t["bg"])

        self._update_status_color(t)
        self.logo_glow.update_colors(bg=t["bg"], fg=t["accent"], glow=t["glow"])

        self._update_nav_buttons_theme(t)

        # Панель активного модуля перефарбовує себе сама — ядро не знає,
        # які саме віджети всередині.
        if self.active_module is not None:
            self.active_module.on_theme_change(self._build_ctx())

        self._recreate_logs()

    def _update_status_color(self, t: dict):
        if self.serial_mgr.is_connected:
            self.status_label.configure(text_color=t["accent"])
        else:
            self.status_label.configure(text_color=t["alert"])

    def _restyle_nav_style_btn(self, btn, t: dict, active: bool):
        """Підсвічує кнопку CONNECTION/SETTINGS у боковій панелі так само,
        як підсвічується активний пункт меню режимів — щоб було видно, яка
        саме панель зараз показана в controls_box."""
        if active:
            btn.configure(fg_color=t["accent"], text_color=t["text_dark"], border_color=t["accent"])
        else:
            btn.configure(fg_color="transparent", text_color=t["accent"], border_color=t["accent_dim"])

    def _update_nav_buttons_theme(self, t: dict):
        for key, btn in self.nav_buttons.items():
            label = self.modules[key].label
            if key == self.mode:
                btn.configure(fg_color=t["accent"], text_color=t["text_dark"], text=f">> {label}")
            else:
                btn.configure(fg_color="transparent", text_color=t["accent"], text=label)

    # =======================================================================
    # Перетягування вікна (замінює стандартну рамку ОС, якої тепер нема)
    # =======================================================================
    def _start_move(self, event):
        self._drag_offset_x = self.winfo_pointerx() - self.winfo_x()
        self._drag_offset_y = self.winfo_pointery() - self.winfo_y()

    def _do_move(self, event):
        x = self.winfo_pointerx() - self._drag_offset_x
        y = self.winfo_pointery() - self._drag_offset_y
        self.geometry(f"+{x}+{y}")

    # =======================================================================
    # Мигаючий індикатор
    # =======================================================================
    def _blink_loop(self):
        self.blink_state = not self.blink_state
        t     = THEMES[self.current_theme]
        color = t["alert"] if self.blink_state else t["accent"]
        self.blink_dot.configure(text_color=color)
        self.after(600, self._blink_loop)

    # =======================================================================
    # Перемикання режимів (диспетчер модулів)
    # =======================================================================
    def _clear_workspace_view(self):
        """Ховає геть усе, що могло бути запаковано в controls_box: кешовану
        панель БУДЬ-ЯКОГО модуля або одну з псевдо-панелей CONNECTION/
        SETTINGS. Викликається перед показом чогось нового в тому самому
        місці, щоб уникнути накладання віджетів один на одного."""
        for panel in list(self._module_panels.values()) + [self.conn_panel, self.settings_panel]:
            if panel is not None:
                panel.pack_forget()

    def set_mode(self, m: str, show: bool = True):
        """show=False перемикає активний модуль «під капотом» (рендер на
        пристрій, on_activate/on_deactivate, збереження стану), НЕ чіпаючи
        те, що зараз видно в controls_box — потрібно, коли користувач
        вимикає поточний активний модуль, перебуваючи в панелі SETTINGS:
        інакше застосунок непомітно викинув би його з налаштувань у панель
        нового режиму."""
        if m not in self.modules:
            self.log_process(f"MODE_UNKNOWN: {m}")
            return
        if not CONF["modules_enabled"].get(m, True):
            self.log_process(f"MODE_DISABLED: {m}")
            return

        ctx = self._build_ctx()

        # Деактивуємо попередній модуль (панель ховає _clear_workspace_view нижче)
        if self.active_module is not None:
            try:
                self.active_module.on_deactivate(ctx)
            except Exception as e:
                self.log_process(f"MODULE_DEACTIVATE_ERR ({self.active_module.key}): {str(e)[:60]}")

        t = THEMES[self.current_theme]
        self.mode = m
        self.active_module = self.modules[m]
        self._update_nav_buttons_theme(t)

        # Будуємо панель модуля лише один раз, далі перевикористовуємо
        panel = self._module_panels.get(m)
        if panel is None:
            panel = self.active_module.build_ui(self.controls_box, ctx)
            self._module_panels[m] = panel

        if show:
            # Реальний режим завжди витісняє псевдо-панелі CONNECTION/
            # SETTINGS (якщо котрась із них саме показана в controls_box).
            self._active_view = None
            self._clear_workspace_view()
            self._restyle_nav_style_btn(self.conn_settings_btn, t, active=False)
            self._restyle_nav_style_btn(self.app_settings_btn, t, active=False)
            panel.pack(fill="both", expand=True)

        try:
            self.active_module.on_activate(ctx)
        except Exception as e:
            self.log_process(f"MODULE_ACTIVATE_ERR ({m}): {str(e)[:60]}")

        # Модулі зі своєю індикацією статусу (напр. бібліотека файлів)
        # можуть заховати загальну лог-панель на час своєї активності,
        # або лишити її видимою, але компактнішою (log_feed_height).
        self.feed_container.pack_forget()
        if not self.active_module.hides_log_feed:
            height = self.active_module.log_feed_height or self.DEFAULT_FEED_HEIGHT
            self.feed_container.configure(height=height)
            self.feed_container.pack(fill="x", side="bottom", pady=(10, 0))

        self.log_process(f"MODE_SET: {self.active_module.label}")
        self.header_label.configure(text=f"PRODUCT: HEXEL_ONE | MODE: {self.active_module.label}")
        self._schedule_last_state_save()

    # =======================================================================
    # Автозбереження стану (mode + get_state() кожного модуля)
    # =======================================================================
    def _schedule_last_state_save(self, *_a):
        """Планує запис стану з дебаунсом, щоб не писати файл на кожну
        дрібну зміну (рух повзунка тощо)."""
        if self._last_state_save_job is not None:
            try:
                self.after_cancel(self._last_state_save_job)
            except Exception:
                pass
        self._last_state_save_job = self.after(600, self._flush_last_state_save)

    def _flush_last_state_save(self):
        self._last_state_save_job = None
        CONF["last_state"] = {"mode": self.mode}
        for key, module in self.modules.items():
            try:
                CONF["modules"][key] = module.get_state()
            except Exception as e:
                self.log_process(f"STATE_SAVE_ERR ({key}): {str(e)[:60]}")
        save_config(CONF)

    # =======================================================================
    # COM-порт
    # =======================================================================
    def refresh_ports(self):
        if self.port_menu is None:
            return   # панель CONNECTION ще не будувалась (жодного разу не відкривали)
        ports = [p.device for p in serial.tools.list_ports.comports()] or ["NO_LINK"]
        self.port_menu.configure(values=ports)
        last_port = CONF.get("serial", {}).get("last_port", "")
        if last_port in ports:
            self.port_var.set(last_port)
        else:
            self.port_var.set(ports[0])

    def _update_status_label(self, connected: bool, port: str = ""):
        t = THEMES[self.current_theme]
        if connected:
            self.status_label.configure(text=f"● ONLINE ({port})", text_color=t["accent"])
        else:
            self.status_label.configure(text="● OFFLINE", text_color=t["alert"])

    def connect_serial(self):
        if self.serial_mgr.is_connected:
            self.serial_mgr.disconnect()
            self.log_process("HW_UNLINKED")
            self._update_status_label(connected=False)
            self._update_connect_btn(connected=False)
            return

        selected = self.port_var.get()
        if selected == "NO_LINK":
            return
        msg = self.serial_mgr.connect(selected)
        self.log_process(msg)
        if "OK" in msg:
            self._update_status_label(connected=True, port=selected)
            self._update_connect_btn(connected=True)
            CONF["serial"]["last_port"] = selected
            save_config(CONF)
        else:
            self._update_status_label(connected=False)
            self._update_connect_btn(connected=False)

    def autodetect_serial(self):
        """Запускає пошук HEX_MC (VID-фільтр + handshake) у фоновому потоці."""
        if self.serial_mgr.is_connected:
            self.log_process("AUTO_FIND: вже підключено, спочатку відключіться")
            return
        if self.autodetect_btn is not None:
            self.autodetect_btn.configure(state="disabled", text="[ SEARCHING... ]")
        self.log_process("AUTO_FIND: пошук пристрою...")
        threading.Thread(target=self._autodetect_worker, daemon=True).start()

    def _autodetect_worker(self):
        port = self.serial_mgr.find_device_port()
        self.after(0, self._autodetect_done, port)

    def _autodetect_done(self, port: str | None):
        if self.autodetect_btn is not None:
            self.autodetect_btn.configure(state="normal", text="[ AUTO_FIND ]")

        if port is None:
            self.log_process("AUTO_FIND: пристрій не знайдено")
            return

        self.log_process(f"AUTO_FIND: знайдено на {port}")
        self.refresh_ports()
        if self.port_menu is not None and port in self.port_menu.cget("values"):
            self.port_var.set(port)

        msg = self.serial_mgr.connect(port)
        self.log_process(msg)
        if "OK" in msg:
            self._update_status_label(connected=True, port=port)
            self._update_connect_btn(connected=True)
            CONF["serial"]["last_port"] = port
            save_config(CONF)
        else:
            self._update_status_label(connected=False)
            self._update_connect_btn(connected=False)

    def _update_connect_btn(self, connected: bool):
        if self.connect_btn is None:
            return   # панель CONNECTION ще не будувалась (жодного разу не відкривали)
        t = THEMES[self.current_theme]
        if connected:
            self.connect_btn.configure(text="[ DISCONNECT ]", border_color=t["alert"], text_color=t["alert"])
        else:
            self.connect_btn.configure(text="[ CONNECT ]", border_color=t["accent_dim"], text_color=t["accent"])

    # =======================================================================
    # Повернення з псевдо-панелі (CONNECTION/SETTINGS) до панелі модуля
    # =======================================================================
    def _return_to_module_view(self):
        self._active_view = None
        self._clear_workspace_view()
        t = THEMES[self.current_theme]
        self._restyle_nav_style_btn(self.conn_settings_btn, t, active=False)
        self._restyle_nav_style_btn(self.app_settings_btn, t, active=False)
        panel = self._module_panels.get(self.mode)
        if panel is not None:
            panel.pack(fill="both", expand=True)

    def _make_back_button(self, parent, t: dict, f: str) -> ctk.CTkButton:
        """Спільна кнопка «назад» для псевдо-панелей — повертає у
        controls_box панель поточного активного модуля."""
        return ctk.CTkButton(
            parent, text="[ ← НАЗАД ДО МОДУЛЯ ]",
            corner_radius=0, border_width=1, border_color=t["accent_dim"],
            fg_color="transparent", text_color=t["accent_dim"],
            font=(f, 10, "bold"), command=self._return_to_module_view,
        )

    # =======================================================================
    # Панель налаштувань з'єднання (⚙️ CONNECTION) — показується у controls_box
    # =======================================================================
    def _open_connection_settings(self):
        self._clear_workspace_view()
        self._active_view = "connection"
        self._restyle_nav_style_btn(self.conn_settings_btn, THEMES[self.current_theme], active=True)
        self._restyle_nav_style_btn(self.app_settings_btn, THEMES[self.current_theme], active=False)

        if self.conn_panel is None:
            self._build_connection_panel()
        self.conn_panel.pack(fill="both", expand=True)
        self.refresh_ports()

    def _build_connection_panel(self):
        t = THEMES[self.current_theme]
        f = CONF["ui"]["font_name"]

        panel = ctk.CTkFrame(self.controls_box, fg_color=t["bg"])
        self.conn_panel = panel

        self._make_back_button(panel, t, f).pack(fill="x", padx=14, pady=(14, 10))

        tk.Label(
            panel, text="[ DEVICE_LINK ]", font=(f, 9),
            fg=t["accent_dim"], bg=t["bg"]
        ).pack(anchor="w", padx=14, pady=(0, 2))

        self.port_menu = ctk.CTkOptionMenu(
            panel, variable=self.port_var, values=["SCANNING..."],
            fg_color=t["bg"], button_color=t["accent_dim"],
            text_color=t["accent"], corner_radius=0, font=(f, 11)
        )
        self.port_menu.pack(padx=14, pady=5, fill="x")

        self.autodetect_btn = ctk.CTkButton(
            panel, text="[ AUTO_FIND ]",
            corner_radius=0, border_width=1, border_color=t["accent"],
            fg_color=t["accent"], text_color=t["text_dark"],
            font=(f, 11, "bold"), command=self.autodetect_serial
        )
        self.autodetect_btn.pack(fill="x", padx=14, pady=2)

        self.connect_btn = ctk.CTkButton(
            panel, text="[ CONNECT ]",
            corner_radius=0, border_width=1, border_color=t["accent_dim"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 11, "bold"), command=self.connect_serial
        )
        self.connect_btn.pack(fill="x", padx=14, pady=(2, 10))
        self._update_connect_btn(connected=self.serial_mgr.is_connected)

        self.auto_connect_chk = ctk.CTkCheckBox(
            panel, text="AUTO_CONNECT ON START",
            variable=self.auto_connect_var, onvalue=True, offvalue=False,
            font=(f, 9), text_color=t["accent_dim"],
            fg_color=t["accent"], hover_color=t["accent"],
            border_color=t["accent_dim"], checkbox_width=14, checkbox_height=14,
            command=self._on_auto_connect_toggle
        )
        self.auto_connect_chk.pack(anchor="w", padx=14, pady=(0, 10))

    # =======================================================================
    # Панель налаштувань застосунку (⚙️ SETTINGS) — показується у controls_box
    # =======================================================================
    def _open_app_settings(self):
        self._clear_workspace_view()
        self._active_view = "settings"
        self._restyle_nav_style_btn(self.app_settings_btn, THEMES[self.current_theme], active=True)
        self._restyle_nav_style_btn(self.conn_settings_btn, THEMES[self.current_theme], active=False)

        if self.settings_panel is None:
            self._build_settings_panel()
        self.settings_panel.pack(fill="both", expand=True)

    def _build_settings_panel(self):
        t = THEMES[self.current_theme]
        f = CONF["ui"]["font_name"]

        panel = ctk.CTkFrame(self.controls_box, fg_color=t["bg"])
        self.settings_panel = panel

        self._make_back_button(panel, t, f).pack(fill="x", padx=14, pady=(14, 10))

        tk.Label(
            panel, text="[ МОДУЛІ ]", font=(f, 9),
            fg=t["accent_dim"], bg=t["bg"]
        ).pack(anchor="w", padx=14, pady=(0, 2))

        modules_frame = ctk.CTkScrollableFrame(
            panel, fg_color=t["panel_bg"],
            border_width=1, border_color=t["accent_dim"], height=180,
        )
        modules_frame.pack(fill="x", padx=14, pady=(0, 10))

        self._module_chk_vars.clear()
        self._module_chks.clear()
        for key in self.module_order:
            label = self.modules[key].label
            var = tk.BooleanVar(value=CONF["modules_enabled"].get(key, True))
            self._module_chk_vars[key] = var
            chk = ctk.CTkCheckBox(
                modules_frame, text=label, variable=var,
                onvalue=True, offvalue=False,
                font=(f, 12, "bold"), text_color=t["accent_dim"],
                fg_color=t["accent"], hover_color=t["accent"],
                border_color=t["accent_dim"], checkbox_width=14, checkbox_height=14,
                command=lambda k=key: self.set_module_enabled(k, self._module_chk_vars[k].get())
            )
            chk.pack(anchor="w", padx=8, pady=4)
            self._module_chks[key] = chk

        tk.Label(
            panel,
            text="Вимкнений модуль зникає з меню зліва.", #  Якщо модуль має фонову службу (напр. NOTIFY), повне зупинення служби після вимкнення набуде сили після перезапуску застосунку.
            font=(f, 10), fg=t["accent_dim"], bg=t["bg"],
            wraplength=290, justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 14))

        tk.Label(
            panel, text="[ ІНТЕРФЕЙС ]", font=(f, 9),
            fg=t["accent_dim"], bg=t["bg"]
        ).pack(anchor="w", padx=14, pady=(0, 2))

        # Два прапорці інтерфейсу — в один рядок (grid, 2 рівні колонки),
        # замість повнорозмірних рядків один під одним.
        iface_row = ctk.CTkFrame(panel, fg_color="transparent")
        iface_row.pack(fill="x", padx=14, pady=(4, 4))
        iface_row.grid_columnconfigure(0, weight=1)
        iface_row.grid_columnconfigure(1, weight=1)

        self.low_perf_chk = ctk.CTkCheckBox(
            iface_row, text="LOW_PERF_MODE",
            variable=self.low_perf_var, onvalue=True, offvalue=False,
            font=(f, 12, "bold"), text_color=t["accent_dim"],
            fg_color=t["accent"], hover_color=t["accent"],
            border_color=t["accent_dim"], checkbox_width=14, checkbox_height=14,
            command=self._on_low_perf_toggle,
        )
        self.low_perf_chk.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.hide_preview_chk = ctk.CTkCheckBox(
            iface_row, text="HIDE_PREVIEW",
            variable=self.hide_preview_var, onvalue=True, offvalue=False,
            font=(f, 12, "bold"), text_color=t["accent_dim"],
            fg_color=t["accent"], hover_color=t["accent"],
            border_color=t["accent_dim"], checkbox_width=14, checkbox_height=14,
            command=self._on_hide_preview_toggle,
        )
        self.hide_preview_chk.grid(row=0, column=1, sticky="w")

        tk.Label(
            panel,
            text="LOW_PERF — знижує навантаження на CPU. HIDE_PREVIEW — ховає в'юпорт прев'ю.",
            font=(f, 10), fg=t["accent_dim"], bg=t["bg"],
            wraplength=290, justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 14))

        self.experimental_btn = ctk.CTkButton(
            panel, text="🧪  EXPERIMENTAL",
            corner_radius=0, border_width=1, border_color=t["accent_dim"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 12, "bold"), command=self._open_experimental_popup,
        )
        self.experimental_btn.pack(fill="x", padx=14, pady=(0, 14))

    # =======================================================================
    # EXPERIMENTAL — попап з прапорцями функцій, що ще "обкатуються"
    # =======================================================================
    def _open_experimental_popup(self):
        """Будує попап лениво, при першому відкритті. Повторний клік на
        кнопку, поки вікно вже відкрите, просто піднімає його наверх
        замість дублювання."""
        if self._experimental_win is not None and self._experimental_win.winfo_exists():
            self._experimental_win.lift()
            self._experimental_win.focus_force()
            return

        t = THEMES[self.current_theme]
        f = CONF["ui"]["font_name"]

        win = ctk.CTkToplevel(self)
        win.title("EXPERIMENTAL")
        win.geometry("340x220")
        win.resizable(False, False)
        win.configure(fg_color=t["bg"])
        win.attributes("-topmost", True)   # тримається поверх головного overrideredirect-вікна
        self._experimental_win = win

        tk.Label(
            win, text="⚠ ЕКСПЕРИМЕНТАЛЬНІ ФУНКЦІЇ", font=(f, 10, "bold"),
            fg=t["alert"], bg=t["bg"],
        ).pack(anchor="w", padx=14, pady=(14, 2))

        tk.Label(
            win,
            text="Можуть працювати нестабільно або змінитись у майбутніх версіях.",
            font=(f, 9), fg=t["accent_dim"], bg=t["bg"],
            wraplength=300, justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 14))

        # --- поки єдиний пункт: перемикач теми (тимчасово тут, доки не
        # з'являться інші експериментальні прапорці) -----------------------
        # Зберігаємо var як атрибут — apply_theme() синхронізує прапорець,
        # якщо тему перемкнули кнопкою в шапці, поки цей попап відкритий.
        self._experimental_light_var = tk.BooleanVar(value=(self.current_theme == "light"))

        def _on_theme_flag_toggle():
            self.apply_theme("light" if self._experimental_light_var.get() else "dark")
            self.log_process(f"THEME_SET: {self.current_theme.upper()}")

        ctk.CTkCheckBox(
            win, text="LIGHT_THEME (світла тема)",
            variable=self._experimental_light_var, onvalue=True, offvalue=False,
            font=(f, 12, "bold"), text_color=t["accent_dim"],
            fg_color=t["accent"], hover_color=t["accent"],
            border_color=t["accent_dim"], checkbox_width=14, checkbox_height=14,
            command=_on_theme_flag_toggle,
        ).pack(anchor="w", padx=14, pady=(0, 4))

    def set_module_enabled(self, key: str, enabled: bool):
        """Вмикає/вимикає пункт меню модуля. Вимкнення ховає кнопку в меню
        і, якщо модуль зараз активний, перемикає на перший увімкнений
        модуль (деактивуючи вимкнений). Увімкнення повертає кнопку в меню
        і, якщо для модуля ще не викликався on_engine_ready() (фонові
        модулі на кшталт NOTIFY), запускає його зараз."""
        if key not in self.modules:
            return

        CONF["modules_enabled"][key] = enabled
        save_config(CONF)
        self.log_process(f"MODULE_{'ENABLED' if enabled else 'DISABLED'}: {key}")

        btn = self.nav_buttons.get(key)
        if btn is not None:
            if enabled:
                btn.pack(fill="x", pady=2)
            else:
                btn.pack_forget()

        if enabled and key not in self._engine_ready_called:
            try:
                self.modules[key].on_engine_ready(self._build_ctx())
                self._engine_ready_called.add(key)
            except Exception as e:
                self.log_process(f"MODULE_ENGINE_READY_ERR ({key}): {str(e)[:60]}")

        if not enabled and self.mode == key:
            fallback = next(
                (k for k in self.module_order if k != key and CONF["modules_enabled"].get(k, True)),
                None,
            )
            if fallback is not None:
                # show=False: перемикаємо активний модуль «під капотом», не
                # вибиваючи користувача з панелі SETTINGS, у якій він щойно
                # клацнув цей чекбокс.
                self.set_mode(fallback, show=(self._active_view is None))

    def _on_low_perf_toggle(self):
        CONF["ui"]["low_perf_mode"] = self.low_perf_var.get()
        save_config(CONF)
        self.log_process(f"LOW_PERF_MODE: {'ON' if CONF['ui']['low_perf_mode'] else 'OFF'}")

        # Примусово перемальовуємо вже побудовані GlowLabel-и (логотип, лог)
        # і даємо всім модулям шанс перефарбувати власні GlowLabel-віджети —
        # on_theme_change() уже вміє це робити і безпечно ігнорує ще не
        # побудовані панелі (усі посилання на віджети там None-guarded).
        self.logo_glow.draw_text()
        self._recreate_logs()
        ctx = self._build_ctx()
        for module in self.modules.values():
            try:
                module.on_theme_change(ctx)
            except Exception as e:
                self.log_process(f"MODULE_THEME_ERR ({module.key}): {str(e)[:60]}")

    def _on_hide_preview_toggle(self):
        hide = self.hide_preview_var.get()
        CONF["ui"]["hide_preview"] = hide
        save_config(CONF)
        self.log_process(f"HIDE_PREVIEW: {'ON' if hide else 'OFF'}")
        if hide:
            self.preview_box.pack_forget()
        else:
            # before=controls_box гарантує правильний порядок (прев'ю над
            # панеллю модуля), незалежно від того, коли віджет пакується.
            self.preview_box.pack(fill="x", pady=5, before=self.controls_box)

    # =======================================================================
    # Лог
    # =======================================================================
    def log_process(self, msg: str):
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        if self.log_queue.full():
            try:
                self.log_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self.log_queue.put_nowait(line)
        except queue.Full:
            pass

    def _recreate_logs(self):
        t = THEMES[self.current_theme]
        for w in self.log_widgets:
            w.destroy()
        self.log_widgets.clear()
        for line in reversed(self.last_logs):
            lbl = GlowLabel(
                self.log_canvas_frame,
                text=f"> {line}",
                font=(CONF["ui"]["font_name"], 9),
                foreground=t["accent"], glow_color=t["glow"], bg=t["panel_bg"]
            )
            lbl.pack(anchor="nw")
            self.log_widgets.append(lbl)

    # =======================================================================
    # Системний трей
    # =======================================================================
    def _on_minimize_event(self, event):
        if event.widget is self and self.state() == "iconic":
            self.after(10, self.minimize_to_tray)

    def minimize_to_tray(self):
        """Ховає головне вікно і піднімає іконку в треї (замінює звичайне закриття/згортання)."""
        if not PYSTRAY_AVAILABLE:
            self.iconify()
            return

        self.withdraw()
        self._paused = True
        if self._tray_icon is None:
            menu = pystray.Menu(
                pystray.MenuItem("Показати HEX.MC", self._tray_restore, default=True),
                pystray.MenuItem("Вихід",            self._tray_quit),
            )
            self._tray_icon = pystray.Icon("HEX_MC", build_tray_icon_image(), "HEX.MC", menu)
            threading.Thread(target=self._tray_icon.run, daemon=True).start()
        self.log_process("TRAY: згорнуто в трей, прев'ю призупинено (матриця далі активна)")

    def _tray_restore(self, icon=None, item=None):
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self.after(0, self._show_window)

    def _show_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()
        self._paused = False
        self.log_process("TRAY: вікно відновлено, прев'ю поновлено")

    def _tray_quit(self, icon=None, item=None):
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self.after(0, self._full_quit)

    def _full_quit(self):
        """Коректне завершення програми з трею: зупиняє фонові потоки та закриває порт."""
        self.running = False
        if self.active_module is not None:
            try:
                self.active_module.on_deactivate(self._build_ctx())
            except Exception:
                pass
        try:
            self.serial_mgr.disconnect()
        except Exception:
            pass
        self.destroy()
        os._exit(0)

    # =======================================================================
    # Зворотні дані з пристрою (телеметрія датчиків)
    # =======================================================================
    def _serial_read_worker(self):
        """Окремий потік: читає рядки телеметрії від пристрою у форматі
        'SENSOR:temp=23.4,light=512,ax=0.01,ay=-0.02,az=0.98' та зберігає
        останні значення у self.sensor_data (доступно модулям через
        активну розробку — див. HEX_MC_Script_Dev_Guide.md). Також слухає
        'BTN:<event>' — натискання фізичної кнопки (TTP223) на пристрої."""
        while self.running:
            if self.serial_mgr.is_connected:
                try:
                    line = self.serial_mgr.readline_nowait()
                except Exception:
                    line = None
                if line and line.startswith("SENSOR:"):
                    parsed = self._parse_sensor_line(line[len("SENSOR:"):])
                    if parsed:
                        self.sensor_data.update(parsed)
                        if self.sensor_queue.full():
                            try:
                                self.sensor_queue.get_nowait()
                            except queue.Empty:
                                pass
                        try:
                            self.sensor_queue.put_nowait(dict(self.sensor_data))
                        except queue.Full:
                            pass
                elif line and line.startswith("BTN:"):
                    event = line[len("BTN:"):].strip()
                    self.sensor_data["btn_last_event"] = event
                    self.sensor_data["btn_last_time"]   = time.time()
                    self.sensor_data["btn_tap_count"]   = self.sensor_data.get("btn_tap_count", 0) + 1
            time.sleep(0.05)

    @staticmethod
    def _parse_sensor_line(payload: str) -> dict | None:
        """Парсить 'key=val,key=val,...' у словник float-значень.
        Некоректні токени просто пропускаються — один зіпсований рядок
        не повинен ламати весь потік телеметрії."""
        result = {}
        for token in payload.split(","):
            if "=" not in token:
                continue
            key, _, val = token.partition("=")
            key = key.strip()
            try:
                result[key] = float(val.strip())
            except ValueError:
                continue
        return result or None

    def _update_sensor_label(self, data: dict):
        temp  = data.get("temp")
        light = data.get("light")
        ax, ay, az = data.get("ax"), data.get("ay"), data.get("az")
        temp_s  = f"{temp:.1f}" if temp is not None else "--"
        light_s = f"{light:.0f}" if light is not None else "--"
        acc_s   = (
            f"{ax:.2f},{ay:.2f},{az:.2f}"
            if None not in (ax, ay, az) else "--,--,--"
        )
        self.sensor_lbl.configure(text=f"T:{temp_s} L:{light_s} \nACC:{acc_s}")

    # =======================================================================
    # Відеопотік (окремий daemon-потік) — тепер лише диспетчер
    # =======================================================================
    def _video_processing(self):
        """PULL-модулі (owns_thread=False) тікаються звідси: щокадру
        викликається module.render(). PUSH-модулі (owns_thread=True) цей
        цикл ігнорує — вони самі штовхають кадри через ctx.request_redraw
        зі свого потоку (стартованого в on_activate).

        Рендер і відправка на пристрій НЕ зупиняються, коли вікно
        згорнуте в трей — матриця має продовжувати працювати, інакше
        згортання в трей втрачає сенс. Ставиться на паузу лише
        генерація прев'ю-картинки (_on_module_frame → _enqueue_preview)
        — її все одно нікому показувати, поки вікно приховане.

        Перед рендером активного модуля щотік перевіряється переривання
        (interrupt_display, §NOTIFY тощо) — поки воно активне, замість
        активного режиму на пристрій і в прев'ю йде його кадр."""
        while self.running:
            frame_start = time.time()

            with self._interrupt_lock:
                interrupt_active = self._interrupt_grid is not None and time.time() < self._interrupt_until
                interrupt_grid   = self._interrupt_grid if interrupt_active else None

            if interrupt_grid is not None:
                self._on_module_frame(interrupt_grid)
            else:
                module = self.active_module
                if module is not None and not module.owns_thread:
                    grid = np.zeros((5, 16, 3), dtype=np.uint8)
                    try:
                        grid = module.render(grid, self._build_ctx())
                    except Exception as e:
                        self.log_process(f"PROC_ERROR ({module.key}): {str(e)[:60]}")
                    self._on_module_frame(grid)

            elapsed = time.time() - frame_start
            sleep_t = max(0.005, 0.033 - elapsed)
            time.sleep(sleep_t)

    def _on_module_frame(self, grid: np.ndarray):
        """Єдина точка входу кадру в рушій — з головного циклу (pull) або
        напряму з потоку push-модуля (ctx.request_redraw). Відправка на
        пристрій відбувається ЗАВЖДИ — матриця має жити своїм життям
        незалежно від того, згорнуте вікно чи ні. Прев'ю (важкий
        cv2-пост-процес для картинки, яку в цей момент нікому показувати)
        пропускається, поки self._paused (вікно згорнуте в трей)."""
        self._send_data(grid)
        if not self._paused:
            self._enqueue_preview(grid)

    # =======================================================================
    # Надсилання даних на пристрій
    # =======================================================================
    def _send_data(self, grid: np.ndarray):
        if not self.serial_mgr.is_connected:
            return

        from core.config import ROW_CONFIG

        data = bytearray(b'S')
        for r in range(5):
            row_len = ROW_CONFIG[r]
            # Парні рядки (15 px): зсув +1; непарні (16 px): зсув 0
            # Та сама логіка що й у PreviewRenderer (color_idx)
            start_x    = 1 if (r % 2 == 0) else 0
            row_pixels = [grid[r, start_x + x] for x in range(row_len)]
            if r % 2 != 0:
                row_pixels.reverse()
            for p in row_pixels:
                data.extend([int(p[2]), int(p[1]), int(p[0])])

        self.serial_mgr.send(data)

    # =======================================================================
    # Генерація прев'ю та оновлення UI
    # =======================================================================
    def _enqueue_preview(self, grid: np.ndarray):
        is_light = (self.current_theme == "light")
        low_perf = CONF["ui"].get("low_perf_mode", False)
        img      = self.preview_renderer.render(grid, is_light, low_perf=low_perf)
        if self.image_queue.full():
            try:
                self.image_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self.image_queue.put_nowait(img)
        except queue.Full:
            pass

    def _refresh_ui_elements(self):
        # Оновлення прев'ю
        try:
            while not self.image_queue.empty():
                img        = self.image_queue.get_nowait()
                self.photo = ImageTk.PhotoImage(img)
                self.preview_canvas.delete("all")
                self.preview_canvas.create_image(0, 0, anchor="nw", image=self.photo)
        except queue.Empty:
            pass
        except Exception as e:
            print(f"[WARN] preview update: {e}")

        # Безпечне читання черги логів через list() snapshot
        try:
            snapshot = []
            while True:
                snapshot.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        for item in snapshot:
            try:
                self.log_queue.put_nowait(item)
            except queue.Full:
                break

        if snapshot != self.last_logs and snapshot:
            self.last_logs = snapshot.copy()
            self._recreate_logs()

        # Оновлення мітки телеметрії датчиків (T/L/ACC)
        try:
            latest_sensor = None
            while True:
                latest_sensor = self.sensor_queue.get_nowait()
        except queue.Empty:
            pass
        if latest_sensor:
            self._update_sensor_label(latest_sensor)

        if self.running:
            self.after(50, self._refresh_ui_elements)