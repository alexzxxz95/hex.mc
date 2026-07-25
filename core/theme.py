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
core/theme.py
=============
GlowLabel — Canvas-мітка з ефектом тіні/світіння, що використовується
в шапці/логотипі рушія. Сама палітра (THEMES) живе в core/config.py,
щоб не плодити два джерела правди про кольори.
"""
import tkinter as tk

from core.config import THEMES


class GlowLabel(tk.Canvas):
    """Кастомний віджет для тексту з ефектом світіння."""

    def __init__(self, master, text, font, foreground=None, glow_color=None, **kwargs):
        theme = THEMES["dark"]
        bg_color = kwargs.pop("bg", theme["bg"])
        super().__init__(master, bg=bg_color, highlightthickness=0, **kwargs)
        self.text       = text
        self.font       = font
        self.fg         = foreground  or theme["accent"]
        self.glow       = glow_color  or theme["glow"]
        self.draw_text()

    def draw_text(self):
        from core.config import CONF   # живий словник — читаємо актуальне значення щоразу
        low_perf = CONF.get("ui", {}).get("low_perf_mode", False)

        self.delete("all")
        if not low_perf:
            self.create_text(2, 2, text=self.text, font=self.font, fill=self.glow, anchor="nw")
        tid  = self.create_text(0, 0, text=self.text, font=self.font, fill=self.fg,   anchor="nw")
        bbox = self.bbox(tid)
        if bbox:
            self.config(width=bbox[2] - bbox[0] + 4, height=bbox[3] - bbox[1] + 4)

    def update_text(self, new_text):
        self.text = new_text
        self.draw_text()

    def update_colors(self, bg, fg, glow):
        self.config(bg=bg)
        self.fg   = fg
        self.glow = glow
        self.draw_text()
