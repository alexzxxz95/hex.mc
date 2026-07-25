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
modules/scripts.py
===================
Найскладніший вбудований модуль — динамічно підвантажує сторонні .py
файли (написані за HEX_MC_Script_Dev_Guide.md) через importlib,
дає їм власну область у панелі (script.setup_ui(parent)) і кличе
script.update() щокадру для генерації grid.

СУМІСНІСТЬ ЗІ СТАРИМИ СКРИПТАМИ: у монолітній версії скрипт отримував
`module.app = self` — увесь MatrixApp. Тепер `module.app = ctx`, тобто
ModuleContext. Це означає одну зміну для авторів скриптів:
    app.log_process(msg)  →  app.log(msg)
    app.sensor_data       →  без змін (є і в ctx)
    app.serial_mgr        →  без змін (є і в ctx)
Дублюючих ключів немає навмисно — контракт має бути один. Дозагальнити
HEX_MC_Script_Dev_Guide.md під нову назву методу — окрема задача.
"""
import importlib.util
import os
import threading

import numpy as np
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog

from core.plugin_base import HexModuleBase, ModuleContext, register_module
from core.widgets import FileLibraryPanel


@register_module
class ScriptsModule(HexModuleBase):
    key   = "script"
    label = "SCRIPTS"
    owns_thread = False
    log_feed_height = 48   # компактно: заголовок + ~1 рядок останнього логу

    def __init__(self):
        self._pending_state = {"folder": "", "active_path": None}

        self._lock          = threading.Lock()
        self._active_script = None   # завантажений модуль (importlib)

        self.library:            FileLibraryPanel | None = None
        self.load_once_btn:      ctk.CTkButton | None = None
        self.warning_lbl:        tk.Label | None = None
        self.script_dynamic_area: ctk.CTkFrame | None = None

    # --- завантаження скрипта --------------------------------------------

    def _load_script(self, path: str, ctx: ModuleContext) -> bool:
        """Спільна логіка завантаження скрипта. Помилка в одному
        скрипті не впливає на решту бібліотеки чи роботу програми."""
        with self._lock:
            self._active_script = None
        if self.script_dynamic_area is not None:
            for w in self.script_dynamic_area.winfo_children():
                w.destroy()

        try:
            spec   = importlib.util.spec_from_file_location("dynamic_script", path)
            module = importlib.util.module_from_spec(spec)
            # Даємо скрипту доступ до ModuleContext під іменем `app` —
            # автори скриптів використовують app.sensor_data (телеметрія
            # з пристрою), app.log(...), app.serial_mgr тощо.
            module.app = ctx
            spec.loader.exec_module(module)
            if hasattr(module, "setup_ui") and self.script_dynamic_area is not None:
                module.setup_ui(self.script_dynamic_area)
            with self._lock:
                self._active_script = module
            ctx.log(f"SCRIPT_LOADED: {os.path.basename(path)}")
            return True
        except Exception as e:
            ctx.log(f"SCRIPT_ERROR ({os.path.basename(path)}): {str(e)[:60]}")
            return False

    def _activate_from_library(self, path: str, ctx: ModuleContext):
        ok = self._load_script(path, ctx)
        self.library.active_path = path if ok else None

    def _load_once(self, ctx: ModuleContext):
        path = filedialog.askopenfilename(filetypes=[("Python Files", "*.py")])
        if not path:
            return
        if self._load_script(path, ctx):
            if self.library is not None:
                self.library.active_path = None   # разове завантаження — поза бібліотекою
                self.library.highlight_active(ctx)

    # --- контракт HexModuleBase -----------------------------------------

    def build_ui(self, parent, ctx: ModuleContext) -> ctk.CTkFrame:
        t, f = ctx.theme, ctx.font_name
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        self.warning_lbl = tk.Label(
            frame,
            text="⚠ ЗАВАНТАЖУЙТЕ ЛИШЕ СКРИПТИ З ДОВІРЕНИХ ДЖЕРЕЛ",
            font=(f, 8), fg=t["alert"], bg=t["bg"],
            wraplength=200, justify="left",
        )
        self.warning_lbl.pack(anchor="nw", padx=10, pady=(10, 6))

        self.library = FileLibraryPanel(
            frame, ctx,
            extension=".py",
            on_select=self._activate_from_library,
            choose_title="Оберіть папку зі скриптами",
            empty_no_folder_text="Оберіть папку, щоб побачити скрипти",
            empty_no_files_text="У папці немає .py файлів",
            collapsible=True,   # список ховається за компактним рядком —
                                # UI скрипта під ним не перекривається
        )
        self.library.frame.pack(fill="x")

        # Кнопка разового завантаження — прямо в header-рядку бібліотеки
        # (поруч зі стрілкою розгортання й назвою активного файлу), а не
        # окремим повнорозмірним рядком під списком.
        self.load_once_btn = ctk.CTkButton(
            self.library.header, text="[ РАЗОВО ]",
            corner_radius=0, border_width=1, border_color=t["accent"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 10, "bold"), width=90,
            command=lambda: self._load_once(ctx)
        )
        self.load_once_btn.pack(side="right", padx=(6, 10), pady=6)

        self.library.set_folder(self._pending_state["folder"], ctx)
        self.library.active_path = self._pending_state["active_path"]
        if self.library.active_path:
            self._load_script(self.library.active_path, ctx)
        self.library.highlight_active(ctx)

        # Область, у яку завантажений скрипт малює власний UI через
        # свою функцію setup_ui(parent) — модуль її не наповнює сам.
        self.script_dynamic_area = ctk.CTkFrame(frame, fg_color="transparent")
        self.script_dynamic_area.pack(fill="both", expand=True)

        return frame

    def on_activate(self, ctx: ModuleContext) -> None:
        pass   # скрипт лишається завантаженим між активаціями, доки не покинули режим

    def on_deactivate(self, ctx: ModuleContext) -> None:
        """При виході з режиму скрипт вивантажується — так само, як у
        монолітній версії. Це свідомий запобіжник: чужий код не працює
        у фоні, поки користувач не в цьому режимі."""
        with self._lock:
            self._active_script = None
        if self.script_dynamic_area is not None:
            for w in self.script_dynamic_area.winfo_children():
                w.destroy()
        if self.library is not None:
            self.library.active_path = None
            self.library.highlight_active(ctx)

    def on_theme_change(self, ctx: ModuleContext) -> None:
        t = ctx.theme
        if self.warning_lbl is not None:
            self.warning_lbl.configure(fg=t["alert"], bg=t["bg"])
        if self.load_once_btn is not None:
            self.load_once_btn.configure(border_color=t["accent"], text_color=t["accent"])
        if self.library is not None:
            self.library.on_theme_change(ctx)

    def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
        with self._lock:
            script = self._active_script
        if script and hasattr(script, "update"):
            try:
                res = script.update()
                if res is not None:
                    grid = res
            except Exception as e:
                ctx.log(f"SCRIPT_RUNTIME_ERR: {str(e)[:60]}")
                with self._lock:
                    self._active_script = None
        return grid

    def get_state(self) -> dict:
        if self.library is None:
            return dict(self._pending_state)
        return {
            "folder":      self.library.folder,
            "active_path": self.library.active_path,
        }

    def set_state(self, state: dict) -> None:
        merged = {"folder": "", "active_path": None, **(state or {})}
        self._pending_state = merged
        if self.library is not None:
            self.library.folder      = merged["folder"]
            self.library.active_path = merged["active_path"]