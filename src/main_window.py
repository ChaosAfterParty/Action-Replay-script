"""CustomTkinter widgets, window chrome, dialogs and presentation helpers for Mimic.

The look follows macOS in dark mode: a borderless window with its own title bar
(red / yellow / green "traffic light" buttons), a sidebar, grey push buttons and
blue accents.
"""

import ctypes
import sys
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

import customtkinter as ctk
from PIL import Image, ImageDraw
from pynput import mouse

from macro_engine import describe, format_duration, total_duration


# ----- colours and fonts (macOS dark mode) ------------------------------------

WINDOW = "#1e1e1e"      # content area
BG = WINDOW
TOOLBAR = "#2c2c2e"     # title bar
SIDEBAR = "#252527"
CARD = "#2f2f31"
TABLE = "#232325"
STRIPE = "#29292b"
FIELD = "#1c1c1e"
OUTLINE = "#48484c"     # 1 px window outline
SEPARATOR = "#111112"
BORDER = "#3a3a3c"
FAINT = "#48484a"
MUTED = "#8e8e93"
SOFT = "#aeaeb2"
TEXT = "#e5e5ea"
BRIGHT = "#ffffff"
LOG_BG = "#19191a"

BUTTON = "#3a3a3c"
BUTTON_HOVER = "#4a4a4d"
ACCENT = "#0a84ff"
ACCENT_HOVER = "#3d9bff"
SELECTION = "#0a5fd1"
GREEN = "#30d158"
RED = "#ff453a"
RED_HOVER = "#ff6259"
AMBER = "#ff9f0a"

LIGHT_COLOURS = {"close": "#ff5f57", "minimize": "#febc2e", "zoom": "#28c840", "off": "#4d4d50"}

UI_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"

# Mimic draws its own title bars, so CustomTkinter must not restyle the Windows ones.
ctk.CTk._deactivate_windows_window_header_manipulation = True
ctk.CTkToplevel._deactivate_windows_window_header_manipulation = True


def font(size: int = 13, weight: str = "normal", family: str | None = None):
    """``weight`` is normal, semibold, bold or light."""
    if weight in ("semibold", "light") and family is None:
        return ctk.CTkFont(family=f"{UI_FAMILY} {weight.capitalize()}", size=size)
    return ctk.CTkFont(family=family or UI_FAMILY, size=size,
                       weight="bold" if weight == "bold" else "normal")


ICON_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "icons"
_icons: dict = {}


def icon(name: str, colour: str = TEXT, size: int = 16) -> ctk.CTkImage:
    """A Lucide icon (assets/icons) tinted in ``colour``."""
    key = (name, colour, size)
    if key not in _icons:
        mask = Image.open(ICON_DIR / f"{name}.png").getchannel("A")
        image = Image.new("RGBA", mask.size, colour)
        image.putalpha(mask)
        _icons[key] = ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))
    return _icons[key]


def stopwatch(seconds: float) -> str:
    """00:03.60 like the iPhone stopwatch."""
    hundredths = int(max(0.0, seconds) * 100)
    minutes, rest = divmod(hundredths, 6000)
    return f"{minutes:02d}:{rest // 100:02d}.{rest % 100:02d}"


def scaling(widget) -> float:
    try:
        return ctk.ScalingTracker.get_window_scaling(widget.winfo_toplevel())
    except (AttributeError, KeyError):
        return 1.0


# ----- Windows window handling -------------------------------------------------

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetParent.restype = wintypes.HWND
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.IsIconic.argtypes = [wintypes.HWND]

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
else:  # pragma: no cover - Mimic targets Windows
    _user32 = None


class WindowChrome:
    """Moving, zooming, minimising and resizing a window that has no Windows title bar."""

    EDGE = 6
    CURSORS = {"e": "size_we", "w": "size_we", "s": "size_ns", "se": "size_nw_se", "sw": "size_ne_sw"}

    def __init__(self, window, resizable: bool = False, taskbar: bool = False):
        self.window = window
        self.resizable = resizable
        self.zoomed = False
        self.normal_geometry = None
        self._drag = None
        self._resize = None
        self._cursor_widget = None
        window.overrideredirect(True)
        window.after(10, lambda: self._prepare(taskbar))
        if resizable:
            window.bind_all("<Motion>", self._hover_edge, add="+")
            window.bind_all("<ButtonPress-1>", self._start_resize, add="+")
            window.bind_all("<B1-Motion>", self._do_resize, add="+")
            window.bind_all("<ButtonRelease-1>", self._end_resize, add="+")

    def hwnd(self):
        return _user32.GetParent(self.window.winfo_id()) if _user32 else None

    def _prepare(self, taskbar: bool):
        """Keep a taskbar button and Alt+Tab entry; round the corners on Windows 11."""
        if _user32 is None or not self.window.winfo_exists():
            return
        hwnd = self.hwnd()
        try:
            corner = ctypes.c_int(2)  # DWMWA_WINDOW_CORNER_PREFERENCE = round (Windows 11 only)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
        except OSError:
            pass
        if taskbar:
            gwl_exstyle, app_window, tool_window = -20, 0x00040000, 0x00000080
            style = _user32.GetWindowLongW(hwnd, gwl_exstyle)
            _user32.SetWindowLongW(hwnd, gwl_exstyle, (style & ~tool_window) | app_window)
            if self.window.state() == "normal":
                self.window.withdraw()
                self.window.after(10, self.window.deiconify)

    # ----- move ------------------------------------------------------------
    def attach_drag(self, *widgets):
        for widget in widgets:
            widget.bind("<ButtonPress-1>", self._start_drag, add="+")
            widget.bind("<B1-Motion>", self._do_drag, add="+")
            widget.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None), add="+")

    def _start_drag(self, event):
        if self._resize or self._edge_at(event):
            return
        if self.zoomed:  # dragging a zoomed window restores its size under the pointer
            width = int(self.normal_geometry.split("x")[0])
            ratio = (event.x_root - self.window.winfo_x()) / max(1, self.window.winfo_width())
            self.toggle_zoom()
            self.window.wm_geometry(f"+{int(event.x_root - width * ratio)}+{event.y_root - 15}")
            self.window.update_idletasks()
        self._drag = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def _do_drag(self, event):
        if self._drag:
            self.window.wm_geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    # ----- minimise / zoom ---------------------------------------------------
    FADE_STEPS = 6
    FADE_SECONDS = 0.09

    def _fade(self, start: float, end: float):
        """Short opacity fade, so moves between sizes do not show the window being redrawn."""
        for step in range(1, self.FADE_STEPS + 1):
            self.window.attributes("-alpha", start + (end - start) * step / self.FADE_STEPS)
            self.window.update_idletasks()
            time.sleep(self.FADE_SECONDS / self.FADE_STEPS)

    def minimize(self):
        if _user32 is None:
            return
        self._fade(1.0, 0.0)
        _user32.ShowWindow(self.hwnd(), 6)  # SW_MINIMIZE keeps the borderless style
        self.window.after(50, self._wait_for_restore)

    def _wait_for_restore(self):
        """Stay invisible while minimised; fade back in once the taskbar restores the window."""
        if not self.window.winfo_exists():
            return
        if _user32.IsIconic(self.hwnd()):
            self.window.after(50, self._wait_for_restore)
            return
        self.window.update()
        self._fade(0.0, 1.0)

    def toggle_zoom(self):
        if _user32 is None:
            return
        self._fade(1.0, 0.0)
        try:
            if self.zoomed:
                self.window.wm_geometry(self.normal_geometry)
                self.zoomed = False
            else:
                info = _MonitorInfo()
                info.cbSize = ctypes.sizeof(_MonitorInfo)
                _user32.GetMonitorInfoW(_user32.MonitorFromWindow(self.hwnd(), 2), ctypes.byref(info))
                area = info.rcWork
                self.normal_geometry = self.window.wm_geometry()
                self.window.wm_geometry(f"{area.right - area.left}x{area.bottom - area.top}"
                                        f"+{area.left}+{area.top}")
                self.zoomed = True
            self.window.update()  # let every widget redraw at the new size while invisible
        finally:
            self._fade(0.0, 1.0)

    # ----- resize from the left, right and bottom edges ----------------------
    def _mine(self, event) -> bool:
        widget = event.widget
        return isinstance(widget, tk.Misc) and widget.winfo_toplevel() is self.window

    def _edge_at(self, event) -> str:
        if self.zoomed or not self.resizable:
            return ""
        x = event.x_root - self.window.winfo_rootx()
        y = event.y_root - self.window.winfo_rooty()
        width, height = self.window.winfo_width(), self.window.winfo_height()
        edge = "s" if height - self.EDGE <= y <= height else ""
        if 0 <= x <= self.EDGE:
            edge += "w"
        elif width - self.EDGE <= x <= width:
            edge += "e"
        return edge if 0 <= y <= height else ""

    def _hover_edge(self, event):
        if self._resize or not self._mine(event):
            return
        edge = self._edge_at(event)
        if edge:
            if self._cursor_widget is not event.widget:
                self._restore_cursor()
                try:
                    self._cursor_widget = (event.widget, event.widget.cget("cursor"))
                except tk.TclError:
                    return
            event.widget.configure(cursor=self.CURSORS[edge])
        else:
            self._restore_cursor()

    def _restore_cursor(self):
        if self._cursor_widget:
            widget, cursor = self._cursor_widget
            self._cursor_widget = None
            try:
                widget.configure(cursor=cursor)
            except tk.TclError:
                pass

    def _start_resize(self, event):
        if not self._mine(event) or not (edge := self._edge_at(event)):
            return
        self._drag = None
        self._resize = (edge, event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y(),
                        self.window.winfo_width(), self.window.winfo_height())

    def _do_resize(self, event):
        if not self._resize:
            return
        edge, x0, y0, x, y, width, height = self._resize
        min_width, min_height = self.window.wm_minsize()
        dx, dy = event.x_root - x0, event.y_root - y0
        new_x, new_width, new_height = x, width, height
        if "e" in edge:
            new_width = max(min_width, width + dx)
        if "w" in edge:
            new_width = max(min_width, width - dx)
            new_x = x + width - new_width
        if "s" in edge:
            new_height = max(min_height, height + dy)
        self.window.wm_geometry(f"{new_width}x{new_height}+{new_x}+{y}")

    def _end_resize(self, _event):
        if self._resize:
            self._resize = None
            self._restore_cursor()


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _mix(top, bottom, amount: float):
    return tuple(round(t * amount + b * (1 - amount)) for t, b in zip(top, bottom))


def _near_segment(x, y, ax, ay, bx, by, half_width) -> bool:
    vx, vy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy)))
    px, py = ax + t * vx - x, ay + t * vy - y
    return px * px + py * py <= half_width * half_width


def _in_triangle(x, y, a, b, c) -> bool:
    def side(p, q):
        return (x - q[0]) * (p[1] - q[1]) - (p[0] - q[0]) * (y - q[1])

    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _in_glyph(kind: str, x: float, y: float, size: int) -> bool:
    half = max(0.055, 0.6 / size)
    if kind == "close":
        return (_near_segment(x, y, 0.32, 0.32, 0.68, 0.68, half)
                or _near_segment(x, y, 0.68, 0.32, 0.32, 0.68, half))
    if kind == "minimize":
        return _near_segment(x, y, 0.26, 0.5, 0.74, 0.5, half * 1.1)
    if kind == "zoom":
        return (_in_triangle(x, y, (0.29, 0.29), (0.63, 0.29), (0.29, 0.63))
                or _in_triangle(x, y, (0.71, 0.71), (0.37, 0.71), (0.71, 0.37)))
    return False


def light_image(master, size: int, kind: str, background: str, glyph: bool) -> tk.PhotoImage:
    """Draw one anti-aliased traffic light (no extra image library needed)."""
    colour = LIGHT_COLOURS[kind if kind in LIGHT_COLOURS else "off"]
    fill, rim, bg = _rgb(colour), _mix((0, 0, 0), _rgb(colour), 0.18), _rgb(background)
    mark = _mix((0, 0, 0), fill, 0.55)
    samples = 4
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            disc = edge = sign = 0
            for sy in range(samples):
                for sx in range(samples):
                    x = (px + (sx + 0.5) / samples) / size
                    y = (py + (sy + 0.5) / samples) / size
                    distance = (x - 0.5) ** 2 + (y - 0.5) ** 2
                    if distance <= 0.25:
                        disc += 1
                        if distance > (0.5 - 1.0 / size) ** 2:
                            edge += 1
                        elif glyph and _in_glyph(kind, x, y, size):
                            sign += 1
            total = samples * samples
            pixel = bg
            if disc:
                body = _mix(rim, fill, edge / disc) if edge else fill
                body = _mix(mark, body, sign / disc) if sign else body
                pixel = _mix(body, bg, disc / total)
            row.append("#%02x%02x%02x" % pixel)
        rows.append("{" + " ".join(row) + "}")
    image = tk.PhotoImage(master=master, width=size, height=size)
    image.put(" ".join(rows), to=(0, 0))
    return image


class TitleBar(ctk.CTkFrame):
    """macOS-style title bar: traffic lights on the left, the title in the middle."""

    def __init__(self, parent, chrome: WindowChrome, title: str, on_close: Callable,
                 on_minimize: Callable | None = None, on_zoom: Callable | None = None,
                 height: int = 40, fg_color: str = TOOLBAR):
        super().__init__(parent, height=height, corner_radius=0, fg_color=fg_color)
        self.pack_propagate(False)
        self.chrome = chrome
        actions = {"close": on_close, "minimize": on_minimize, "zoom": on_zoom}

        size = max(12, round(13 * scaling(parent)))
        self.lights = tk.Frame(self, bg=fg_color)
        self.lights.place(x=round(14 * scaling(parent)), rely=0.5, anchor="w")
        self.images = {}
        self.buttons = []
        for kind, action in actions.items():
            shown = kind if action else "off"
            plain = light_image(self, size, shown, fg_color, glyph=False)
            marked = light_image(self, size, shown, fg_color, glyph=bool(action))
            label = tk.Label(self.lights, image=plain, bg=fg_color, bd=0, highlightthickness=0)
            label.pack(side="left", padx=(0, round(8 * scaling(parent))))
            label.bind("<Enter>", lambda _e: self._show_marks(True))
            label.bind("<Leave>", lambda _e: self.after(20, self._check_hover))
            if action:
                label.bind("<ButtonRelease-1>", lambda _e, run=action: run())
            self.images[label] = (plain, marked)
            self.buttons.append(label)

        self.title_label = ctk.CTkLabel(self, text=title, font=font(13, "semibold"), text_color=SOFT)
        self.title_label.place(relx=0.5, rely=0.5, anchor="center")
        chrome.attach_drag(self, self.title_label)
        if on_zoom:
            self.bind("<Double-Button-1>", lambda _e: on_zoom())
            self.title_label.bind("<Double-Button-1>", lambda _e: on_zoom())

    def _show_marks(self, marked: bool):
        for label, (plain, with_mark) in self.images.items():
            label.configure(image=with_mark if marked else plain)

    def _check_hover(self):
        if not self.winfo_exists():
            return
        under = self.winfo_containing(*self.winfo_pointerxy())
        if under not in self.buttons:
            self._show_marks(False)

    def set_title(self, text: str):
        self.title_label.configure(text=text)


# ----- dialogs ---------------------------------------------------------------

class MacDialog(ctk.CTkToplevel):
    """A borderless, modal sheet with a macOS title bar."""

    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.withdraw()
        self.parent = parent
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(fg_color=OUTLINE)
        self.chrome = WindowChrome(self)
        frame = ctk.CTkFrame(self, corner_radius=0, fg_color=SIDEBAR)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        TitleBar(frame, self.chrome, title, on_close=self.cancel, height=34).pack(fill="x")
        ctk.CTkFrame(frame, height=1, corner_radius=0, fg_color=SEPARATOR).pack(fill="x")
        self.body = ctk.CTkFrame(frame, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=22, pady=(16, 18))
        self.bind("<Escape>", lambda _e: self.cancel())

    def cancel(self):
        self.destroy()

    def button_row(self, buttons: list[tuple[str, Callable, bool]]):
        """(text, command, primary) from left to right, aligned right like macOS."""
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=(14, 0))
        for text, command, primary in reversed(buttons):
            push_button(row, text, command, width=92, primary=primary).pack(side="right", padx=(8, 0))

    def present(self, focus=None):
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        parent = self.parent
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + max(40, (parent.winfo_height() - height) // 3)
        self.wm_geometry(f"{width}x{height}+{x}+{y}")
        if parent.attributes("-topmost"):
            self.attributes("-topmost", True)  # stay above a pinned main window
        self.deiconify()
        self.after(30, lambda: self._take_focus(focus))

    def _stay_above_parent(self):
        """Borderless windows ignore Tk's transient(), so a click on the main window could cover
        the dialog and leave Mimic looking frozen. Make the main window the dialog's Windows
        owner instead: an owned window is always kept above its owner."""
        if _user32 is None:
            return
        try:
            owner = _user32.GetParent(self.parent.winfo_toplevel().winfo_id())
            _user32.SetWindowLongPtrW(self.chrome.hwnd(), -8, owner or 0)  # GWLP_HWNDPARENT
        except (OSError, tk.TclError):
            pass

    def _take_focus(self, focus):
        if not self.winfo_exists():
            return
        self._stay_above_parent()
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        if focus is not None:
            focus.focus_set()
            try:
                focus.select_range(0, "end")
            except (AttributeError, tk.TclError):
                pass


@dataclass
class Field:
    name: str
    label: str
    value: str = ""
    choices: tuple[str, ...] = ()
    hint: str = ""
    pick_position: bool = False  # adds a "Pick in 3 s" button that fills x / y


class FormDialog(MacDialog):
    """Show labelled inputs; ``validate`` turns the raw text into a result or
    raises ValueError with a message shown in the dialog."""

    def __init__(self, parent, title: str, fields: list[Field], validate: Callable[[dict], object]):
        super().__init__(parent, title)
        self.validate = validate
        self.result = None
        self.inputs: dict[str, ctk.StringVar] = {}

        first_entry = None
        for spec in fields:
            row = ctk.CTkFrame(self.body, fg_color="transparent")
            row.pack(fill="x", pady=5)
            ctk.CTkLabel(row, text=spec.label, width=140, anchor="e", font=font(13),
                         text_color=TEXT).pack(side="left", padx=(0, 12))
            var = ctk.StringVar(value=spec.value)
            self.inputs[spec.name] = var
            if spec.choices:
                widget = ctk.CTkOptionMenu(row, values=list(spec.choices), variable=var, width=200, height=28,
                                           corner_radius=6, fg_color=BUTTON, button_color=BUTTON,
                                           button_hover_color=BUTTON_HOVER, text_color=TEXT, font=font(13),
                                           dropdown_fg_color=CARD, dropdown_hover_color=ACCENT,
                                           dropdown_text_color=TEXT, dropdown_font=font(13))
            else:
                widget = ctk.CTkEntry(row, textvariable=var, width=200, height=28, corner_radius=6,
                                      fg_color=FIELD, border_color=BORDER, border_width=1,
                                      text_color=TEXT, font=font(13))
                first_entry = first_entry or widget
            widget.pack(side="left")
            if spec.pick_position:
                push_button(row, "Pick in 3 s", self.pick_position, width=104, icon_name="pick").pack(side="left", padx=(8, 0))
            if spec.hint:
                ctk.CTkLabel(self.body, text=spec.hint, font=font(11), text_color=MUTED, justify="left",
                             wraplength=360, anchor="w").pack(anchor="w", padx=(152, 0))

        self.error = ctk.CTkLabel(self.body, text="", text_color=RED, font=font(12), wraplength=420,
                                  justify="left", anchor="w")
        self.error.pack(anchor="w", padx=(152, 0), pady=(4, 0))
        self.button_row([("Cancel", self.cancel, False), ("OK", self.submit, True)])

        self.bind("<Return>", lambda _e: self.submit())
        self.present(first_entry)

    def pick_position(self):
        self.error.configure(text="Move the mouse to the spot… 3", text_color=ACCENT)

        def tick(left):
            if not self.winfo_exists():
                return
            if left > 0:
                self.error.configure(text=f"Move the mouse to the spot… {left}")
                self.after(1000, tick, left - 1)
                return
            x, y = mouse.Controller().position
            self.inputs["x"].set(str(int(x)))
            self.inputs["y"].set(str(int(y)))
            self.error.configure(text=f"Picked ({int(x)}, {int(y)}).", text_color=GREEN)

        self.after(1000, tick, 2)

    def submit(self):
        try:
            self.result = self.validate({name: var.get() for name, var in self.inputs.items()})
        except ValueError as error:
            self.error.configure(text=str(error), text_color=RED)
            return
        self.destroy()


class ChoiceDialog(MacDialog):
    """A macOS-style alert: a bold message, a smaller explanation and buttons."""

    def __init__(self, parent, title: str, message: str, detail: str,
                 buttons: list[tuple[str, str]], default: str, cancel: str):
        super().__init__(parent, title)
        self.answer = cancel
        self.cancel_key = cancel
        ctk.CTkLabel(self.body, text=message, font=font(14, "semibold"), text_color=BRIGHT,
                     justify="left", wraplength=380, anchor="w").pack(anchor="w")
        if detail:
            ctk.CTkLabel(self.body, text=detail, font=font(12), text_color=SOFT, justify="left",
                         wraplength=380, anchor="w").pack(anchor="w", pady=(6, 0))
        self.button_row([(text, lambda key=key: self.choose(key), key == default) for key, text in buttons])
        self.bind("<Return>", lambda _e: self.choose(default))
        self.present()

    def choose(self, key: str):
        self.answer = key
        self.destroy()

    def cancel(self):
        self.choose(self.cancel_key)


def ask(parent, title: str, fields: list[Field], validate: Callable[[dict], object]):
    dialog = FormDialog(parent, title, fields, validate)
    parent.wait_window(dialog)
    return dialog.result


def choose(parent, title: str, message: str, detail: str = "",
           buttons: list[tuple[str, str]] = (("ok", "OK"),), default: str = "ok", cancel: str = "ok") -> str:
    dialog = ChoiceDialog(parent, title, message, detail, list(buttons), default, cancel)
    parent.wait_window(dialog)
    return dialog.answer


# ----- value parsing helpers ---------------------------------------------
def parse_ms(text: str, label: str = "Delay") -> float:
    """Milliseconds as typed → seconds."""
    try:
        value = float(text.replace(",", ".").strip())
    except ValueError:
        raise ValueError(f"{label} must be a number of milliseconds, for example 250.") from None
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")
    if value > 3_600_000:
        raise ValueError(f"{label} is limited to one hour (3600000 ms).")
    return value / 1000


def parse_int(text: str, label: str) -> int:
    try:
        return int(float(text.strip()))
    except ValueError:
        raise ValueError(f"{label} must be a whole number.") from None


def ms(seconds: float) -> str:
    value = seconds * 1000
    return str(int(round(value))) if abs(value - round(value)) < 0.05 else f"{value:.1f}"


# ----- main window -----------------------------------------------------------

SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
DEFAULT_SPEED_INDEX = SPEEDS.index(1.0)
KBD = "#3a3a3c"


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def push_button(parent, text, command, width=70, primary=False, icon_name=None, **kwargs):
    """A macOS push button: grey, or blue for the default action, with an optional icon."""
    colour = BRIGHT if primary else TEXT
    options = {
        "fg_color": ACCENT if primary else BUTTON,
        "hover_color": ACCENT_HOVER if primary else BUTTON_HOVER,
        "text_color": colour,
        "text_color_disabled": FAINT,
        "font": font(12),
        "height": 26,
        "corner_radius": 6,
    }
    if icon_name:
        options.update(image=icon(icon_name, colour, 14), compound="left")
    options.update(kwargs)
    button = ctk.CTkButton(parent, text=text, width=width, command=command, **options)
    if icon_name:
        button.icons = (icon(icon_name, colour, 14), icon(icon_name, FAINT, 14))
    return button


def set_enabled(button, enabled: bool):
    """Enable or disable a button and dim its icon with it."""
    button.configure(state="normal" if enabled else "disabled")
    icons = getattr(button, "icons", None)
    if icons:
        button.configure(image=icons[0] if enabled else icons[1])


def kbd(parent, keys: str, fg_color: str = KBD, text_color: str = SOFT, size: int = 10):
    """A keyboard shortcut badge in the shadcn/ui ``Kbd`` style."""
    return ctk.CTkLabel(parent, text=keys, font=font(size, "semibold"), text_color=text_color,
                        fg_color=fg_color, corner_radius=4, height=18, width=24)


def card(parent, **pack):
    frame = ctk.CTkFrame(parent, corner_radius=10, fg_color=CARD, border_width=1, border_color=BORDER)
    frame.pack(**pack)
    return frame


def section_label(parent, text, **pack):
    label = ctk.CTkLabel(parent, text=text, font=font(11, "semibold"), text_color=MUTED, anchor="w")
    label.pack(**({"anchor": "w", "padx": 26, "pady": (0, 4)} | pack))
    return label


def separator(parent, vertical=False, **pack):
    line = ctk.CTkFrame(parent, corner_radius=0, fg_color=SEPARATOR,
                        width=1 if vertical else 10, height=10 if vertical else 1)
    line.pack(**pack)
    return line


_circles: dict = {}


def ring_image(fill: str, background: str, size: int) -> ctk.CTkImage:
    """The iPhone stopwatch button shape: a thin ring, a black gap and a filled disc (anti-aliased)."""
    key = (fill, background, size)
    if key not in _circles:
        big = size * 8
        image = Image.new("RGB", (big, big), background)
        draw = ImageDraw.Draw(image)
        ring = round(big * 0.028)
        draw.ellipse((0, 0, big - 1, big - 1), fill=fill)
        draw.ellipse((ring, ring, big - 1 - ring, big - 1 - ring), fill=background)
        gap = ring * 2
        draw.ellipse((gap, gap, big - 1 - gap, big - 1 - gap), fill=fill)
        image = image.resize((size * 3, size * 3), Image.LANCZOS)
        _circles[key] = ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))
    return _circles[key]


class RoundButton(ctk.CTkFrame):
    """The round, ringed button of the iPhone stopwatch, with an icon, a label and a shortcut badge."""

    STYLES = {  # fill, hover, text, icon
        "grey": ("#333336", "#3e3e42", BRIGHT, RED),
        "green": ("#0a2a12", "#0f3a1a", GREEN, GREEN),
        "red": ("#330e0c", "#461511", RED, RED),
        "off": ("#1a1a1c", "#1a1a1c", "#5a5a5e", "#5a5a5e"),
    }
    SIZE = 86

    def __init__(self, parent, command: Callable, background: str):
        super().__init__(parent, width=self.SIZE, height=self.SIZE, corner_radius=0, fg_color=background)
        self.background = background
        self.command = command
        self.enabled = True
        self.style = "grey"
        self.icon_name = "record"
        self.shape = ctk.CTkLabel(self, text="", width=self.SIZE, height=self.SIZE, fg_color=background)
        self.shape.place(x=0, y=0, relwidth=1, relheight=1)
        self.icon_label = ctk.CTkLabel(self, text="", height=16, width=16)
        self.icon_label.place(relx=0.5, rely=0.28, anchor="center")
        self.text_label = ctk.CTkLabel(self, text="", font=font(13, "semibold"), height=18)
        self.text_label.place(relx=0.5, rely=0.52, anchor="center")
        self.key_label = kbd(self, "", size=9)
        self.key_label.configure(height=16, width=26)
        self.key_label.place(relx=0.5, rely=0.75, anchor="center")
        for widget in (self, self.shape, self.icon_label, self.text_label, self.key_label):
            widget.bind("<ButtonRelease-1>", self._click)
            widget.bind("<Enter>", lambda _e: self._paint(hover=True))
            widget.bind("<Leave>", lambda _e: self._paint(hover=False))
            widget.configure(cursor="hand2")

    def set(self, text: str, icon_name: str, style: str, key: str, enabled: bool = True):
        self.enabled = enabled
        self.style = style if enabled else "off"
        self.icon_name = icon_name
        self.text_label.configure(text=text)
        self.key_label.configure(text=key)
        self._paint(hover=False)

    def _paint(self, hover: bool):
        fill, hovered, text, icon_colour = self.STYLES[self.style]
        colour = hovered if hover and self.enabled else fill
        self.shape.configure(image=ring_image(colour, self.background, self.SIZE))
        self.icon_label.configure(image=icon(self.icon_name, icon_colour, 16), fg_color=colour)
        self.text_label.configure(text_color=text, fg_color=colour)
        self.key_label.configure(fg_color=_hex(_mix(_rgb(text), _rgb(colour), 0.16)), text_color=text,
                                 bg_color=colour)

    def _click(self, _event):
        if self.enabled:
            self.command()


class DropdownMenu(ctk.CTkToplevel):
    """A small pop-up menu below ``anchor``: rows of (icon, text, shortcut, command), None = separator."""

    WIDTH = 230

    def __init__(self, anchor, items):
        window = anchor.winfo_toplevel()
        super().__init__(window)
        self.withdraw()
        self.overrideredirect(True)
        self.configure(fg_color=OUTLINE)
        frame = ctk.CTkFrame(self, corner_radius=0, fg_color=CARD)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        ctk.CTkFrame(frame, height=4, fg_color="transparent").pack()
        for item in items:
            if item is None:
                ctk.CTkFrame(frame, height=1, corner_radius=0, fg_color=BORDER).pack(fill="x", padx=10, pady=4)
            elif item[0] == "title":
                ctk.CTkLabel(frame, text=item[1], font=font(11, "semibold"), text_color=MUTED, anchor="w",
                             height=22).pack(fill="x", padx=15, pady=(0, 1))
            elif item[0] == "check":
                self._check_row(frame, item[1], item[2])
            else:
                self._row(frame, *item)
        ctk.CTkFrame(frame, height=4, fg_color="transparent").pack()

        self.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        self.wm_geometry(f"+{x}+{y}")
        if window.attributes("-topmost"):
            self.attributes("-topmost", True)
        self.deiconify()
        self.lift()
        self.focus_force()
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<FocusOut>", lambda _e: self.after(80, self._close_if_unfocused))
        # Close when the window moves or anything outside the menu is clicked.
        self.window = window
        self.anchor = anchor
        self.window_position = (window.winfo_x(), window.winfo_y())
        self._move_binding = window.bind("<Configure>", self._window_changed, add="+")
        self._click_binding = window.bind_all("<ButtonPress-1>", self._click_anywhere, add="+")

    def _window_changed(self, _event):
        if (self.window.winfo_x(), self.window.winfo_y()) != self.window_position:
            self.close()

    def _click_anywhere(self, event):
        widget = event.widget
        if not isinstance(widget, tk.Misc) or not self.winfo_exists():
            return
        if widget.winfo_toplevel() is self or str(widget).startswith(str(self.anchor)):
            return
        self.close()

    def _row(self, parent, icon_name, text, shortcut, command):
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=5, height=28, width=self.WIDTH)
        row.pack(fill="x", padx=5, pady=1)
        row.pack_propagate(False)
        picture = ctk.CTkLabel(row, text="", image=icon(icon_name, TEXT, 14), width=18)
        picture.pack(side="left", padx=(10, 8))
        label = ctk.CTkLabel(row, text=text, font=font(13), text_color=TEXT)
        label.pack(side="left")
        keys = ctk.CTkLabel(row, text=shortcut, font=font(11), text_color=MUTED)
        keys.pack(side="right", padx=(0, 10))

        def paint(active):
            row.configure(fg_color=ACCENT if active else "transparent")
            picture.configure(image=icon(icon_name, BRIGHT if active else TEXT, 14))
            label.configure(text_color=BRIGHT if active else TEXT)
            keys.configure(text_color=BRIGHT if active else MUTED)

        def run(_event):
            self.close()
            command()

        for widget in (row, picture, label, keys):
            widget.bind("<Enter>", lambda _e: paint(True))
            widget.bind("<Leave>", lambda _e: paint(False))
            widget.bind("<ButtonRelease-1>", run)

    def _check_row(self, parent, text, variable):
        """A setting that ticks on and off; the menu stays open so several can be changed."""
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=5, height=28, width=self.WIDTH)
        row.pack(fill="x", padx=5, pady=1)
        row.pack_propagate(False)
        mark = ctk.CTkLabel(row, text="", width=18)
        mark.pack(side="left", padx=(10, 8))
        label = ctk.CTkLabel(row, text=text, font=font(13), text_color=TEXT)
        label.pack(side="left")
        state = {"active": False}

        def paint(active=None):
            if active is not None:
                state["active"] = active
            colour = BRIGHT if state["active"] else TEXT
            row.configure(fg_color=ACCENT if state["active"] else "transparent")
            mark.configure(image=icon("check", colour, 14) if variable.get() else icon("blank", colour, 14))
            label.configure(text_color=colour)

        def toggle(_event):
            variable.set(not variable.get())
            paint()

        for widget in (row, mark, label):
            widget.bind("<Enter>", lambda _e: paint(True))
            widget.bind("<Leave>", lambda _e: paint(False))
            widget.bind("<ButtonRelease-1>", toggle)
        paint(False)

    def _close_if_unfocused(self):
        if self.winfo_exists() and self.focus_get() is None:
            self.close()

    def close(self):
        if not self.winfo_exists():
            return
        try:
            self.window.unbind("<Configure>", self._move_binding)
            self.window.tk.call("bind", "all", "<ButtonPress-1>",
                                self._without(self.window.tk.call("bind", "all", "<ButtonPress-1>"),
                                              self._click_binding))
        except (AttributeError, tk.TclError):
            pass
        self.destroy()

    @staticmethod
    def _without(script: str, funcid: str) -> str:
        """The 'all' binding script minus the line that calls ``funcid`` (keeps other handlers)."""
        return "\n".join(line for line in str(script).splitlines() if funcid not in line)


def show_centered(window):
    """Map a withdrawn, fully built window in the middle of the screen at its final size."""
    window.update_idletasks()
    # A withdrawn window still reports its old size, so use the size the layout asked for.
    width, height = getattr(window, "final_size", None) or (window.winfo_reqwidth(), window.winfo_reqheight())
    x = max((window.winfo_screenwidth() - width) // 2, 0)
    y = max((window.winfo_screenheight() - height) // 2 - 20, 0)
    window.wm_geometry(f"{width}x{height}+{x}+{y}")
    window.deiconify()
    window.lift()
    window.focus_force()


class MainWindowLayout:
    """Build and update the application's visual interface."""

    LEFT_WIDTH = 450

    def build_ui(self):
        self.root.configure(fg_color=OUTLINE)
        self.chrome = WindowChrome(self.root, resizable=True, taskbar=True)
        frame = ctk.CTkFrame(self.root, corner_radius=0, fg_color=WINDOW)
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        body = ctk.CTkFrame(frame, corner_radius=0, fg_color=WINDOW)
        body.pack(fill="both", expand=True)
        left = ctk.CTkFrame(body, corner_radius=0, fg_color=SIDEBAR, width=self.LEFT_WIDTH)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.sidebar = left
        separator(body, vertical=True, side="left", fill="y")
        right = ctk.CTkFrame(body, corner_radius=0, fg_color=WINDOW)
        right.pack(side="left", fill="both", expand=True)

        self._build_title_bar(left, right)
        self.menu = None
        self._build_header(left)
        self._build_timer(left)
        self._build_settings(left)
        self._build_log(left)
        self._build_editor(right)

        self.update_slider_labels(None)
        self.set_busy_controls(None)
        self.hide_logs()
        self.log_message("System initialized. Ready to record.")

    # ----- title bar -----------------------------------------------------
    def _build_title_bar(self, sidebar, content):
        """No separate bar: the traffic lights sit in the sidebar's top strip and the window
        controls in the content's top strip, so the window reads as one surface (like macOS)."""
        self.title_bar = TitleBar(sidebar, self.chrome, "", on_close=self.close,
                                  on_minimize=self.chrome.minimize, on_zoom=self.chrome.toggle_zoom,
                                  fg_color=SIDEBAR)
        self.title_bar.pack(fill="x")

        strip = ctk.CTkFrame(content, corner_radius=0, fg_color=WINDOW, height=40)
        strip.pack(fill="x")
        strip.pack_propagate(False)
        self.chrome.attach_drag(strip)
        strip.bind("<Double-Button-1>", lambda _e: self.chrome.toggle_zoom())
        right = ctk.CTkFrame(strip, fg_color="transparent")
        right.place(relx=1.0, x=-24, rely=0.5, anchor="e")
        self.status_badge = ctk.CTkFrame(right, fg_color=CARD, corner_radius=11, border_width=1,
                                         border_color=BORDER, height=22, width=104)
        self.status_badge.pack(side="left", padx=(0, 16))
        self.status_badge.pack_propagate(False)
        content = ctk.CTkFrame(self.status_badge, fg_color="transparent")
        content.place(relx=0.5, rely=0.5, anchor="center")
        self.status_dot = ctk.CTkFrame(content, width=8, height=8, corner_radius=4, fg_color=SOFT)
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_text = ctk.CTkLabel(content, text="Ready", font=font(11, "semibold"), text_color=SOFT,
                                        height=16)
        self.status_text.pack(side="left")

        ctk.CTkLabel(right, text="", image=icon("pin", SOFT, 14), width=16).pack(side="left", padx=(0, 6))
        ctk.CTkSwitch(right, text="Keep on Top", variable=self.ontop_var, command=self.toggle_top,
                      font=font(12), text_color=SOFT, **self._switch_style()).pack(side="left")
        kbd(right, "F8").pack(side="left", padx=(2, 0))

    @staticmethod
    def _switch_style():
        return {"progress_color": ACCENT, "button_color": BRIGHT, "button_hover_color": TEXT,
                "fg_color": FAINT, "switch_width": 30, "switch_height": 16}

    # ----- sidebar -------------------------------------------------------
    def _build_header(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(4, 12))
        self.menu_btn = ctk.CTkButton(header, text="", image=icon("menu", TEXT, 20), width=34, height=34,
                                      corner_radius=8, fg_color="transparent", hover_color=BUTTON,
                                      command=self.toggle_menu)
        self.menu_btn.icons = (icon("menu", TEXT, 20), icon("menu", FAINT, 20))
        self.menu_btn.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(header, text="Mimic", font=font(26, "bold"), text_color=BRIGHT).pack(side="left")
        ctk.CTkLabel(header, text="Record · Edit · Replay", font=font(12), text_color=MUTED).pack(
            side="left", padx=(10, 0), pady=(8, 0))
        self.file_buttons = [self.menu_btn]

    def toggle_menu(self):
        if self.menu is not None and self.menu.winfo_exists():
            self.menu.close()
            self.menu = None
            return
        self.menu = DropdownMenu(self.menu_btn, [
            ("new", "New", "Ctrl+N", self.new_recording),
            ("open", "Open…", "Ctrl+O", self.open_file),
            None,
            ("save", "Save", "Ctrl+S", self.save_file),
            ("save_as", "Save As…", "Ctrl+Shift+S", self.save_file_as),
            None,
            ("title", "Capture Settings"),
            ("check", "Record mouse moves", self.rec_moves_var),
            ("check", "Record clicks & scroll", self.rec_buttons_var),
            ("check", "Record keyboard", self.rec_keys_var),
        ])

    def _build_timer(self, parent):
        black = "#000000"
        panel = ctk.CTkFrame(parent, corner_radius=16, fg_color=black, border_width=1, border_color=BORDER)
        panel.pack(fill="x", padx=18, pady=(0, 16))
        self.timer_title = ctk.CTkLabel(panel, text="Recording Length", font=font(11, "semibold"),
                                        text_color=MUTED, fg_color=black)
        self.timer_title.pack(pady=(14, 0))
        ctk.CTkLabel(panel, textvariable=self.timer_var, font=font(60, "light"), text_color=BRIGHT,
                     fg_color=black).pack(pady=(0, 4))

        row = ctk.CTkFrame(panel, fg_color=black)
        row.pack(fill="x", padx=18, pady=(0, 18))
        self.record_btn = RoundButton(row, self.toggle_recording, black)
        self.record_btn.pack(side="left")
        self.play_btn = RoundButton(row, self.toggle_playback, black)
        self.play_btn.pack(side="right")
        ctk.CTkLabel(row, textvariable=self.timer_detail_var, font=font(12), text_color=SOFT,
                     fg_color=black, wraplength=170).pack(expand=True)

    def _slider_row(self, panel, title, value_label_attr, pady=(0, 2)):
        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=pady)
        ctk.CTkLabel(row, text=title, font=font(13), text_color=TEXT).pack(side="left")
        label = ctk.CTkLabel(row, text="", font=font(13, "semibold"), text_color=SOFT)
        label.pack(side="right")
        setattr(self, value_label_attr, label)
        return row

    def _build_settings(self, parent):
        section_label(parent, "Playback")
        panel = card(parent, fill="x", padx=18, pady=(0, 14))
        slider = {
            "button_color": BRIGHT, "button_hover_color": TEXT, "progress_color": ACCENT,
            "fg_color": FAINT, "height": 18, "border_width": 6, "button_length": 0,
            "command": self.update_slider_labels,
        }

        self._slider_row(panel, "Speed", "speed_label", pady=(12, 2))
        self.speed_scale = ctk.CTkSlider(panel, from_=0, to=len(SPEEDS) - 1, number_of_steps=len(SPEEDS) - 1,
                                         variable=self.speed_index_var, **slider)
        self.speed_scale.pack(fill="x", padx=12, pady=(0, 10))

        repeat_row = self._slider_row(panel, "Repeat", "repeat_label")
        ctk.CTkSwitch(repeat_row, text="Forever", variable=self.forever_var, command=self.update_slider_labels,
                      font=font(12), text_color=SOFT, width=40, **self._switch_style()).pack(
            side="right", padx=(0, 14))
        self.repeat_scale = ctk.CTkSlider(panel, from_=1, to=100, number_of_steps=99,
                                          variable=self.repeat_var, **slider)
        self.repeat_scale.pack(fill="x", padx=12, pady=(0, 10))

        self._slider_row(panel, "Pause Between Repeats", "pause_label")
        ctk.CTkSlider(panel, from_=0, to=10, number_of_steps=20, variable=self.pause_var, **slider).pack(
            fill="x", padx=12, pady=(0, 14))

    def _build_log(self, parent):
        bottom = ctk.CTkFrame(parent, fg_color="transparent")
        bottom.pack(fill="x", padx=18, pady=(4, 4))
        self.hide_log_btn = push_button(bottom, "Hide Log", self.hide_logs, width=90, height=24,
                                        font=font(11), icon_name="log")
        self.show_log_btn = push_button(bottom, "Show Log", self.show_logs, width=90, height=24,
                                        font=font(11), icon_name="log")

        self.log_text = ctk.CTkTextbox(parent, corner_radius=8, fg_color=LOG_BG, border_width=1,
                                       border_color=BORDER, text_color="#c7c7cc",
                                       font=font(11, family=MONO_FAMILY))
        self.log_text.configure(state="disabled")

    # ----- content: the step editor -------------------------------------
    def _build_editor(self, parent):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(4, 12))
        ctk.CTkLabel(head, text="Steps", font=font(20, "bold"), text_color=BRIGHT).pack(side="left")
        self.file_label = ctk.CTkLabel(head, text="Untitled", font=font(12), text_color=MUTED)
        self.file_label.pack(side="left", padx=(12, 0), pady=(5, 0))
        self.redo_btn = push_button(head, "Redo", self.redo, width=68, icon_name="redo")
        self.undo_btn = push_button(head, "Undo", self.undo, width=68, icon_name="undo")
        self.redo_btn.pack(side="right")
        self.undo_btn.pack(side="right", padx=(0, 6))

        inserts = ctk.CTkFrame(parent, fg_color="transparent")
        inserts.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkLabel(inserts, text="Insert", font=font(12), text_color=MUTED, width=52, anchor="w").pack(
            side="left", padx=(0, 8))
        self.insert_buttons = [
            push_button(inserts, "Wait…", self.insert_wait, width=74, icon_name="wait"),
            push_button(inserts, "Key…", self.insert_key, width=66, icon_name="key"),
            push_button(inserts, "Text…", self.insert_text, width=70, icon_name="text"),
            push_button(inserts, "Click…", self.insert_click, width=74, icon_name="click"),
        ]
        for button in self.insert_buttons:
            button.pack(side="left", padx=(0, 6))
        self.strip_btn = push_button(inserts, "Remove Mouse Moves", self.remove_moves, width=160,
                                     icon_name="strip")
        self.strip_btn.pack(side="right")

        tools = ctk.CTkFrame(parent, fg_color="transparent")
        tools.pack(fill="x", padx=24, pady=(0, 12))
        ctk.CTkLabel(tools, text="Actions", font=font(12), text_color=MUTED, width=52, anchor="w").pack(
            side="left", padx=(0, 8))
        self.edit_buttons = [
            push_button(tools, "Edit", self.edit_selected, width=64, icon_name="edit"),
            push_button(tools, "Delete", self.delete_selected, width=74, hover_color=RED, icon_name="delete"),
            push_button(tools, "Up", lambda: self.move_selected(-1), width=56, icon_name="up"),
            push_button(tools, "Down", lambda: self.move_selected(1), width=68, icon_name="down"),
            push_button(tools, "Duplicate", self.duplicate_selected, width=92, icon_name="duplicate"),
            push_button(tools, "Set Delay…", self.set_delay_selected, width=98, icon_name="delay"),
        ]
        for button in self.edit_buttons:
            button.pack(side="left", padx=(0, 6))

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Mimic.Treeview", background=TABLE, fieldbackground=TABLE, foreground=TEXT,
                        bordercolor=TABLE, borderwidth=0, rowheight=round(26 * scaling(self.root)),
                        font=(UI_FAMILY, 10))
        style.map("Mimic.Treeview", background=[("selected", SELECTION)], foreground=[("selected", BRIGHT)])
        style.configure("Mimic.Treeview.Heading", background=TABLE, foreground=MUTED, relief="flat",
                        font=(f"{UI_FAMILY} Semibold", 9), bordercolor=TABLE, lightcolor=TABLE,
                        darkcolor=TABLE, padding=(6, 4))
        style.map("Mimic.Treeview.Heading", background=[("active", CARD)])
        style.layout("Mimic.Treeview", [("Mimic.Treeview.treearea", {"sticky": "nswe"})])

        table_card = ctk.CTkFrame(parent, corner_radius=10, fg_color=TABLE, border_width=1, border_color=BORDER)
        table_card.pack(fill="both", expand=True, padx=24, pady=(0, 10))
        table = tk.Frame(table_card, bg=TABLE)
        table.pack(fill="both", expand=True, padx=6, pady=6)
        columns = ("number", "delay", "action", "details")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", style="Mimic.Treeview",
                                 selectmode="extended")
        for name, title, width, anchor, stretch in (
            ("number", "#", 56, "e", False),
            ("delay", "Delay (ms)", 90, "e", False),
            ("action", "Action", 120, "w", False),
            ("details", "Details", 260, "w", True),
        ):
            self.tree.heading(name, text=title, anchor=anchor)
            self.tree.column(name, width=width, minwidth=40, anchor=anchor, stretch=stretch)
        self.tree.tag_configure("odd", background=STRIPE)
        self.tree.tag_configure("key", foreground="#ffd60a")
        self.tree.tag_configure("button", foreground="#64d2ff")
        self.tree.tag_configure("other", foreground="#da8fff")
        scrollbar = ctk.CTkScrollbar(table, command=self.tree.yview, fg_color=TABLE, width=12,
                                     button_color="#5a5a5e", button_hover_color="#6e6e73")
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<Double-1>", lambda _e: self.edit_selected())
        self.tree.bind("<Return>", lambda _e: self.edit_selected())
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.update_summary())
        self.root.bind("<Control-z>", lambda _e: self.undo())
        self.root.bind("<Control-y>", lambda _e: self.redo())
        self.root.bind("<Control-n>", lambda _e: self.new_recording())
        self.root.bind("<Control-o>", lambda _e: self.open_file())
        self.root.bind("<Control-s>", lambda _e: self.save_file())
        self.root.bind("<Control-S>", lambda _e: self.save_file_as())
        self.tree.bind("<Control-a>", lambda _e: self.tree.selection_set(self.tree.get_children()))

        self.summary_label = ctk.CTkLabel(parent, text="", font=font(12), text_color=MUTED)
        self.summary_label.pack(anchor="w", padx=26, pady=(0, 14))

    # ----- presentation helpers -----------------------------------------
    def refresh_steps(self, select: list[int] | None = None):
        self.tree.delete(*self.tree.get_children())
        for index, step in enumerate(self.step_list.steps):
            action, details = describe(step)
            tags = ["odd"] if index % 2 else []
            if step.kind in ("key_down", "key_up"):
                tags.append("key")
            elif step.kind in ("mouse_down", "mouse_up", "scroll"):
                tags.append("button")
            elif step.kind in ("wait", "text"):
                tags.append("other")
            self.tree.insert("", "end", iid=str(index), values=(index + 1, ms(step.delay), action, details),
                             tags=tags)
        if select:
            iids = [str(i) for i in select if 0 <= i < len(self.step_list.steps)]
            if iids:
                self.tree.selection_set(iids)
                self.tree.see(iids[0])
        self.update_summary()

    def selected_indices(self) -> list[int]:
        return sorted(int(iid) for iid in self.tree.selection())

    def update_summary(self):
        steps = self.step_list.steps
        selected = len(self.tree.selection())
        text = f"{len(steps)} steps · {format_duration(total_duration(steps))} at 1×"
        if selected:
            text += f" · {selected} selected"
        self.summary_label.configure(text=text)
        set_enabled(self.undo_btn, self.step_list.can_undo() and not self.busy)
        set_enabled(self.redo_btn, self.step_list.can_redo() and not self.busy)
        name = self.file_path.name if self.file_path else "Untitled"
        edited = " — Edited" if self.step_list.dirty else ""
        self.file_label.configure(text=f"{name}{edited}")
        self.root.title(f"{name} — Mimic")
        if not self.busy:
            self.timer_var.set(stopwatch(total_duration(steps)))
            self.timer_detail_var.set(f"{len(steps)} steps")

    def set_status(self, text: str, colour: str):
        self.status_dot.configure(fg_color=colour)
        self.status_text.configure(text=text.capitalize(), text_color=colour)
        self.status_badge.configure(border_color=colour if colour != SOFT else BORDER)

    def set_busy_controls(self, mode: str | None):
        """mode: None (idle), "recording" or "playing"."""
        if mode == "recording":
            self.record_btn.set("Stop", "stop", "red", "F9")
            self.play_btn.set("Play", "play", "green", "F10", enabled=False)
        elif mode == "playing":
            self.record_btn.set("Record", "record", "grey", "F9", enabled=False)
            self.play_btn.set("Stop", "stop", "red", "Esc")
        else:
            self.record_btn.set("Record", "record", "grey", "F9")
            self.play_btn.set("Play", "play", "green", "F10")
        idle = mode is None
        for button in self.file_buttons + self.edit_buttons + self.insert_buttons + [self.strip_btn]:
            set_enabled(button, idle)
        if not idle and self.menu is not None:
            self.menu.close()
        self.timer_title.configure(
            text={"recording": "Recording", "playing": "Playing"}.get(mode, "Recording Length"))
        self.update_summary()

    def toggle_top(self):
        self.root.attributes("-topmost", self.ontop_var.get())

    def toggle_pin(self):
        self.ontop_var.set(not self.ontop_var.get())
        self.toggle_top()

    LOG_HEIGHT = 240

    def fit_height(self, with_log: bool):
        """Make the window exactly as tall as the sidebar needs, plus room for the log when shown."""
        self.root.update_idletasks()
        content = 0
        for child in self.sidebar.pack_slaves():
            if child is self.log_text:
                continue
            pady = child.pack_info().get("pady", 0)
            pads = [int(v) for v in (pady if isinstance(pady, (tuple, list)) else str(pady).split())]
            content += child.winfo_reqheight() + (pads[0] + pads[-1] if pads else 0)
        scale = scaling(self.root)
        compact = 2 + content + round(14 * scale)
        height = compact + (round(self.LOG_HEIGHT * scale) if with_log else 0)
        self.root.wm_minsize(self.root.wm_minsize()[0], height)
        if self.chrome.zoomed:
            return
        width = max(int(self.root.wm_geometry().split("x")[0]), self.root.wm_minsize()[0])
        self.root.final_size = (width, height)
        self.root.wm_geometry(f"{width}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def hide_logs(self):
        self.log_text.pack_forget()
        self.hide_log_btn.pack_forget()
        self.show_log_btn.pack(side="right")
        self.fit_height(with_log=False)

    def show_logs(self):
        self.log_text.pack(fill="both", expand=True, padx=18, pady=(4, 18))
        self.show_log_btn.pack_forget()
        self.hide_log_btn.pack(side="right")
        self.fit_height(with_log=True)

    def update_slider_labels(self, _=None):
        self.speed_label.configure(text=f"{SPEEDS[int(round(self.speed_index_var.get()))]:g}×")
        if self.forever_var.get():
            self.repeat_label.configure(text="∞")
            self.repeat_scale.configure(state="disabled", button_color=FAINT, progress_color=FAINT)
        else:
            self.repeat_label.configure(text=f"{int(self.repeat_var.get())}×")
            self.repeat_scale.configure(state="normal", button_color=BRIGHT, progress_color=ACCENT)
        self.pause_label.configure(text=f"{self.pause_var.get():.1f} s")

    def log_message(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{timestamp}  {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
