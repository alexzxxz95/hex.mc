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
modules/screen.py
==================
Найскладніший вбудований модуль — захоплення довільної ділянки екрана
через mss, три кольорові корекції (GAIN/CONTRAST/SATURATION),
блокування співвідношення сторін (LOCK_ASPECT) і 6 слотів пресетів.

mss-сесія (self._sct) відкривається в on_activate() і закривається в
on_deactivate() — модуль тікається лише поки активний (owns_thread=False,
пул-рендер з боку рушія), тож тримати захоплення відкритим постійно
немає сенсу.

Пресети раніше жили в CONF["profiles"] на верхньому рівні конфігу —
тепер повністю переїхали в CONF["modules"]["screen"]["presets"] (власний
неймспейс модуля), відповідно до узгодженої архітектури.
"""
import cv2
import numpy as np
import customtkinter as ctk
import tkinter as tk
from mss import mss

from core.plugin_base import HexModuleBase, ModuleContext, register_module
from core.widgets import make_slider, SliderWidget


@register_module
class ScreenModule(HexModuleBase):
    key   = "screen"
    label = "SCREEN"
    owns_thread = False

    DEFAULTS = {
        "cap_x": None, "cap_y": None,   # None → підставляються по центру монітора при першій побудові
        "cap_w": 160, "cap_h": 50,
        "gain": 1.0, "contrast": 1.0, "saturation": 1.0,
        "aspect_lock": False,
        "presets": [None, None, None, None, None, None],
    }

    def __init__(self):
        self._pending_state = dict(self.DEFAULTS)
        self._presets: list[dict | None] = list(self.DEFAULTS["presets"])

        # Межі монітора — рахуємо один раз, не залежить від активності режиму.
        try:
            with mss() as sct:
                m = sct.monitors[0]   # [0] = віртуальний прямокутник ВСІХ моніторів
                self.screen_left = m["left"]
                self.screen_top  = m["top"]
                self.max_w       = m["width"]
                self.max_h       = m["height"]
        except Exception:
            self.screen_left, self.screen_top = 0, 0
            self.max_w, self.max_h = 1920, 1080

        if self._pending_state["cap_x"] is None:
            self._pending_state["cap_x"] = self.screen_left + self.max_w // 2
        if self._pending_state["cap_y"] is None:
            self._pending_state["cap_y"] = self.screen_top + self.max_h // 2

        self._sct = None   # mss-сесія — жива лише поки режим активний

        self._aspect_ratio    = self._pending_state["cap_w"] / max(1, self._pending_state["cap_h"])
        self._aspect_updating = False
        self._profile_save_armed = False

        # Віджети (None, поки build_ui() не викликано)
        self.cap_x: SliderWidget | None = None
        self.cap_y: SliderWidget | None = None
        self.cap_w: SliderWidget | None = None
        self.cap_h: SliderWidget | None = None
        self.gain: SliderWidget | None = None
        self.contrast: SliderWidget | None = None
        self.saturation: SliderWidget | None = None
        self.aspect_lock_var: tk.BooleanVar | None = None
        self.aspect_lock_chk = None
        self.section_labels: list[tk.Label] = []
        self.profile_buttons: dict[int, ctk.CTkButton] = {}
        self.profile_save_btn = None

    # --- контракт HexModuleBase -----------------------------------------

    def build_ui(self, parent, ctx: ModuleContext) -> ctk.CTkFrame:
        t, f = ctx.theme, ctx.font_name
        ps = self._pending_state
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        self.section_labels = []

        def _section(grid_parent, text, row):
            lbl = tk.Label(
                grid_parent, text=text, font=(f, 9, "bold"),
                fg=t["accent"], bg=t["bg"], anchor="w"
            )
            lbl.grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 0))
            self.section_labels.append(lbl)

        screen_grid = ctk.CTkFrame(frame, fg_color="transparent")
        screen_grid.pack(fill="x", padx=12, pady=(10, 0))
        screen_grid.grid_columnconfigure(0, weight=1)
        screen_grid.grid_columnconfigure(1, weight=1)

        _section(screen_grid, "POSITION", 0)
        self.cap_x = make_slider(
            screen_grid, t, f, "POS_X", self.screen_left, self.screen_left + self.max_w,
            ps["cap_x"], grid_pos=(1, 0), compact=True
        )
        self.cap_y = make_slider(
            screen_grid, t, f, "POS_Y", self.screen_top, self.screen_top + self.max_h,
            ps["cap_y"], grid_pos=(1, 1), compact=True
        )

        _section(screen_grid, "SIZE", 2)
        self.cap_w = make_slider(screen_grid, t, f, "WIDTH",  16, 800, ps["cap_w"], grid_pos=(3, 0), compact=True)
        self.cap_h = make_slider(screen_grid, t, f, "HEIGHT", 5,  600, ps["cap_h"], grid_pos=(3, 1), compact=True)
        self.cap_w.var.trace_add("write", lambda *_a: self._on_cap_dim_change("w"))
        self.cap_h.var.trace_add("write", lambda *_a: self._on_cap_dim_change("h"))

        _section(screen_grid, "COLOR", 4)
        self.gain = make_slider(
            screen_grid, t, f, "GAIN", 0.1, 4.0, ps["gain"], decimals=2, grid_pos=(5, 0), compact=True
        )
        self.contrast = make_slider(
            screen_grid, t, f, "CONTRAST", 0.1, 3.0, ps["contrast"], decimals=2, grid_pos=(5, 1), compact=True
        )
        self.saturation = make_slider(
            screen_grid, t, f, "SATURATION", 0.0, 3.0, ps["saturation"], decimals=2, grid_pos=(6, 0), compact=True
        )

        self.aspect_lock_var = tk.BooleanVar(value=ps["aspect_lock"])
        self._aspect_ratio = self.cap_w.var.get() / max(1.0, self.cap_h.var.get())
        self.aspect_lock_chk = ctk.CTkCheckBox(
            frame, text="LOCK_ASPECT (WIDTH/HEIGHT)",
            variable=self.aspect_lock_var, onvalue=True, offvalue=False,
            font=(f, 10), text_color=t["accent"],
            fg_color=t["accent"], hover_color=t["accent"],
            border_color=t["accent_dim"], checkbox_width=16, checkbox_height=16,
            command=lambda: self._on_aspect_lock_toggle(ctx)
        )
        self.aspect_lock_chk.pack(anchor="w", padx=20, pady=(6, 10))

        # --- Пресети (6 слотів) --------------------------------------------
        tk.Label(
            frame, text="[ SCREEN PRESETS ]",
            font=(f, 9), fg=t["accent_dim"], bg=t["bg"]
        ).pack(anchor="w", padx=20, pady=(4, 2))

        profiles_row = ctk.CTkFrame(frame, fg_color="transparent")
        profiles_row.pack(anchor="w", padx=20)
        self.profile_buttons = {}
        for i in range(6):
            pbtn = ctk.CTkButton(
                profiles_row, text=str(i + 1), width=28, height=24,
                corner_radius=0, border_width=1, border_color=t["accent_dim"],
                fg_color="transparent", text_color=t["accent"],
                font=(f, 10, "bold"),
                command=lambda idx=i: self._profile_slot_clicked(idx, ctx)
            )
            pbtn.grid(row=0, column=i, padx=1, pady=2)
            self.profile_buttons[i] = pbtn
        self._refresh_profile_buttons_style(ctx)

        self.profile_save_btn = ctk.CTkButton(
            frame, text="[ SAVE TO SLOT ]",
            corner_radius=0, border_width=1, border_color=t["accent_dim"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 9, "bold"), command=lambda: self._toggle_profile_save_mode(ctx)
        )
        self.profile_save_btn.pack(fill="x", padx=20, pady=(4, 10))

        return frame

    def on_activate(self, ctx: ModuleContext) -> None:
        # НАВМИСНО нічого не робимо тут: on_activate() викликається з
        # головного GUI-потоку, а mss прив'язує свій внутрішній стан до
        # потоку, в якому створено інстанс. render() натомість завжди
        # виконується з робочого потоку рендеру (_video_processing) —
        # тому mss() створюється лениво там, при першому кадрі.
        pass

    def on_deactivate(self, ctx: ModuleContext) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass   # закриття з іншого потоку може впасти — це не критично
            self._sct = None

    def on_theme_change(self, ctx: ModuleContext) -> None:
        t = ctx.theme
        for s in (self.cap_x, self.cap_y, self.cap_w, self.cap_h,
                  self.gain, self.contrast, self.saturation):
            if s is not None:
                s.restyle(t)
        for lbl in self.section_labels:
            lbl.configure(fg=t["accent"], bg=t["bg"])
        if self.aspect_lock_chk is not None:
            self.aspect_lock_chk.configure(
                text_color=t["accent"], fg_color=t["accent"],
                hover_color=t["accent"], border_color=t["accent_dim"]
            )
        if self.profile_save_btn is not None:
            self.profile_save_btn.configure(border_color=t["accent_dim"], text_color=t["accent"])
        self._refresh_profile_buttons_style(ctx)

    def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
        if self._sct is None:
            # Створюємо тут, а не в on_activate() — це вже робочий потік
            # рендеру, і mss буде прив'язаний саме до нього.
            try:
                self._sct = mss()
            except Exception as e:
                ctx.log(f"SCREEN_CAPTURE_INIT_ERR: {str(e)[:60]}")
                return grid

        width  = int(max(16, self.cap_w.var.get())) if self.cap_w else self._pending_state["cap_w"]
        height = int(max(5,  self.cap_h.var.get())) if self.cap_h else self._pending_state["cap_h"]
        x_min, x_max = self.screen_left, self.screen_left + self.max_w
        y_min, y_max = self.screen_top,  self.screen_top  + self.max_h

        cap_x = self.cap_x.var.get() if self.cap_x else self._pending_state["cap_x"]
        cap_y = self.cap_y.var.get() if self.cap_y else self._pending_state["cap_y"]
        left = int(min(max(cap_x, x_min), x_max - width))
        top  = int(min(max(cap_y, y_min), y_max - height))

        reg = {"top": top, "left": left, "width": width, "height": height}
        try:
            img = np.array(self._sct.grab(reg))
        except Exception as e:
            ctx.log(f"SCREEN_GRAB_ERR: {str(e)[:60]}")
            return grid
        frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        gain = self.gain.var.get() if self.gain else self._pending_state["gain"]
        frame = cv2.convertScaleAbs(frame, alpha=gain, beta=0)

        contrast = self.contrast.var.get() if self.contrast else self._pending_state["contrast"]
        frame = cv2.convertScaleAbs(frame, alpha=contrast, beta=128 * (1 - contrast))

        saturation = self.saturation.var.get() if self.saturation else self._pending_state["saturation"]
        if abs(saturation - 1.0) > 1e-3:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[..., 1] = np.clip(hsv[..., 1] * saturation, 0, 255)
            frame = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        return cv2.resize(frame, (16, 5))

    def get_state(self) -> dict:
        if self.cap_x is None:
            return {**self._pending_state, "presets": self._presets}
        return {
            "cap_x": self.cap_x.var.get(), "cap_y": self.cap_y.var.get(),
            "cap_w": self.cap_w.var.get(), "cap_h": self.cap_h.var.get(),
            "gain": self.gain.var.get(), "contrast": self.contrast.var.get(),
            "saturation": self.saturation.var.get(),
            "aspect_lock": self.aspect_lock_var.get(),
            "presets": self._presets,
        }

    def set_state(self, state: dict) -> None:
        merged = {**self.DEFAULTS, **(state or {})}
        if merged["cap_x"] is None:
            merged["cap_x"] = self.screen_left + self.max_w // 2
        if merged["cap_y"] is None:
            merged["cap_y"] = self.screen_top + self.max_h // 2
        self._pending_state = merged
        self._presets = list(merged.get("presets") or [None] * 6)
        if self.cap_x is not None:
            self._apply_preset_values(merged)

    # --- aspect lock -------------------------------------------------------

    def _on_aspect_lock_toggle(self, ctx: ModuleContext):
        enabled = self.aspect_lock_var.get()
        if enabled:
            h = max(1.0, self.cap_h.var.get())
            self._aspect_ratio = self.cap_w.var.get() / h
        ctx.log(f"ASPECT_LOCK: {'ON' if enabled else 'OFF'}")

    def _on_cap_dim_change(self, which: str):
        if self.aspect_lock_var is None or not self.aspect_lock_var.get() or self._aspect_updating:
            return
        self._aspect_updating = True
        try:
            if which == "w":
                new_h = self.cap_w.var.get() / self._aspect_ratio
                new_h = min(max(new_h, 5), 600)
                self.cap_h.var.set(round(new_h))
            else:
                new_w = self.cap_h.var.get() * self._aspect_ratio
                new_w = min(max(new_w, 16), 800)
                self.cap_w.var.set(round(new_w))
        finally:
            self._aspect_updating = False

    # --- пресети -------------------------------------------------------

    def _toggle_profile_save_mode(self, ctx: ModuleContext):
        t = ctx.theme
        self._profile_save_armed = not self._profile_save_armed
        if self._profile_save_armed:
            self.profile_save_btn.configure(text="[ ОБЕРІТЬ СЛОТ... ]", border_color=t["alert"], text_color=t["alert"])
            ctx.log("PROFILE_SAVE: оберіть слот 1-6 для запису")
        else:
            self.profile_save_btn.configure(text="[ SAVE TO SLOT ]", border_color=t["accent_dim"], text_color=t["accent"])

    def _profile_slot_clicked(self, idx: int, ctx: ModuleContext):
        if self._profile_save_armed:
            self._save_profile(idx, ctx)
            self._toggle_profile_save_mode(ctx)
        else:
            self._load_profile(idx, ctx)

    def _gather_preset_values(self) -> dict:
        return {
            "cap_x": self.cap_x.var.get(), "cap_y": self.cap_y.var.get(),
            "cap_w": self.cap_w.var.get(), "cap_h": self.cap_h.var.get(),
            "gain": self.gain.var.get(), "contrast": self.contrast.var.get(),
            "saturation": self.saturation.var.get(),
            "aspect_lock": self.aspect_lock_var.get(),
        }

    def _apply_preset_values(self, data: dict):
        self.cap_x.var.set(data.get("cap_x", self.cap_x.var.get()))
        self.cap_y.var.set(data.get("cap_y", self.cap_y.var.get()))
        self.cap_w.var.set(data.get("cap_w", self.cap_w.var.get()))
        self.cap_h.var.set(data.get("cap_h", self.cap_h.var.get()))
        self.gain.var.set(data.get("gain", self.gain.var.get()))
        self.contrast.var.set(data.get("contrast", self.contrast.var.get()))
        self.saturation.var.set(data.get("saturation", self.saturation.var.get()))
        self.aspect_lock_var.set(data.get("aspect_lock", False))
        self._aspect_ratio = max(1e-6, self.cap_w.var.get()) / max(1.0, self.cap_h.var.get())

    def _save_profile(self, idx: int, ctx: ModuleContext):
        self._presets[idx] = self._gather_preset_values()
        self._refresh_profile_buttons_style(ctx)
        ctx.log(f"PROFILE_SAVED: слот {idx + 1}")

    def _load_profile(self, idx: int, ctx: ModuleContext):
        data = self._presets[idx] if idx < len(self._presets) else None
        if not data:
            ctx.log(f"PROFILE_LOAD: слот {idx + 1} порожній")
            return
        self._apply_preset_values(data)
        ctx.log(f"PROFILE_LOAD: слот {idx + 1}")

    def _refresh_profile_buttons_style(self, ctx: ModuleContext):
        t = ctx.theme
        for i, btn in self.profile_buttons.items():
            filled = i < len(self._presets) and self._presets[i] is not None
            if filled:
                btn.configure(fg_color=t["accent"], text_color=t["bg"], border_color=t["accent"])
            else:
                btn.configure(fg_color="transparent", text_color=t["accent"], border_color=t["accent_dim"])
