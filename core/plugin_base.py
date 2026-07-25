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
core/plugin_base.py
====================
Контракт, за яким рушій (core/app.py) спілкується з модулями
(modules/clock.py, modules/screen.py, ...) — і за яким користувач
зможе писати власні підключні модулі, не чіпаючи ядро.

Два типи модулів:
  * PULL-модуль (owns_thread=False) — не має власного I/O. Рушій сам,
    у своєму єдиному циклі обробки кадру, викликає render(grid, ctx)
    щотік і надсилає результат на пристрій. Приклади: clock, softbox.
  * PUSH-модуль (owns_thread=True) — сам керує джерелом даних у
    власному потоці (BLE, мережеве джерело на кшталт Minecraft-мосту,
    скрипти з непередбачуваним таймінгом). Рушій в on_activate() лише
    каже модулю стартувати, а модуль сам штовхає готові кадри через
    ctx.request_redraw(grid), коли вважає за потрібне. render() у
    такому модулі можна не реалізовувати.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

import numpy as np

from core.serial_manager import SerialManager


@dataclass
class ModuleContext:
    """Міст між ядром і модулем. Модуль отримує лише це — прямого
    доступу до внутрішніх атрибутів MatrixApp модулі не мають."""

    serial_mgr: SerialManager
    conf: dict                                   # повний CONF; модуль лізе у conf["modules"][key]
    log: Callable[[str], None]                   # ядро.log_process
    theme: dict                                  # поточна THEMES[current_theme]
    font_name: str
    workspace_size: tuple[int, int]               # (width, height) робочої області для верстки панелі
    request_redraw: Callable[[np.ndarray], None]  # push-модулі штовхають готовий кадр сюди
    sensor_data: dict                             # останні показники телеметрії з пристрою (жива посилка)
    interrupt_display: Callable[[np.ndarray, float], None]
    # Перебиває кадр АКТИВНОГО режиму на `duration` секунд, незалежно від
    # того, який режим зараз обрано в меню — після завершення рушій сам
    # повертається до звичайного рендеру активного модуля. Признач для
    # короткочасних сповіщень (напр. modules/notify.py). НЕ призначено
    # для звичайного рендеру — це "поверх усього", один раз на подію.


@runtime_checkable
class HexModule(Protocol):
    """Формальний контракт. Використовується лише для type-checking —
    на практиці модулі успадковують HexModuleBase (нижче), щоб не
    дублювати no-op реалізації в кожному файлі."""

    key: str            # унікальний ідентифікатор, напр. "clock"
    label: str           # текст пункту меню, напр. "FEED"
    owns_thread: bool    # True → push-модуль з власним потоком

    def build_ui(self, parent, ctx: ModuleContext):
        ...

    def on_engine_ready(self, ctx: ModuleContext) -> None:
        ...

    def on_activate(self, ctx: ModuleContext) -> None:
        ...

    def on_deactivate(self, ctx: ModuleContext) -> None:
        ...

    def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
        ...

    def on_theme_change(self, ctx: ModuleContext) -> None:
        ...

    def get_state(self) -> dict:
        ...

    def set_state(self, state: dict) -> None:
        ...


class HexModuleBase:
    """Зручний базовий клас зі стандартними (no-op) реалізаціями —
    новий модуль перевизначає лише те, що йому реально потрібне."""

    key: str = "unnamed"
    label: str = "UNNAMED"
    owns_thread: bool = False
    # Якщо модуль має власну індикацію статусу в build_ui() (напр.
    # бібліотека файлів зі станом SCAN/LOADED/ERROR у списку), він може
    # заховати загальну лог-панель ядра на час своєї активності.
    hides_log_feed: bool = False
    # Альтернатива повному приховуванню: залишити лог-панель видимою,
    # але компактною (напр. одна стрічка — "живий" індикатор без
    # витрати простору під власний UI модуля). None → стандартна
    # висота ядра (MatrixApp.DEFAULT_FEED_HEIGHT).
    log_feed_height: int | None = None

    def build_ui(self, parent, ctx: ModuleContext):
        """Має повернути CTkFrame з елементами керування модуля.
        Рушій сам пакує/ховає цей фрейм при перемиканні режимів."""
        raise NotImplementedError

    def on_engine_ready(self, ctx: ModuleContext) -> None:
        """Викликається РІВНО ОДИН РАЗ для КОЖНОГО зареєстрованого
        модуля одразу після старту рушія — незалежно від того, чи цей
        модуль зараз активний/обраний у меню. Признач для модулів, яким
        треба працювати у фоні постійно (напр. слухати системні події),
        а не лише поки їх обрано як активний режим — на відміну від
        on_activate()/on_deactivate(), які прив'язані саме до вибору в
        меню. `ctx` тут можна безпечно зберегти собі надовго — методи
        на ньому (log/interrupt_display/...) є стабільними прив'язаними
        методами ядра, не одноразовим знімком."""
        pass

    def on_activate(self, ctx: ModuleContext) -> None:
        """Викликається при вході в режим модуля. Push-модулі
        стартують тут власний потік."""
        pass

    def on_deactivate(self, ctx: ModuleContext) -> None:
        """Викликається при виході з режиму модуля. Push-модулі
        зупиняють тут власний потік."""
        pass

    def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
        """Для pull-модулів: повернути новий кадр 5×16×3 (BGR uint8).
        Push-модулі (owns_thread=True) можуть не перевизначати —
        рушій цей метод для них не викликає."""
        return grid

    def on_theme_change(self, ctx: ModuleContext) -> None:
        """Викликається рушієм при перемиканні теми (dark/light), якщо
        панель модуля вже була побудована. Модуль перефарбовує СВОЇ
        віджети тут (ядро про їхній вміст нічого не знає)."""
        pass

    def get_state(self) -> dict:
        """Стан для автозбереження у CONF["modules"][self.key]."""
        return {}

    def set_state(self, state: dict) -> None:
        """Відновлення стану з CONF["modules"][self.key] при старті."""
        pass


# ---------------------------------------------------------------------------
# Реєстр модулів
# ---------------------------------------------------------------------------
# Модуль реєструє себе одним рядком у власному файлі:
#   from core.plugin_base import HexModuleBase, register_module
#   class ClockModule(HexModuleBase): ...
#   register_module(ClockModule)
#
# Ядро лише викликає discover_modules(), яка імпортує пакет modules/
# (кожен файл при імпорті сам себе реєструє) і повертає готові
# екземпляри в порядку реєстрації — без жодної згадки конкретних
# модулів у коді ядра.
_REGISTRY: list[type[HexModuleBase]] = []


def register_module(module_cls: type[HexModuleBase]) -> type[HexModuleBase]:
    """Може використовуватись і як декоратор: @register_module."""
    if module_cls not in _REGISTRY:
        _REGISTRY.append(module_cls)
    return module_cls


def discover_modules() -> list[HexModuleBase]:
    """Імпортує пакет modules/ (тригерить самореєстрацію файлів
    усередині) і повертає по одному екземпляру кожного зареєстрованого
    модуля, у порядку реєстрації."""
    import importlib
    import pkgutil
    import modules as modules_pkg

    for _, mod_name, _ in pkgutil.iter_modules(modules_pkg.__path__):
        importlib.import_module(f"modules.{mod_name}")

    return [cls() for cls in _REGISTRY]