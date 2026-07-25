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
modules/clock.py
=================
Найпростіший вбудований модуль — pull-рендер без слайдерів і файлових
бібліотек. Показує години:хвилини піксельним шрифтом 3×5, з трьома
кольорами, що обираються через системний colorchooser.

Слугує еталоном мінімального контракту HexModuleBase.
"""
import numpy as np
import customtkinter as ctk
import tkinter as tk
from tkinter import colorchooser
from datetime import datetime

from core.config import FONT_3x5, CLOCK_POS
from core.plugin_base import HexModuleBase, ModuleContext, register_module


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


@register_module
class ClockModule(HexModuleBase):
    key   = "clock"
    label = "Clock"
    owns_thread = False

    DEFAULT_HOUR_HEX = "#662A02"
    DEFAULT_DOT_HEX  = "#5E0D09"
    DEFAULT_MIN_HEX  = "#662A02"

    def __init__(self):
        self.hour_hex = self.DEFAULT_HOUR_HEX
        self.dot_hex  = self.DEFAULT_DOT_HEX
        self.min_hex  = self.DEFAULT_MIN_HEX

        self.hour_color = np.array(self._bgr(self.hour_hex), dtype=np.uint8)
        self.dot_color  = np.array(self._bgr(self.dot_hex),  dtype=np.uint8)
        self.min_color  = np.array(self._bgr(self.min_hex),  dtype=np.uint8)

        # Посилання на віджети — None, поки build_ui() не викликано
        self.hdr_lbl  = None
        self.hour_btn = None
        self.dot_btn  = None
        self.min_btn  = None

    # --- допоміжне -----------------------------------------------------

    @staticmethod
    def _bgr(hex_color: str) -> tuple[int, int, int]:
        r, g, b = _hex_to_rgb(hex_color)
        return (b, g, r)

    @staticmethod
    def _text_color_for(hex_color: str) -> str:
        r, g, b = _hex_to_rgb(hex_color)
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        return "#000000" if luminance > 140 else "#FFFFFF"

    def _apply_hex(self, target: str, hex_color: str):
        bgr = np.array(self._bgr(hex_color), dtype=np.uint8)
        text_on_btn = self._text_color_for(hex_color)

        if target == "hour":
            self.hour_hex, self.hour_color = hex_color, bgr
            if self.hour_btn is not None:
                self.hour_btn.configure(text=hex_color, fg_color=hex_color, text_color=text_on_btn)
        elif target == "dot":
            self.dot_hex, self.dot_color = hex_color, bgr
            if self.dot_btn is not None:
                self.dot_btn.configure(text=hex_color, fg_color=hex_color, text_color=text_on_btn)
        elif target == "min":
            self.min_hex, self.min_color = hex_color, bgr
            if self.min_btn is not None:
                self.min_btn.configure(text=hex_color, fg_color=hex_color, text_color=text_on_btn)

    def _pick_color(self, target: str, ctx: ModuleContext):
        if target == "hour":
            current = self.hour_hex
        elif target == "dot":
            current = self.dot_hex
        else:
            current = self.min_hex

        _, hex_color = colorchooser.askcolor(color=current, title="Оберіть колір")
        if hex_color is None:
            return  # користувач скасував вибір
        self._apply_hex(target, hex_color)
        ctx.log(f"CLOCK_{target.upper()}_COLOR: {hex_color}")

    # --- контракт HexModuleBase -----------------------------------------

    def build_ui(self, parent, ctx: ModuleContext) -> ctk.CTkFrame:
        t, f = ctx.theme, ctx.font_name
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        self.hdr_lbl = tk.Label(
            frame, text="(години / крапки / хвилини)",
            font=(f, 10, "bold"), fg=t["accent"], bg=t["bg"],
        )
        self.hdr_lbl.pack(anchor="w", padx=20, pady=(15, 6))

        # Контейнер для розташування кнопок в один рядок
        btns_container = ctk.CTkFrame(frame, fg_color="transparent")
        btns_container.pack(anchor="w", padx=20, pady=(0, 15))

        self.hour_btn = ctk.CTkButton(
            btns_container, text=self.hour_hex, width=80,
            corner_radius=0, border_width=1, border_color=t["accent"],
            fg_color=self.hour_hex, text_color=self._text_color_for(self.hour_hex),
            font=(f, 11, "bold"),
            command=lambda: self._pick_color("hour", ctx)
        )
        self.hour_btn.grid(row=0, column=0, padx=(0, 8))

        self.dot_btn = ctk.CTkButton(
            btns_container, text=self.dot_hex, width=80,
            corner_radius=0, border_width=1, border_color=t["accent"],
            fg_color=self.dot_hex, text_color=self._text_color_for(self.dot_hex),
            font=(f, 11, "bold"),
            command=lambda: self._pick_color("dot", ctx)
        )
        self.dot_btn.grid(row=0, column=1, padx=(0, 8))

        self.min_btn = ctk.CTkButton(
            btns_container, text=self.min_hex, width=80,
            corner_radius=0, border_width=1, border_color=t["accent"],
            fg_color=self.min_hex, text_color=self._text_color_for(self.min_hex),
            font=(f, 11, "bold"),
            command=lambda: self._pick_color("min", ctx)
        )
        self.min_btn.grid(row=0, column=2)

        return frame

    def on_theme_change(self, ctx: ModuleContext) -> None:
        t = ctx.theme
        if self.hdr_lbl is not None:
            self.hdr_lbl.configure(fg=t["accent"], bg=t["bg"])

    def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
        now   = datetime.now()
        h_str = now.strftime("%H")
        m_str = now.strftime("%M")

        self._draw_digit(grid, h_str[0], CLOCK_POS["h0"], self.hour_color)
        self._draw_digit(grid, h_str[1], CLOCK_POS["h1"], self.hour_color)
        if now.second % 2 == 0:
            self._draw_digit(grid, ":", CLOCK_POS["colon"], self.dot_color)
        self._draw_digit(grid, m_str[0], CLOCK_POS["m0"], self.min_color)
        self._draw_digit(grid, m_str[1], CLOCK_POS["m1"], self.min_color)
        return grid

    @staticmethod
    def _draw_digit(frame: np.ndarray, char: str, x: int, color):
        if char not in FONT_3x5:
            return
        for r in range(5):
            for c in range(3):
                if (FONT_3x5[char][r] >> (2 - c)) & 1:
                    px = x + c
                    if 0 <= px < 16:
                        frame[r, px] = color

    def get_state(self) -> dict:
        return {
            "hour_hex": self.hour_hex,
            "dot_hex":  self.dot_hex,
            "min_hex":  self.min_hex
        }

    def set_state(self, state: dict) -> None:
        self._apply_hex("hour", state.get("hour_hex", self.DEFAULT_HOUR_HEX))
        self._apply_hex("dot",  state.get("dot_hex",  self.DEFAULT_DOT_HEX))
        self._apply_hex("min",  state.get("min_hex",  self.DEFAULT_MIN_HEX))