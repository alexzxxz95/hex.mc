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
modules/gif_player.py
======================
Третій вбудований модуль — перший, що використовує FileLibraryPanel
(core/widgets.py) для бібліотеки файлів (папка + прокручуваний список
.gif). Показує, як компонувати FileLibraryPanel + слайдери в одному
build_ui().

hides_log_feed=True — бібліотека сама показує статус завантаження,
загальний системний лог рушія на час цього режиму ховається (як і в
монолітній версії).
"""
import os
import threading
import time

import cv2
import numpy as np
import customtkinter as ctk
from PIL import Image, ImageSequence
from tkinter import filedialog

from core.plugin_base import HexModuleBase, ModuleContext, register_module
from core.widgets import make_slider, SliderWidget, FileLibraryPanel


@register_module
class GifPlayerModule(HexModuleBase):
    key   = "gif"
    label = "GIF"
    owns_thread = False
    hides_log_feed = True

    DEFAULTS = {"folder": "", "brightness": 1.0, "speed": 10.0, "active_path": None}

    def __init__(self):
        self._pending_state = dict(self.DEFAULTS)

        self._lock          = threading.Lock()
        self._frames: list[np.ndarray] = []
        self._current_frame = 0
        self._last_update   = 0.0

        self.library:       FileLibraryPanel | None = None
        self.load_once_btn: ctk.CTkButton | None = None
        self.brightness:    SliderWidget | None = None
        self.speed:         SliderWidget | None = None

    # --- завантаження GIF ---------------------------------------------------

    def _load_gif(self, path: str, ctx: ModuleContext) -> bool:
        """Спільна логіка завантаження GIF. Помилка з одним файлом не
        впливає на решту бібліотеки чи роботу програми."""
        try:
            img    = Image.open(path)
            frames = []
            for frame in ImageSequence.Iterator(img):
                fr = frame.convert("RGB").resize((16, 5), Image.Resampling.LANCZOS)
                frames.append(cv2.cvtColor(np.array(fr), cv2.COLOR_RGB2BGR))

            with self._lock:
                self._frames        = frames
                self._current_frame = 0

            ctx.log(f"GIF_LOADED: {os.path.basename(path)} ({len(frames)} frames)")
            return True
        except Exception as e:
            ctx.log(f"GIF_ERROR ({os.path.basename(path)}): {str(e)[:60]}")
            return False

    def _activate_from_library(self, path: str, ctx: ModuleContext):
        ok = self._load_gif(path, ctx)
        self.library.active_path = path if ok else None

    def _load_once(self, ctx: ModuleContext):
        path = filedialog.askopenfilename(filetypes=[("GIF Files", "*.gif")])
        if not path:
            return
        if self._load_gif(path, ctx):
            if self.library is not None:
                self.library.active_path = None   # разове завантаження — поза бібліотекою
                self.library.highlight_active(ctx)

    # --- контракт HexModuleBase -----------------------------------------

    def build_ui(self, parent, ctx: ModuleContext) -> ctk.CTkFrame:
        t, f = ctx.theme, ctx.font_name
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        self.library = FileLibraryPanel(
            frame, ctx,
            extension=".gif",
            on_select=self._activate_from_library,
            choose_title="Оберіть папку з GIF-файлами",
            empty_no_folder_text="Оберіть папку, щоб побачити GIF-файли",
            empty_no_files_text="У папці немає .gif файлів",
        )
        self.library.frame.pack(fill="x")
        self.library.set_folder(self._pending_state["folder"], ctx)
        self.library.active_path = self._pending_state["active_path"]
        if self.library.active_path:
            self._load_gif(self.library.active_path, ctx)
        self.library.highlight_active(ctx)

        self.load_once_btn = ctk.CTkButton(
            frame, text="[ LOAD_GIF_FILE (разово) ]",
            corner_radius=0, border_width=1, border_color=t["accent"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 11, "bold"), command=lambda: self._load_once(ctx)
        )
        self.load_once_btn.pack(fill="x", padx=10, pady=(0, 10))

        self.brightness = make_slider(
            frame, t, f, "GIF_GAIN", 0.1, 3.0, self._pending_state["brightness"], decimals=2
        )
        self.speed = make_slider(
            frame, t, f, "PLAY_SPEED", 1, 50, self._pending_state["speed"]
        )

        return frame

    def on_theme_change(self, ctx: ModuleContext) -> None:
        if self.library is not None:
            self.library.on_theme_change(ctx)
        if self.load_once_btn is not None:
            self.load_once_btn.configure(border_color=ctx.theme["accent"], text_color=ctx.theme["accent"])
        for s in (self.brightness, self.speed):
            if s is not None:
                s.restyle(ctx.theme)

    def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
        with self._lock:
            frames    = self._frames
            cur_frame = self._current_frame

        if not frames:
            return grid

        speed_val      = self.speed.var.get() if self.speed else self._pending_state["speed"]
        speed_interval = 1.0 / max(1.0, speed_val * 1.5)
        now            = time.time()
        if now - self._last_update > speed_interval:
            cur_frame          = (cur_frame + 1) % len(frames)
            self._last_update  = now
            with self._lock:
                self._current_frame = cur_frame

        gain = self.brightness.var.get() if self.brightness else self._pending_state["brightness"]
        return cv2.convertScaleAbs(frames[cur_frame], alpha=gain, beta=0)

    def get_state(self) -> dict:
        if self.library is None:
            return dict(self._pending_state)
        return {
            "folder":      self.library.folder,
            "brightness":  self.brightness.var.get(),
            "speed":       self.speed.var.get(),
            "active_path": self.library.active_path,
        }

    def set_state(self, state: dict) -> None:
        merged = {**self.DEFAULTS, **(state or {})}
        self._pending_state = merged
        # Панель ще не побудована при першому виклику (рушій кличе
        # set_state() до set_mode()/build_ui()) — тоді просто кешуємо
        # значення у _pending_state, build_ui() підхопить їх сам.
        if self.library is not None:
            self.library.folder = merged["folder"]
            self.library.active_path = merged["active_path"]
            self.brightness.var.set(merged["brightness"])
            self.speed.var.set(merged["speed"])
