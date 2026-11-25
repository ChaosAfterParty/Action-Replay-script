"""Convenient Windows launcher for Mimic."""

import ctypes
import os
import subprocess
import sys
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


def main() -> None:
    leave_console()
    use_project_environment()
    use_real_pixels()
    use_own_taskbar_identity()

    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(PROJECT_ROOT / "src"))

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

    root.after(30, lambda: show_centered(root))
    root.mainloop()


if __name__ == "__main__":
    main()
