# HEX_MC — Modules Dev Guide

Гайд для розробників, що хочуть додати **новий режим відображення** (пункт бічного меню) у HEX_MC як окремий модуль у `modules/`.

Якщо натомість потрібно лише швидко намалювати свою анімацію без окремого пункту меню й окремої панелі — вам, ймовірно, простіше в режим **SCRIPTS**, див. [SCRIPTS_DEV_GUIDE.md](SCRIPTS_DEV_GUIDE.md).

---

## Зміст

- [Принцип](#принцип)
- [Мінімальний модуль](#мінімальний-модуль)
- [Контракт HexModuleBase](#контракт-hexmodulebase)
- [ModuleContext](#modulecontext)
- [Формат кадру (grid)](#формат-кадру-grid)
- [Pull vs Push модулі](#pull-vs-push-модулі)
- [Збереження стану](#збереження-стану)
- [Спільні UI-компоненти](#спільні-ui-компоненти)
- [Перебивання екрана (interrupt_display)](#перебивання-екрана-interrupt_display)
- [Приклад: повний pull-модуль зі слайдерами](#приклад-повний-pull-модуль-зі-слайдерами)
- [Контрольний список перед PR](#контрольний-список-перед-pr)

---

## Принцип

Ядро (`core/app.py`) нічого не знає про конкретні режими. Воно імпортує пакет `modules/`, кожен файл усередині сам себе реєструє через `register_module()`, і ядро отримує вже готовий список екземплярів — у порядку реєстрації = порядок пунктів меню.

**Щоб додати модуль, достатньо покласти новий файл у `modules/`. Код ядра (`core/app.py`) не редагується.**

## Мінімальний модуль

```python
# modules/hello.py
import numpy as np
import customtkinter as ctk

from core.plugin_base import HexModuleBase, ModuleContext, register_module


@register_module
class HelloModule(HexModuleBase):
    key   = "hello"       # унікальний ідентифікатор — використовується як ключ
                           # у CONF["modules"][key] і в CONF["modules_enabled"][key]
    label = "HELLO"        # текст пункту бічного меню

    def build_ui(self, parent, ctx: ModuleContext) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(frame, text="Нема налаштувань").pack(pady=20)
        return frame

    def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
        grid[:, :] = (0, 128, 255)   # BGR — суцільна заливка
        return grid
```

Файл кладеться в `modules/hello.py` — при наступному запуску застосунку пункт `HELLO` з'явиться в меню автоматично.

## Контракт HexModuleBase

Успадковуйтесь від `core.plugin_base.HexModuleBase` і перевизначайте лише те, що реально потрібно — базовий клас має no-op реалізації для всього.

### Атрибути класу

| Атрибут | Тип | За замовч. | Призначення |
|---|---|---|---|
| `key` | `str` | `"unnamed"` | Унікальний ідентифікатор модуля. Використовується як ключ конфігу — **не змінюйте після релізу**, інакше користувачі втратять збережений стан модуля. |
| `label` | `str` | `"UNNAMED"` | Текст кнопки в бічному меню. |
| `owns_thread` | `bool` | `False` | `False` = pull-модуль (ядро само кличе `render()` щокадру). `True` = push-модуль (модуль сам штовхає кадри). Див. [Pull vs Push](#pull-vs-push-модулі). |
| `hides_log_feed` | `bool` | `False` | `True` → на час активності модуля ядро повністю ховає загальну панель `SYSTEM_DATA_FEED` (якщо у модуля своя індикація статусу — напр. бібліотека файлів). |
| `log_feed_height` | `int \| None` | `None` | Альтернатива повному приховуванню — задати компактнішу висоту лог-панелі (напр. `48` — заголовок + один рядок), замість стандартних `MatrixApp.DEFAULT_FEED_HEIGHT` (180px). |

### Методи

```python
def build_ui(self, parent, ctx: ModuleContext) -> ctk.CTkFrame:
    """ОБОВ'ЯЗКОВО перевизначити. Повертає CTkFrame з елементами
    керування модуля. Викликається РІВНО ОДИН РАЗ (при першому виборі
    режиму) — ядро кешує результат і надалі лише pack()/pack_forget()
    той самий фрейм. Тому build_ui() — місце для СТВОРЕННЯ віджетів,
    а не для читання поточного стану щоразу."""

def on_engine_ready(self, ctx: ModuleContext) -> None:
    """Викликається РІВНО ОДИН РАЗ для КОЖНОГО увімкненого зареєстрованого
    модуля одразу після старту рушія — незалежно від того, чи цей модуль
    зараз обраний у меню. Призначено для модулів, яким треба працювати
    у фоні ПОСТІЙНО (напр. слухати системні події), а не лише поки
    режим активний. `ctx` тут можна безпечно зберегти на довго — методи
    на ньому стабільні."""

def on_activate(self, ctx: ModuleContext) -> None:
    """Вхід у режим (користувач обрав пункт меню). Push-модулі
    стартують тут власний потік / відкривають ресурси (напр. mss-сесію)."""

def on_deactivate(self, ctx: ModuleContext) -> None:
    """Вихід з режиму. Push-модулі зупиняють тут потік / звільняють ресурси."""

def render(self, grid: np.ndarray, ctx: ModuleContext) -> np.ndarray:
    """Для pull-модулів: повернути новий кадр 5×16×3 BGR uint8.
    Викликається ядром ~30 разів/сек, ЛИШЕ поки модуль активний.
    Push-модулі можуть не перевизначати — ядро для них це не викликає."""
    return grid

def on_theme_change(self, ctx: ModuleContext) -> None:
    """Викликається при перемиканні dark/light, ЯКЩО панель модуля вже
    була побудована. Перефарбуйте СВОЇ віджети тут — ядро про їхній
    вміст нічого не знає."""

def get_state(self) -> dict:
    """Що зберігати в CONF["modules"][self.key] при автозбереженні."""
    return {}

def set_state(self, state: dict) -> None:
    """Відновлення стану при старті застосунку. ВАЖЛИВО: викликається
    ДО build_ui() — на цей момент віджетів ще не існує. Кешуйте значення
    у self._pending_state і застосовуйте їх у build_ui()."""
```

## ModuleContext

Єдина точка доступу модуля до ядра — `ModuleContext` (dataclass, `core/plugin_base.py`). Прямого доступу до внутрішніх атрибутів `MatrixApp` модулі не мають.

| Поле | Тип | Опис |
|---|---|---|
| `serial_mgr` | `SerialManager` | Обгортка над COM-портом. Зазвичай не потрібна напряму — надсилання кадру ядро робить само після `render()`/`request_redraw()`. |
| `conf` | `dict` | Живий словник `CONF`. Своя гілка — `conf["modules"][self.key]`, але зазвичай досить `get_state()`/`set_state()`. |
| `log` | `Callable[[str], None]` | Пише рядок у системний лог (`SYSTEM_DATA_FEED`). |
| `theme` | `dict` | Поточна палітра — `THEMES["dark"]` або `THEMES["light"]`, ключі: `bg`, `panel_bg`, `preview_bg`, `accent`, `accent_dim`, `text_dark`, `alert`, `glow`. |
| `font_name` | `str` | Шрифт інтерфейсу з конфігу (`CONF["ui"]["font_name"]`). |
| `workspace_size` | `tuple[int, int]` | Поточний розмір робочої області (ширина, висота) — для верстки панелі. |
| `request_redraw` | `Callable[[np.ndarray], None]` | Push-модулі штовхають сюди готовий кадр 5×16×3 BGR зі свого потоку. |
| `sensor_data` | `dict` | Останні показники телеметрії з пристрою (`temp`, `light`, `ax`/`ay`/`az`, `btn_last_event`, ...) — жива посилка, оновлюється фоновим потоком. |
| `interrupt_display` | `Callable[[np.ndarray, float], None]` | Перебиває кадр АКТИВНОГО режиму на N секунд, незалежно від обраного пункту меню. Див. [нижче](#перебивання-екрана-interrupt_display). |

## Формат кадру (grid)

- `numpy.ndarray`, форма `(5, 16, 3)`, `dtype=np.uint8`, порядок каналів **BGR** (як в OpenCV).
- Фізична матриця: рядки по 15/16/15/16/15 гексагонів (77 світлодіодів). У 15-довгих рядках стовпець `0` grid не мапиться на реальний піксель — використовуйте індекси `1..15` для точної відповідності фізичному розташуванню, якщо це важливо (напр. геометричні візерунки). Для суцільної заливки чи текстового рендеру (як у `clock.py`) це зазвичай неважливо.
- `render()` отримує вже виділений `grid` (нулі) і повертає його (або новий масив тієї ж форми).

## Pull vs Push модулі

**Pull (`owns_thread = False`)** — типовий випадок. Ядро у своєму єдиному відеопотоці (`_video_processing`, ~30 fps) саме викликає `render(grid, ctx)` і надсилає результат на пристрій. Приклади: `clock.py`, `softbox.py`, `screen.py`.

**Push (`owns_thread = True`)** — модуль сам вирішує, коли готовий новий кадр (напр. джерело з непередбачуваним таймінгом — мережевий міст, BLE-джерело). Стартуйте власний потік у `on_activate()`, зупиняйте в `on_deactivate()`, а всередині потоку штовхайте кадри через `ctx.request_redraw(grid)`. Метод `render()` можна не реалізовувати — ядро його не викликає, поки `owns_thread = True`.

```python
class MyPushModule(HexModuleBase):
    key = "my_push"
    label = "PUSH_DEMO"
    owns_thread = True

    def on_activate(self, ctx):
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(ctx,), daemon=True)
        self._thread.start()

    def on_deactivate(self, ctx):
        self._running = False

    def _loop(self, ctx):
        while self._running:
            grid = np.zeros((5, 16, 3), dtype=np.uint8)
            # ... заповнити grid ...
            ctx.request_redraw(grid)
            time.sleep(0.1)
```

## Збереження стану

Кожен модуль має власний неймспейс — `CONF["modules"][self.key]` — ядро його вміст не інтерпретує. Стандартний патерн (див. `softbox.py`, `gif_player.py`):

```python
DEFAULTS = {"brightness": 128.0}

def __init__(self):
    self._pending_state = dict(self.DEFAULTS)
    self.brightness = None   # SliderWidget — None, поки build_ui() не викликано

def build_ui(self, parent, ctx):
    ...
    self.brightness = make_slider(frame, ..., start_v=self._pending_state["brightness"])
    ...

def get_state(self) -> dict:
    if self.brightness is None:
        return dict(self._pending_state)   # панель ще не побудована
    return {"brightness": self.brightness.var.get()}

def set_state(self, state: dict) -> None:
    merged = {**self.DEFAULTS, **(state or {})}
    self._pending_state = merged
    if self.brightness is not None:          # панель вже існує — застосувати одразу
        self.brightness.var.set(merged["brightness"])
```

`set_state()` викликається ядром **до** `build_ui()` при старті застосунку — тому обов'язково кешуйте вхідні дані в `_pending_state` і перевіряйте `is not None` перед прямим зверненням до віджетів.

Автозбереження всіх модулів відбувається з дебаунсом 600 мс при будь-якій зміні (перемикання режиму, рух слайдера тощо) — власноруч викликати збереження не потрібно.

## Спільні UI-компоненти

`core/widgets.py` — не чіпається ядром напряму, лише допоміжна бібліотека для модулів.

### `make_slider(...)`

```python
from core.widgets import make_slider, SliderWidget

self.brightness: SliderWidget = make_slider(
    frame, ctx.theme, ctx.font_name, "BRIGHTNESS",
    min_v=0, max_v=255, start_v=128.0, decimals=0,
)
```

Повертає `SliderWidget(var, row, slider, label, value_lbl, decimals)`. Поточне значення — `self.brightness.var.get()`. Для перефарбовування при зміні теми викликайте `self.brightness.restyle(ctx.theme)` в `on_theme_change()`.

### `FileLibraryPanel`

Готовий блок "кнопка вибору папки + прокручуваний список файлів заданого розширення" — використовується в `gif_player.py` (`.gif`) і `scripts.py` (`.py`). Не пишіть свій сканер папки заново, якщо модуль працює з файловою бібліотекою:

```python
from core.widgets import FileLibraryPanel

self.library = FileLibraryPanel(
    frame, ctx,
    extension=".gif",
    on_select=self._activate_from_library,      # callable(path, ctx)
    choose_title="Оберіть папку з GIF-файлами",
    empty_no_folder_text="Оберіть папку, щоб побачити GIF-файли",
    empty_no_files_text="У папці немає .gif файлів",
    collapsible=False,   # True — компактний рядок із розгортанням (коли під
                          # списком ще є власний UI модуля, який інакше
                          # список перекриє — див. scripts.py)
)
self.library.frame.pack(fill="x")
```

Модуль сам відповідає за збереження `self.library.folder` / `self.library.active_path` у своєму `get_state()`.

### `GlowLabel` (`core/theme.py`)

Текстова мітка з ефектом світіння у фірмовому стилі. Використовується всередині `make_slider()`; можна застосовувати і напряму для заголовків панелі.

## Перебивання екрана (interrupt_display)

Будь-який модуль (типово — той, що працює у фоні через `on_engine_ready`, напр. системні нотифікації) може на короткий час показати свій кадр **поверх** активного режиму, не перемикаючи меню:

```python
def on_engine_ready(self, ctx: ModuleContext) -> None:
    self._ctx = ctx
    threading.Thread(target=self._watch_events, daemon=True).start()

def _watch_events(self):
    while True:
        event = wait_for_something()
        grid = build_notification_grid(event)
        self._ctx.interrupt_display(grid, duration=3.0)   # 3 секунди поверх усього
```

Потокобезпечно. Після завершення `duration` ядро саме повертається до звичайного рендеру активного режиму.

## Приклад: повний pull-модуль зі слайдерами

Дивіться `modules/softbox.py` у репозиторії — компактний повний приклад: три слайдери, `get_state()`/`set_state()`, `on_theme_change()`, суцільна заливка кольором з корекцією яскравості/температури/відтінку. Гарна відправна точка для копіювання.

Для прикладу з файловою бібліотекою — `modules/gif_player.py`. Для прикладу найскладнішого модуля (mss-захоплення екрана, слайдери з блокуванням співвідношення сторін, 6 слотів пресетів) — `modules/screen.py`.

## Контрольний список перед PR

- [ ] `key` унікальний і не змінюється між релізами (люди втратять збережений стан).
- [ ] `build_ui()` лише створює віджети, не блокує (жодних довгих операцій — файлова I/O, мережа тощо, робіть у фоновому потоці).
- [ ] `render()` не кидає необроблених винятків — ядро ловить `Exception` і пише в лог, але сам режим при цьому просто "зависає" на останньому кадрі.
- [ ] `get_state()`/`set_state()` симетричні й толерантні до відсутніх/зіпсованих ключів (`{**DEFAULTS, **(state or {})}`).
- [ ] Якщо модуль push (`owns_thread=True`) — потік коректно зупиняється в `on_deactivate()` (без "зомбі"-потоків, що продовжують штовхати кадри після виходу з режиму).
- [ ] Немає прямих звернень до внутрішніх атрибутів `MatrixApp` — лише через `ModuleContext`.
