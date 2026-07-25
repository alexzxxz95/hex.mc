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
modules/softbox.py
===================
Другий за складністю вбудований модуль — три слайдери
(BRIGHTNESS / TEMPERATURE / COLOR_HUE), без файлових бібліотек.
Перевіряє core.widgets.make_slider та збереження стану кількох
числових полів у CONF["modules"]["softbox"].
"""
import cv2
import numpy as np
import customtkinter as ctk

from core.plugin_base import HexModuleBase, ModuleContext, register_module
from core.widgets import make_slider, SliderWidget


@register_module
class SoftboxModule(HexModuleBase):
    key   = "softbox"
    label = "SOFTBOX"
    owns_thread = False

    DEFAULTS = {"brightness": 128.0, "temperature": 50.0, "hue": 0.0}

    def __init__(self):
        self._pending_state = dict(self.DEFAULTS)   # застосовується у build_ui()
        self.brightness: SliderWidget | None = None
        self.temperature: SliderWidget | None = None
        self.hue: SliderWidget | None = None

    # --- контракт HexModuleBase -----------------------------------------

    def build_ui(self, parent, ctx: ModuleContext) -> ctk.CTkFrame:
        t, f = ctx.theme, ctx.font_name
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        self.brightness = make_slider(
            frame, t, f, "BRIGHTNESS", 0, 255, self._pending_state["brightness"]
        )
        self.temperature = make_slider(
            frame, t, f, "TEMPERATURE", 0, 100, self._pending_state["temperature"]
        )
        self.hue = make_slider(
            frame, t, f, "COLOR_HUE", 0, 180, self._pending_state["hue"]
        )

        return frame

    def on_theme_change(self, ctx: ModuleContext) -> None:
        for s in (self.brightness, self.temperature, self.hue):
            if s is not None:
                s.restyle(ctx.theme)

    def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
        br   = int(self.brightness.var.get()) if self.brightness else int(self._pending_state["brightness"])
        hue  = int(self.hue.var.get())        if self.hue        else int(self._pending_state["hue"])
        temp = (self.temperature.var.get() if self.temperature else self._pending_state["temperature"]) / 100.0

        if hue > 0:
            hsv_color = np.uint8([[[hue, 255, br]]])
            bgr_color = cv2.cvtColor(hsv_color, cv2.COLOR_HSV2BGR)[0][0]
            grid[:, :] = bgr_color
        else:
            warm  = np.array([60,  160, 255], dtype=np.float32)
            cold  = np.array([255, 220, 200], dtype=np.float32)
            mixed = warm * (1.0 - temp) + cold * temp
            grid[:, :] = (mixed * (br / 255.0)).astype(np.uint8)
        return grid

    def get_state(self) -> dict:
        if self.brightness is None:
            return dict(self._pending_state)   # панель ще не побудована
        return {
            "brightness":  self.brightness.var.get(),
            "temperature": self.temperature.var.get(),
            "hue":         self.hue.var.get(),
        }

    def set_state(self, state: dict) -> None:
        merged = {**self.DEFAULTS, **(state or {})}
        self._pending_state = merged
        # Якщо панель вже побудована (перемикання теми/повторний виклик) —
        # застосовуємо одразу до наявних слайдерів.
        if self.brightness is not None:
            self.brightness.var.set(merged["brightness"])
            self.temperature.var.set(merged["temperature"])
            self.hue.var.set(merged["hue"])
