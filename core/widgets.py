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
core/widgets.py
================
Спільні UI-будівельні блоки, якими модулі користуються у своєму
build_ui(), щоб не дублювати верстку слайдера в кожному файлі.
Ядро (core/app.py) сам ці функції не викликає.
"""
from __future__ import annotations

from dataclasses import dataclass

import os

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog

from core.theme import GlowLabel


@dataclass
class SliderWidget:
    """Повертається make_slider() — модуль тримає це у себе (напр. у
    списку self._sliders), щоб мати змогу перефарбувати при зміні теми
    в on_theme_change(), і читає var.get() у своєму render()."""
    var:       tk.DoubleVar
    row:       ctk.CTkFrame
    slider:    ctk.CTkSlider
    label:     GlowLabel
    value_lbl: tk.Label
    decimals:  int = 0

    def restyle(self, theme: dict):
        self.slider.configure(
            button_color=theme["accent"], progress_color=theme["accent"],
            fg_color=theme["accent_dim"]
        )
        self.label.update_colors(bg=theme["bg"], fg=theme["accent"], glow=theme["glow"])
        self.value_lbl.configure(fg=theme["accent"], bg=theme["bg"])


def make_slider(
    parent, theme: dict, font_name: str, label: str,
    min_v: float, max_v: float, start_v: float, decimals: int = 0,
    grid_pos: tuple[int, int] | None = None, compact: bool = False,
    on_change=None,
) -> SliderWidget:
    """Створює рядок-слайдер у фірмовому стилі HEX_MC.
    За замовчуванням пакується вертикально (pack). Якщо передано
    grid_pos=(row, col), рядок розміщується у батьківському
    grid-контейнері (компактна 2-колонкова сітка, як у SCREEN)."""
    t, f = theme, font_name
    row = ctk.CTkFrame(parent, fg_color="transparent")
    if grid_pos is not None:
        row.grid(row=grid_pos[0], column=grid_pos[1], padx=8, pady=3, sticky="ew")
    else:
        row.pack(fill="x", padx=20, pady=6)

    lbl = GlowLabel(
        row, text=label, font=(f, 10, "bold"),
        foreground=t["accent"], glow_color=t["glow"], bg=t["bg"]
    )
    lbl.pack(side="left", padx=(0, 15 if not compact else 8))

    val_lbl = tk.Label(
        row, text=f"{start_v:.{decimals}f}", font=(f, 10, "bold"),
        fg=t["accent"], bg=t["bg"], width=6, anchor="e"
    )
    val_lbl.pack(side="right", padx=(10, 0))

    var = tk.DoubleVar(value=start_v)

    def _on_change(*_a):
        try:
            val_lbl.configure(text=f"{var.get():.{decimals}f}")
        except Exception:
            pass
        if on_change is not None:
            on_change(*_a)
    var.trace_add("write", _on_change)

    slider = ctk.CTkSlider(
        row, from_=min_v, to=max_v, variable=var,
        button_color=t["accent"], progress_color=t["accent"],
        fg_color=t["accent_dim"], corner_radius=0, height=14
    )
    slider.pack(side="left", fill="x", expand=True)

    return SliderWidget(var=var, row=row, slider=slider, label=lbl,
                         value_lbl=val_lbl, decimals=decimals)


# ---------------------------------------------------------------------------
# FileLibraryPanel — спільний блок "папка з файлами + прокручуваний список
# кнопок-активаторів". Використовується modules/gif_player.py (.gif) та
# modules/scripts.py (.py) — щоб не дублювати однакову верстку й логіку
# сканування папки в обох модулях.
# ---------------------------------------------------------------------------
class FileLibraryPanel:
    """Інкапсулює: мітку обраної папки, кнопки [ОБРАТИ ПАПКУ]/[ОНОВИТИ],
    прокручуваний список файлів заданого розширення. Сам не знає, ЩО
    робити при активації файлу — це робить callback on_select(path).

    Модуль сам відповідає за збереження шляху до папки у своєму
    get_state()/set_state() — цей клас лише зберігає folder у пам'яті
    (self.folder) на час роботи."""

    def __init__(
        self, parent, ctx, *,
        extension: str,               # напр. ".gif" або ".py"
        on_select,                    # callable(path: str) — виклик при кліку на файл
        choose_title: str,            # заголовок діалогу вибору папки
        empty_no_folder_text: str,    # текст, коли папка ще не обрана
        empty_no_files_text: str,     # текст, коли в папці немає файлів
        list_height: int = 150,
        collapsible: bool = False,    # True → компактний рядок "активний файл" +
                                       # розгортання по кліку, замість завжди
                                       # видимого списку. Для модулів, де під
                                       # списком ще є власний UI (напр. SCRIPTS),
                                       # який інакше список перекриває.
    ):
        self.extension            = extension.lower()
        self.on_select             = on_select
        self.choose_title          = choose_title
        self.empty_no_folder_text  = empty_no_folder_text
        self.empty_no_files_text   = empty_no_files_text
        self.collapsible           = collapsible
        self.folder    = ""
        self.active_path = None
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._expanded = not collapsible   # collapsible-панелі стартують згорнутими

        t, f = ctx.theme, ctx.font_name

        self.frame = ctk.CTkFrame(parent, fg_color="transparent")

        self.toggle_btn = None
        self.active_file_lbl = None
        self.header = None
        if self.collapsible:
            header = ctk.CTkFrame(self.frame, fg_color="transparent")
            header.pack(fill="x")
            self.header = header   # публічний — виклик може докинути сюди свої віджети (side="right")

            self.toggle_btn = ctk.CTkButton(
                header, text="▸", width=28, corner_radius=0,
                border_width=1, border_color=t["accent_dim"],
                fg_color="transparent", text_color=t["accent"],
                font=(f, 11, "bold"), command=lambda: self._toggle(ctx)
            )
            self.toggle_btn.pack(side="left", padx=(10, 6), pady=6)

            self.active_file_lbl = tk.Label(
                header, text=self._active_file_display(),
                font=(f, 10, "bold"), fg=t["accent"], bg=t["bg"],
                anchor="w", justify="left",
            )
            self.active_file_lbl.pack(side="left", fill="x", expand=True, pady=6)

        # body — усе, що ховається/показується при згортанні. Для
        # collapsible=False це просто те саме, що й self.frame напряму.
        self.body = ctk.CTkFrame(self.frame, fg_color="transparent") if self.collapsible else self.frame

        self.folder_label = tk.Label(
            self.body, text=self._folder_display(),
            font=(f, 9), fg=t["accent_dim"], bg=t["bg"],
            wraplength=220, justify="left",
        )
        self.folder_label.pack(anchor="nw", padx=10, pady=(10, 6))

        btn_row = ctk.CTkFrame(self.body, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(0, 4))

        self.choose_btn = ctk.CTkButton(
            btn_row, text="[ ОБРАТИ ПАПКУ ]",
            corner_radius=0, border_width=1, border_color=t["accent"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 10, "bold"), command=lambda: self._choose_folder(ctx)
        )
        self.choose_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.refresh_btn = ctk.CTkButton(
            btn_row, text="[ ОНОВИТИ ]",
            corner_radius=0, border_width=1, border_color=t["accent"],
            fg_color="transparent", text_color=t["accent"],
            font=(f, 10, "bold"), command=lambda: self.refresh(ctx)
        )
        self.refresh_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        self.list_frame = ctk.CTkScrollableFrame(
            self.body, fg_color=t["panel_bg"],
            border_width=1, border_color=t["accent_dim"], height=list_height,
        )
        self.list_frame.pack(fill="x", padx=10, pady=(4, 10))

        if self.collapsible:
            # body ще не пакується — стартуємо згорнутими (self._expanded=False)
            pass
        # (для collapsible=False self.body IS self.frame — вже запаковано викликачем)

    # --- публічне ------------------------------------------------------

    def set_folder(self, folder: str, ctx):
        """Встановлює папку без відкриття діалогу (напр. при set_state())."""
        self.folder = folder
        self.folder_label.configure(text=self._folder_display())
        self.refresh(ctx)

    def refresh(self, ctx):
        """Пересканує обрану папку та перебудовує список кнопок."""
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._buttons.clear()

        t, f = ctx.theme, ctx.font_name

        if not self.folder or not os.path.isdir(self.folder):
            tk.Label(
                self.list_frame, text=self.empty_no_folder_text,
                font=(f, 9), fg=t["accent_dim"], bg=t["panel_bg"],
            ).pack(anchor="w", padx=6, pady=6)
            return

        try:
            files = sorted(
                fn for fn in os.listdir(self.folder)
                if fn.lower().endswith(self.extension)
            )
        except OSError as e:
            ctx.log(f"LIBRARY_SCAN_ERROR: {str(e)[:60]}")
            return

        if not files:
            tk.Label(
                self.list_frame, text=self.empty_no_files_text,
                font=(f, 9), fg=t["accent_dim"], bg=t["panel_bg"],
            ).pack(anchor="w", padx=6, pady=6)
            return

        for fn in files:
            full_path = os.path.join(self.folder, fn)
            btn = ctk.CTkButton(
                self.list_frame, text=fn,
                corner_radius=0, border_width=1, border_color=t["accent_dim"],
                fg_color="transparent", text_color=t["accent"],
                anchor="w", font=(f, 10),
                command=lambda p=full_path: self._activate(p, ctx)
            )
            btn.pack(fill="x", pady=1)
            self._buttons[full_path] = btn

        self.highlight_active(ctx)
        ctx.log(f"LIBRARY_SCAN: знайдено {len(files)} файл(ів)")

    def highlight_active(self, ctx):
        t = ctx.theme
        for path, btn in self._buttons.items():
            if path == self.active_path:
                btn.configure(fg_color=t["accent"], text_color=t["bg"])
            else:
                btn.configure(fg_color="transparent", text_color=t["accent"])
        self._update_active_file_label()

    def on_theme_change(self, ctx):
        t = ctx.theme
        self.choose_btn.configure(border_color=t["accent"], text_color=t["accent"])
        self.refresh_btn.configure(border_color=t["accent"], text_color=t["accent"])
        self.list_frame.configure(fg_color=t["panel_bg"], border_color=t["accent_dim"])
        self.folder_label.configure(fg=t["accent_dim"], bg=t["bg"])
        if self.toggle_btn is not None:
            self.toggle_btn.configure(border_color=t["accent_dim"], text_color=t["accent"])
        if self.active_file_lbl is not None:
            self.active_file_lbl.configure(fg=t["accent"], bg=t["bg"])
        self.refresh(ctx)   # перебудовує кнопки й порожні мітки з новими кольорами

    # --- collapsible -----------------------------------------------------

    def _active_file_display(self) -> str:
        if self.active_path:
            return f"📄 {os.path.basename(self.active_path)}"
        return "Скрипт не обрано"

    def _update_active_file_label(self):
        if self.active_file_lbl is not None:
            self.active_file_lbl.configure(text=self._active_file_display())

    def _set_expanded(self, expanded: bool):
        self._expanded = expanded
        if expanded:
            self.body.pack(fill="x")
            if self.toggle_btn is not None:
                self.toggle_btn.configure(text="▾")
        else:
            self.body.pack_forget()
            if self.toggle_btn is not None:
                self.toggle_btn.configure(text="▸")

    def _toggle(self, ctx):
        self._set_expanded(not self._expanded)

    # --- приватне --------------------------------------------------------

    def _folder_display(self) -> str:
        return f"Папка: {self.folder}" if self.folder else "Папка не обрана"

    def _choose_folder(self, ctx):
        folder = filedialog.askdirectory(title=self.choose_title)
        if not folder:
            return
        self.folder = folder
        self.folder_label.configure(text=self._folder_display())
        ctx.log(f"LIBRARY_FOLDER: {folder}")
        if self.collapsible and not self._expanded:
            self._set_expanded(True)   # щойно обрали папку — одразу показати список
        self.refresh(ctx)

    def _activate(self, path: str, ctx):
        self.on_select(path, ctx)
        self.highlight_active(ctx)
        if self.collapsible:
            self._set_expanded(False)   # обрали файл — звільняємо місце під UI скрипта