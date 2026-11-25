"""Convenient Windows launcher for Mimic."""

import ctypes
import math
import os
import subprocess
import sys
import threading
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYTHONW = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
CONSOLE_FLAGS = ("--check", "--smoke-test", "--console")
READY_MESSAGE = "Mimic is ready ({})"


def leave_console() -> None:
    """Double-clicking main.py opens a black console window next to the app.
    Restart the app with pythonw.exe (no console) and let that window close.
    ``--console`` keeps the console, for example to read error messages."""
    if getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    if any(flag in sys.argv[1:] for flag in CONSOLE_FLAGS):
        return
    if Path(sys.executable).name.lower() != "python.exe":
        return  # already running without a console
    pythonw = VENV_PYTHONW if VENV_PYTHONW.is_file() else Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        return
    subprocess.Popen(
        [str(pythonw), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=str(PROJECT_ROOT),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    sys.exit(0)


def use_project_environment() -> None:
    """Restart with the project virtual environment when launched via ``py``."""
    if getattr(sys, "frozen", False):
        return

    running_in_virtualenv = sys.prefix != sys.base_prefix
    windowless = Path(sys.executable).name.lower() == "pythonw.exe"
    target = VENV_PYTHONW if windowless else VENV_PYTHON
    if not running_in_virtualenv and target.is_file():
        os.execv(
            str(target),
            [str(target), str(Path(__file__).resolve()), *sys.argv[1:]],
        )


def use_real_pixels() -> None:
    """Make recorded and replayed mouse positions use real screen pixels,
    also when Windows display scaling is above 100 %."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def use_own_taskbar_identity() -> None:
    """Group Mimic's windows under its own taskbar button and icon (not python.exe's)."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mimic.Recorder")
    except (AttributeError, OSError):
        pass


class StartupSpinner:
    """A small spinning ring in the middle of the screen while the app starts.

    It is a plain Windows window with true transparency, run by its own thread, so it keeps
    turning while the main thread is busy importing and building the interface."""

    SIZE = 34  # at 100 % display scaling
    FRAMES = 36
    STEP = 0.022  # seconds per frame

    def __init__(self):
        self._stop = threading.Event()
        self._shown = threading.Event()
        self._thread = threading.Thread(target=self._run, name="startup-spinner", daemon=True)

    def start(self) -> None:
        if sys.platform != "win32":
            return
        self._thread.start()
        self._shown.wait(2)

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(1)

    @staticmethod
    def _frames(size: int, count: int) -> list[bytes]:
        from PIL import Image, ImageChops, ImageDraw

        frames = []
        big = size * 4
        stroke = round(big * 0.13)
        edge = round(big * 0.03)  # faint dark rim so the ring also shows on white screens
        ring = big / 2 - stroke / 2 - edge - 2
        centre = big / 2
        for i in range(count):
            start = i * 360 / count - 90
            end = start + 290
            layers = []
            for width, colour, radius in ((stroke + 2 * edge, (0, 0, 0, 90), ring + edge),
                                          (stroke, (255, 255, 255, 255), ring)):
                layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
                draw = ImageDraw.Draw(layer)
                draw.arc((centre - radius, centre - radius, centre + radius, centre + radius),
                         start, end, fill=colour, width=width)
                for a in (start, end):  # round caps
                    x = centre + (ring - stroke / 2) * math.cos(math.radians(a))
                    y = centre + (ring - stroke / 2) * math.sin(math.radians(a))
                    draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=colour)
                layers.append(layer)
            image = Image.alpha_composite(*layers).resize((size, size), Image.LANCZOS)
            r, g, b, a = image.split()
            r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))  # premultiplied alpha
            frames.append(Image.merge("RGBA", (r, g, b, a)).tobytes("raw", "BGRA"))
        return frames

    def _run(self) -> None:
        try:
            self._spin()
        except Exception:  # the loader is decoration: never let it stop the app
            pass
        finally:
            self._shown.set()

    def _spin(self) -> None:
        from ctypes import wintypes as wt

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        wndproc = ctypes.WINFUNCTYPE(lresult, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", wndproc), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("op", ctypes.c_byte), ("flags", ctypes.c_byte),
                        ("alpha", ctypes.c_ubyte), ("format", ctypes.c_byte)]

        kernel32.GetModuleHandleW.restype = wt.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        user32.DefWindowProcW.restype = lresult
        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.RegisterClassW.restype = wt.ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
                                           wt.HINSTANCE, wt.LPVOID]
        user32.DestroyWindow.argtypes = [wt.HWND]
        user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetDC.restype = wt.HDC
        user32.GetDC.argtypes = [wt.HWND]
        user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.UpdateLayeredWindow.restype = wt.BOOL
        user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT),
                                               ctypes.POINTER(wt.SIZE), wt.HDC, ctypes.POINTER(wt.POINT),
                                               wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
        gdi32.GetDeviceCaps.argtypes = [wt.HDC, ctypes.c_int]
        gdi32.CreateCompatibleDC.restype = wt.HDC
        gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
        gdi32.CreateDIBSection.restype = wt.HBITMAP
        gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
        gdi32.SelectObject.restype = wt.HGDIOBJ
        gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wt.HDC]

        screen = user32.GetDC(None)
        size = round(self.SIZE * gdi32.GetDeviceCaps(screen, 88) / 96)  # 88 = LOGPIXELSX
        frames = self._frames(size, self.FRAMES)
        x = (user32.GetSystemMetrics(0) - size) // 2
        y = (user32.GetSystemMetrics(1) - size) // 2

        instance = kernel32.GetModuleHandleW(None)
        procedure = wndproc(lambda hwnd, msg, wparam, lparam: user32.DefWindowProcW(hwnd, msg, wparam, lparam))
        window_class = WNDCLASSW(0, procedure, 0, 0, instance, None, None, None, None, "StartupSpinner")
        user32.RegisterClassW(ctypes.byref(window_class))
        # layered (see-through) | tool window (no taskbar button) | topmost | never takes focus
        hwnd = user32.CreateWindowExW(0x80000 | 0x80 | 0x8 | 0x08000000, "StartupSpinner", "", 0x80000000,
                                      x, y, size, size, None, None, instance, None)
        memory = gdi32.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
        bitmap = gdi32.CreateDIBSection(memory, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(memory, bitmap)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        position, extent, origin = wt.POINT(x, y), wt.SIZE(size, size), wt.POINT(0, 0)
        message = wt.MSG()
        try:
            index = 0
            while True:
                ctypes.memmove(bits, frames[index], len(frames[index]))
                user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(position), ctypes.byref(extent),
                                           memory, ctypes.byref(origin), 0, ctypes.byref(blend), 2)
                if index == 0 and not self._shown.is_set():
                    user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                    self._shown.set()
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
                if self._stop.wait(self.STEP):
                    break
                index = (index + 1) % self.FRAMES
        finally:
            user32.DestroyWindow(hwnd)
            gdi32.SelectObject(memory, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory)
            user32.ReleaseDC(None, screen)
            user32.UnregisterClassW("StartupSpinner", instance)


def close_startup_image() -> None:
    """The exe shows a still picture of the loader while it unpacks itself;
    close it once the live, spinning loader is on screen."""
    try:
        import pyi_splash
    except ImportError:
        return
    pyi_splash.close()


def main() -> None:
    leave_console()
    use_project_environment()
    use_real_pixels()
    use_own_taskbar_identity()

    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(PROJECT_ROOT / "src"))

    quiet = any(flag in sys.argv[1:] for flag in ("--check", "--smoke-test"))
    spinner = StartupSpinner()
    if not quiet:
        spinner.start()
        close_startup_image()

    try:
        import customtkinter as ctk

        from application import MimicApplication

        if "--check" in sys.argv[1:]:
            if sys.stdout is not None:
                print(READY_MESSAGE.format(sys.executable))
            return

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        root = ctk.CTk()
        root.withdraw()  # shown only once it is built and has its final size
        app = MimicApplication(root)

        if "--smoke-test" in sys.argv[1:]:
            root.update_idletasks()
            app.close()
            return

        from main_window import show_centered

        def finish():
            spinner.stop()
            show_centered(root)

        root.after(30, finish)
    finally:
        if quiet:
            spinner.stop()
    root.mainloop()


if __name__ == "__main__":
    main()
