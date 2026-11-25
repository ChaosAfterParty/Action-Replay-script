"""Recording, editing, saving and replaying macros for Mimic."""

import ctypes
import json
import sys
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from pynput import keyboard, mouse


# ==========================================================================
# Steps
# ==========================================================================

MOUSE_KINDS = ("move", "mouse_down", "mouse_up", "scroll")
KEY_KINDS = ("key_down", "key_up")
OTHER_KINDS = ("wait", "text")
KINDS = MOUSE_KINDS + KEY_KINDS + OTHER_KINDS

BUTTONS = ("left", "right", "middle", "x1", "x2")


@dataclass(frozen=True)
class Step:
    """One action. ``delay`` is the pause, in seconds, before the action runs."""

    kind: str
    delay: float = 0.0
    x: int | None = None
    y: int | None = None
    button: str | None = None
    dx: int = 0
    dy: int = 0
    key: str | None = None
    vk: int | None = None
    text: str | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"Unknown step kind: {self.kind}")
        if self.delay < 0:
            raise ValueError("delay must not be negative")
        if self.kind in MOUSE_KINDS and (self.x is None or self.y is None):
            raise ValueError(f"{self.kind} needs x and y")
        if self.kind in ("mouse_down", "mouse_up") and self.button not in BUTTONS:
            raise ValueError(f"Unknown mouse button: {self.button}")
        if self.kind in KEY_KINDS and not self.key and self.vk is None:
            raise ValueError(f"{self.kind} needs a key")
        if self.kind == "text" and not self.text:
            raise ValueError("text step needs text")

    def with_delay(self, delay: float) -> "Step":
        return replace(self, delay=max(0.0, round(delay, 4)))

    def to_dict(self) -> dict:
        """Compact form for files: only the fields this kind uses."""
        data = {k: v for k, v in asdict(self).items() if v is not None}
        if not data.get("dx"):
            data.pop("dx", None)
        if not data.get("dy"):
            data.pop("dy", None)
        data["delay"] = round(self.delay, 4)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)


# Special keys that pynput knows by name, written as <name> in files and in the editor.
def key_label(step: Step) -> str:
    key = step.key or ""
    if key.startswith("<") and key.endswith(">") and len(key) > 2:
        return key[1:-1].replace("_", " ").title()
    if len(key) == 1 and key.isprintable():
        return key.upper() if key.isalpha() else key
    if step.vk is not None:
        if 0x30 <= step.vk <= 0x5A:
            return chr(step.vk)
        return f"VK {step.vk}"
    return repr(key)


ACTION_NAMES = {
    "move": "Mouse move",
    "mouse_down": "Mouse down",
    "mouse_up": "Mouse up",
    "scroll": "Scroll",
    "key_down": "Key down",
    "key_up": "Key up",
    "wait": "Wait",
    "text": "Type text",
}


def describe(step: Step) -> tuple[str, str]:
    """(action, details) as shown in the step list."""
    action = ACTION_NAMES[step.kind]
    if step.kind == "move":
        details = f"to ({step.x}, {step.y})"
    elif step.kind in ("mouse_down", "mouse_up"):
        details = f"{step.button} at ({step.x}, {step.y})"
    elif step.kind == "scroll":
        direction = "down" if step.dy < 0 else "up" if step.dy > 0 else "sideways"
        details = f"{direction} {abs(step.dy) or abs(step.dx)} at ({step.x}, {step.y})"
    elif step.kind in KEY_KINDS:
        details = key_label(step)
    elif step.kind == "text":
        text = step.text or ""
        details = f'"{text[:40]}{"…" if len(text) > 40 else ""}"'
    else:
        details = "pause only"
    return action, details


def total_duration(steps: list[Step]) -> float:
    return sum(step.delay for step in steps)


def format_duration(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{rest:04.1f}"


# ==========================================================================
# Keys
# ==========================================================================

SPECIAL_NAMES = frozenset(key.name for key in keyboard.Key)

ALIASES = {
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "control": "ctrl",
    "ctrl_left": "ctrl_l",
    "ctrl_right": "ctrl_r",
    "win": "cmd",
    "windows": "cmd",
    "pgup": "page_up",
    "pgdn": "page_down",
    "spacebar": "space",
}


def key_from_pynput(key) -> tuple[str | None, int | None]:
    """pynput key from a listener → (key text, virtual-key code)."""
    if isinstance(key, keyboard.Key):
        vk = getattr(key.value, "vk", None)
        return f"<{key.name}>", vk
    char = getattr(key, "char", None)
    vk = getattr(key, "vk", None)
    if char is not None and not char.isprintable():
        char = None
    if char is None and vk is None:
        return None, None
    return char, vk


def key_to_pynput(key: str | None, vk: int | None):
    """Stored key → something ``keyboard.Controller.press`` accepts."""
    if key and key.startswith("<") and key.endswith(">") and len(key) > 2:
        name = key[1:-1]
        if name in SPECIAL_NAMES:
            return keyboard.Key[name]
        raise ValueError(f"Unknown special key: {key}")
    if vk is not None:
        return keyboard.KeyCode.from_vk(vk)
    if key and len(key) == 1:
        return keyboard.KeyCode.from_char(key)
    raise ValueError(f"Cannot replay key: {key!r}")


def parse_user_key(text: str) -> str:
    """What the user typed in the editor (``a``, ``Enter``, ``F5``) → stored key text."""
    raw = text.strip()
    if not raw:
        raise ValueError("Type a key, for example a, 7, enter, esc, shift or f5.")
    if len(raw) == 1:
        return raw
    name = raw.strip("<>").strip().lower().replace(" ", "_")
    name = ALIASES.get(name, name)
    if name in SPECIAL_NAMES:
        return f"<{name}>"
    raise ValueError(
        f'"{raw}" is not a key Mimic knows. Use one character, or a name such as '
        "enter, esc, tab, space, backspace, shift, ctrl, alt, up, f5."
    )


# ==========================================================================
# Files
# ==========================================================================

FILE_VERSION = 1
FILE_SUFFIX = ".mimic"
DEFAULT_FOLDER = Path.home() / "Documents" / "Mimic"


class MacroFileError(Exception):
    """The file is missing, unreadable or not a Mimic recording."""


def save_macro(path: Path, steps: list[Step]) -> Path:
    path = Path(path)
    if path.suffix.lower() != FILE_SUFFIX:
        path = path.with_name(path.name + FILE_SUFFIX)
    payload = {
        "app": "Mimic",
        "version": FILE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "steps": [step.to_dict() for step in steps],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    temporary.replace(path)
    return path


def load_macro(path: Path) -> list[Step]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise MacroFileError(f"File not found: {path}") from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MacroFileError(f"Could not read {Path(path).name}: {error}") from error

    if not isinstance(payload, dict) or payload.get("app") != "Mimic":
        raise MacroFileError(f"{Path(path).name} is not a Mimic recording.")
    if payload.get("version") != FILE_VERSION:
        raise MacroFileError(
            f"{Path(path).name} uses file version {payload.get('version')}; "
            f"this Mimic reads version {FILE_VERSION}."
        )
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raise MacroFileError(f"{Path(path).name} has no step list.")

    steps = []
    for number, raw in enumerate(raw_steps, start=1):
        try:
            steps.append(Step.from_dict(raw))
        except (TypeError, ValueError) as error:
            raise MacroFileError(f"Step {number} is invalid: {error}") from error
    return steps


# ==========================================================================
# Editing
# ==========================================================================

class StepList:
    """The steps of the open recording. Every change can be undone."""

    HISTORY_LIMIT = 200

    def __init__(self, steps: Iterable[Step] = ()):
        self.steps: list[Step] = list(steps)
        self._undo: list[list[Step]] = []
        self._redo: list[list[Step]] = []
        self.dirty = False

    # ----- history -------------------------------------------------------
    def _commit(self, new_steps: list[Step]) -> None:
        self._undo.append(self.steps)
        del self._undo[: -self.HISTORY_LIMIT]
        self._redo.clear()
        self.steps = new_steps
        self.dirty = True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.steps)
        self.steps = self._undo.pop()
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.steps)
        self.steps = self._redo.pop()
        self.dirty = True
        return True

    def load(self, steps: Iterable[Step]) -> None:
        """Replace everything (new recording or opened file); clears history."""
        self.steps = list(steps)
        self._undo.clear()
        self._redo.clear()
        self.dirty = False

    def mark_saved(self) -> None:
        self.dirty = False

    # ----- changes -------------------------------------------------------
    def _valid(self, indices: Iterable[int]) -> list[int]:
        return sorted({i for i in indices if 0 <= i < len(self.steps)})

    def delete(self, indices: Iterable[int]) -> None:
        chosen = set(self._valid(indices))
        if chosen:
            self._commit([s for i, s in enumerate(self.steps) if i not in chosen])

    def move(self, indices: Iterable[int], offset: int) -> list[int]:
        """Move the selected steps up (-1) or down (+1). Returns their new positions."""
        chosen = self._valid(indices)
        if not chosen or offset not in (-1, 1):
            return chosen
        if (offset < 0 and chosen[0] == 0) or (offset > 0 and chosen[-1] == len(self.steps) - 1):
            return chosen
        steps = list(self.steps)
        order = chosen if offset < 0 else list(reversed(chosen))
        for i in order:
            steps[i], steps[i + offset] = steps[i + offset], steps[i]
        self._commit(steps)
        return [i + offset for i in chosen]

    def duplicate(self, indices: Iterable[int]) -> list[int]:
        """Copy the selected steps right after the last selected one. Returns the copies."""
        chosen = self._valid(indices)
        if not chosen:
            return []
        insert_at = chosen[-1] + 1
        copies = [self.steps[i] for i in chosen]
        self._commit(self.steps[:insert_at] + copies + self.steps[insert_at:])
        return list(range(insert_at, insert_at + len(copies)))

    def insert(self, index: int, new_steps: Iterable[Step]) -> list[int]:
        new = list(new_steps)
        index = max(0, min(index, len(self.steps)))
        self._commit(self.steps[:index] + new + self.steps[index:])
        return list(range(index, index + len(new)))

    def replace(self, index: int, step: Step) -> None:
        if 0 <= index < len(self.steps) and self.steps[index] != step:
            steps = list(self.steps)
            steps[index] = step
            self._commit(steps)

    def set_delay(self, indices: Iterable[int], seconds: float) -> None:
        chosen = self._valid(indices)
        if chosen:
            steps = list(self.steps)
            for i in chosen:
                steps[i] = steps[i].with_delay(seconds)
            self._commit(steps)

    def scale_delay(self, indices: Iterable[int], factor: float) -> None:
        if factor < 0:
            raise ValueError("factor must not be negative")
        chosen = self._valid(indices)
        if chosen:
            steps = list(self.steps)
            for i in chosen:
                steps[i] = steps[i].with_delay(steps[i].delay * factor)
            self._commit(steps)

    def remove_mouse_moves(self) -> int:
        """Drop every mouse move but keep the timing: each removed move's delay
        is added to the next kept step. Clicks still go to the right place,
        because they carry their own position. Returns how many were removed."""
        kept: list[Step] = []
        carried = 0.0
        removed = 0
        for step in self.steps:
            if step.kind == "move":
                carried += step.delay
                removed += 1
                continue
            kept.append(step.with_delay(step.delay + carried) if carried else step)
            carried = 0.0
        if removed:
            self._commit(kept)
        return removed


# ==========================================================================
# Recording
# ==========================================================================

@dataclass
class RecordOptions:
    mouse_moves: bool = True
    mouse_buttons: bool = True  # clicks and scrolling
    keyboard: bool = True
    min_move_interval: float = 0.015  # seconds; drops extra moves to keep files small


class Recorder:
    """Collects steps while it runs.

    Events that a program injected (for example
    Mimic's own playback) are ignored, as are the app's hotkeys and releases
    of keys or buttons that were already down before recording started.
    """

    def __init__(
        self,
        options: RecordOptions | None = None,
        ignored_keys: frozenset[str] = frozenset(),
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.options = options or RecordOptions()
        self.ignored_keys = ignored_keys
        self.clock = clock
        self.steps: list[Step] = []
        self._lock = threading.Lock()
        self._last_time = 0.0
        self._last_move_time = float("-inf")
        self._held_keys: set[tuple[str | None, int | None]] = set()
        self._held_buttons: set[str] = set()
        self._listeners: list = []
        self.started_at = 0.0
        self.running = False

    # ----- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self.steps = []
        self._held_keys.clear()
        self._held_buttons.clear()
        self._last_time = self.started_at = self.clock()
        self._last_move_time = float("-inf")
        self.running = True
        self._listeners = [
            mouse.Listener(on_move=self.on_move, on_click=self.on_click, on_scroll=self.on_scroll),
            keyboard.Listener(on_press=self.on_press, on_release=self.on_release),
        ]
        for listener in self._listeners:
            listener.daemon = True
            listener.start()

    def stop(self) -> list[Step]:
        self.running = False
        for listener in self._listeners:
            listener.stop()
        self._listeners = []
        with self._lock:
            return list(self.steps)

    # ----- collecting ----------------------------------------------------
    def _add(self, kind: str, **fields) -> None:
        with self._lock:
            now = self.clock()
            self.steps.append(Step(kind=kind, delay=round(now - self._last_time, 4), **fields))
            self._last_time = now

    @property
    def elapsed(self) -> float:
        return self.clock() - self.started_at if self.running else 0.0

    @property
    def count(self) -> int:
        return len(self.steps)

    def on_move(self, x, y, injected=False):
        if not self.running or injected or not self.options.mouse_moves:
            return
        now = self.clock()
        if now - self._last_move_time < self.options.min_move_interval:
            return
        self._last_move_time = now
        self._add("move", x=int(x), y=int(y))

    def on_click(self, x, y, button, pressed, injected=False):
        if not self.running or injected or not self.options.mouse_buttons:
            return
        name = getattr(button, "name", str(button))
        if name not in ("left", "right", "middle", "x1", "x2"):
            return
        if pressed:
            self._held_buttons.add(name)
        elif name in self._held_buttons:
            self._held_buttons.discard(name)
        else:
            return  # released a button that was down before recording started
        self._add("mouse_down" if pressed else "mouse_up", x=int(x), y=int(y), button=name)

    def on_scroll(self, x, y, dx, dy, injected=False):
        if not self.running or injected or not self.options.mouse_buttons:
            return
        self._add("scroll", x=int(x), y=int(y), dx=int(dx), dy=int(dy))

    def _key(self, key):
        text, vk = key_from_pynput(key)
        if text is None and vk is None:
            return None
        if text in self.ignored_keys:
            return None
        return text, vk

    def on_press(self, key, injected=False):
        if not self.running or injected or not self.options.keyboard:
            return
        identity = self._key(key)
        if identity is None:
            return
        self._held_keys.add(identity[1] if identity[1] is not None else identity[0])
        self._add("key_down", key=identity[0], vk=identity[1])

    def on_release(self, key, injected=False):
        if not self.running or injected or not self.options.keyboard:
            return
        identity = self._key(key)
        if identity is None:
            return
        marker = identity[1] if identity[1] is not None else identity[0]
        if marker not in self._held_keys:
            return  # released a key that was down before recording started
        self._held_keys.discard(marker)
        self._add("key_up", key=identity[0], vk=identity[1])


def trim_trailing_inside(steps: list[Step], box: tuple[int, int, int, int]) -> list[Step]:
    """Remove the mouse steps at the end that happened inside ``box``
    (left, top, right, bottom): the move to Mimic's Stop button and its click."""
    left, top, right, bottom = box
    end = len(steps)
    while end > 0:
        step = steps[end - 1]
        if step.x is None or not (left <= step.x <= right and top <= step.y <= bottom):
            break
        end -= 1
    return steps[:end]


# ==========================================================================
# Playback
# ==========================================================================

@dataclass
class PlayOptions:
    speed: float = 1.0  # 2.0 = twice as fast
    repeats: int = 1  # 0 = until stopped
    pause_between: float = 0.0  # seconds between two runs


@contextmanager
def precise_timer():
    """Ask Windows for 1 ms timer precision while playing, so short delays are kept."""
    winmm = ctypes.WinDLL("winmm") if sys.platform == "win32" else None
    if winmm:
        winmm.timeBeginPeriod(1)
    try:
        yield
    finally:
        if winmm:
            winmm.timeEndPeriod(1)


class Player:
    """Runs steps on schedule. Delays are measured from the start of each run,
    so small timing errors do not add up over a long recording."""

    def __init__(
        self,
        mouse_controller=None,
        keyboard_controller=None,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.mouse = mouse_controller or mouse.Controller()
        self.keyboard = keyboard_controller or keyboard.Controller()
        self.clock = clock
        self._held_keys: list = []
        self._held_buttons: list = []

    def play(
        self,
        steps: list[Step],
        options: PlayOptions,
        stop_event: threading.Event,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Play ``steps``. Returns True if every run finished, False if stopped.
        ``on_progress(step_index, run_number)`` is called before each step."""
        if options.speed <= 0:
            raise ValueError("speed must be positive")
        run = 0
        try:
            while options.repeats == 0 or run < options.repeats:
                run += 1
                if run > 1 and options.pause_between > 0:
                    if stop_event.wait(options.pause_between):
                        return False
                if not self._run_once(steps, options.speed, stop_event, run, on_progress):
                    return False
            return True
        finally:
            self.release_all()

    def _run_once(self, steps, speed, stop_event, run, on_progress) -> bool:
        start = self.clock()
        due = 0.0
        for index, step in enumerate(steps):
            due += step.delay / speed
            remaining = start + due - self.clock()
            if remaining > 0.001:
                if stop_event.wait(remaining):
                    return False
            elif stop_event.is_set():
                return False
            if on_progress:
                on_progress(index, run)
            self.perform(step)
        return True

    def perform(self, step: Step) -> None:
        kind = step.kind
        if kind in ("move", "mouse_down", "mouse_up", "scroll"):
            self.mouse.position = (step.x, step.y)
        if kind == "mouse_down":
            button = mouse.Button[step.button]
            self.mouse.press(button)
            self._held_buttons.append(button)
        elif kind == "mouse_up":
            button = mouse.Button[step.button]
            self.mouse.release(button)
            if button in self._held_buttons:
                self._held_buttons.remove(button)
        elif kind == "scroll":
            self.mouse.scroll(step.dx, step.dy)
        elif kind == "key_down":
            key = key_to_pynput(step.key, step.vk)
            self.keyboard.press(key)
            self._held_keys.append(key)
        elif kind == "key_up":
            key = key_to_pynput(step.key, step.vk)
            self.keyboard.release(key)
            if key in self._held_keys:
                self._held_keys.remove(key)
        elif kind == "text":
            self.keyboard.type(step.text)

    def release_all(self) -> None:
        """Let go of anything still pressed, so a stop never leaves Shift or a mouse button down."""
        for key in reversed(self._held_keys):
            try:
                self.keyboard.release(key)
            except Exception:
                pass
        for button in reversed(self._held_buttons):
            try:
                self.mouse.release(button)
            except Exception:
                pass
        self._held_keys.clear()
        self._held_buttons.clear()
