"""Application state and lifecycle for Mimic."""

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from pynput import keyboard

from macro_engine import (
    BUTTONS,
    DEFAULT_FOLDER,
    FILE_SUFFIX,
    MacroFileError,
    PlayOptions,
    Player,
    RecordOptions,
    Recorder,
    Step,
    StepList,
    format_duration,
    key_label,
    load_macro,
    parse_user_key,
    precise_timer,
    save_macro,
    trim_trailing_inside,
)
from main_window import (
    BG,
    DEFAULT_SPEED_INDEX,
    GREEN,
    RED,
    SOFT,
    SPEEDS,
    Field,
    MainWindowLayout,
    ask,
    choose,
    ms,
    parse_int,
    parse_ms,
    stopwatch,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
ICON_PATH = BUNDLE_ROOT / "assets" / "icon.png"
ICO_PATH = BUNDLE_ROOT / "assets" / "icon.ico"

RECORD_HOTKEY = keyboard.Key.f9
PLAY_HOTKEY = keyboard.Key.f10
PIN_HOTKEY = keyboard.Key.f8
STOP_HOTKEY = keyboard.Key.esc
HOTKEY_NAMES = frozenset({"<f8>", "<f9>", "<f10>"})  # never recorded into a macro

FILE_TYPES = [("Mimic recordings", "*.mimic"), ("All files", "*.*")]


class MimicApplication(MainWindowLayout):
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Mimic Recorder")
        self.root.geometry("1180x900")
        self.root.minsize(1120, 500)
        self.root.configure(fg_color=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if ICON_PATH.is_file():
            self.window_icon = tk.PhotoImage(file=ICON_PATH)
            self.root.iconphoto(True, self.window_icon)
        if ICO_PATH.is_file():
            # Also stops CustomTkinter from swapping in its own icon on the taskbar.
            self.root.iconbitmap(default=str(ICO_PATH))

        self.step_list = StepList()
        self.file_path: Path | None = None
        self.mode: str | None = None  # None, "recording" or "playing"
        self.recorder: Recorder | None = None
        self.player = Player()
        self.stop_event = threading.Event()
        self.play_thread: threading.Thread | None = None
        self.play_started = 0.0
        self.progress = (0, 1)
        self.shown_row = None
        self.listener = None

        self.ontop_var = ctk.BooleanVar(value=False)
        self.speed_index_var = ctk.IntVar(value=DEFAULT_SPEED_INDEX)
        self.repeat_var = ctk.IntVar(value=1)
        self.forever_var = ctk.BooleanVar(value=False)
        self.pause_var = ctk.DoubleVar(value=0.0)
        self.rec_moves_var = ctk.BooleanVar(value=True)
        self.rec_buttons_var = ctk.BooleanVar(value=True)
        self.rec_keys_var = ctk.BooleanVar(value=True)
        self.timer_var = ctk.StringVar(value="00:00.00")
        self.timer_detail_var = ctk.StringVar(value="0 steps")

        self.build_ui()
        self.refresh_steps()
        self.start_hotkey_listener()

    @property
    def busy(self) -> bool:
        return self.mode is not None

    # ----- hotkeys -------------------------------------------------------
    def start_hotkey_listener(self):
        def on_press(key, injected=False):
            if injected:
                return  # Mimic's own playback must not trigger its hotkeys
            if key == RECORD_HOTKEY:
                self.root.after(0, self.toggle_recording)
            elif key == PLAY_HOTKEY:
                self.root.after(0, self.toggle_playback)
            elif key == PIN_HOTKEY:
                self.root.after(0, self.toggle_pin)
            elif key == STOP_HOTKEY and self.mode == "playing":
                self.root.after(0, self.stop_all)
                self.root.after(0, lambda: self.log_message(">> EMERGENCY STOP (ESC) <<"))

        self.listener = keyboard.Listener(on_press=on_press)
        self.listener.daemon = True
        self.listener.start()

    def toggle_recording(self):
        if self.mode == "recording":
            self.stop_recording()
        elif self.mode is None:
            self.start_recording()

    def toggle_playback(self):
        if self.mode == "playing":
            self.stop_all()
        elif self.mode is None:
            self.start_playback()

    # ----- recording -----------------------------------------------------
    def start_recording(self):
        if self.busy:
            return
        options = RecordOptions(
            mouse_moves=self.rec_moves_var.get(),
            mouse_buttons=self.rec_buttons_var.get(),
            keyboard=self.rec_keys_var.get(),
        )
        if not (options.mouse_moves or options.mouse_buttons or options.keyboard):
            self.log_message("Nothing to record: tick at least one Record option.")
            return
        if not self.confirm_discard("Start a new recording"):
            return
        self.recorder = Recorder(options, ignored_keys=HOTKEY_NAMES)
        self.recorder.start()
        self.mode = "recording"
        self.set_busy_controls("recording")
        self.set_status("RECORDING", RED)
        self.log_message("Recording started. Press F9 or STOP to finish.")
        self.tick()

    def stop_recording(self):
        if self.mode != "recording" or self.recorder is None:
            return
        steps = self.recorder.stop()
        steps = trim_trailing_inside(steps, self.window_box())
        self.mode = None
        self.step_list.load(steps)
        self.step_list.dirty = bool(steps)
        self.file_path = None
        self.set_busy_controls(None)
        self.set_status("READY", SOFT)
        self.refresh_steps()
        self.log_message(f"Recording stopped: {len(steps)} steps, "
                         f"{format_duration(sum(s.delay for s in steps))}.")

    def window_box(self) -> tuple[int, int, int, int]:
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        return x - 8, y - 8, x + self.root.winfo_width() + 8, y + self.root.winfo_height() + 8

    # ----- playback ------------------------------------------------------
    def play_options(self) -> PlayOptions:
        return PlayOptions(
            speed=SPEEDS[int(round(self.speed_index_var.get()))],
            repeats=0 if self.forever_var.get() else int(self.repeat_var.get()),
            pause_between=round(float(self.pause_var.get()), 1),
        )

    def start_playback(self):
        if self.busy:
            return
        steps = list(self.step_list.steps)
        if not steps:
            self.log_message("Nothing to play yet: record or open a recording first.")
            return
        options = self.play_options()
        self.stop_event.clear()
        self.mode = "playing"
        self.progress = (0, 1)
        self.shown_row = None
        self.play_started = time.perf_counter()
        self.set_busy_controls("playing")
        self.set_status("PLAYING", GREEN)
        runs = "until stopped" if options.repeats == 0 else f"{options.repeats}×"
        self.log_message(f"Playing {len(steps)} steps at {options.speed:g}×, {runs}. ESC stops.")

        def on_progress(index, run):
            self.progress = (index, run)

        def worker():
            error = None
            finished = False
            try:
                with precise_timer():
                    finished = self.player.play(steps, options, self.stop_event, on_progress)
            except Exception as exc:  # report, never leave the app stuck in "playing"
                error = exc
            self.root.after(0, self.playback_done, finished, error)

        self.play_thread = threading.Thread(target=worker, daemon=True)
        self.play_thread.start()
        self.tick()

    def playback_done(self, finished: bool, error: Exception | None):
        self.mode = None
        self.set_busy_controls(None)
        self.set_status("READY", SOFT)
        if error is not None:
            self.log_message(f"Playback failed: {error}")
        elif finished:
            self.log_message("Playback finished.")
        else:
            self.log_message("Playback stopped.")

    def stop_all(self):
        if self.mode == "recording":
            self.stop_recording()
        elif self.mode == "playing":
            self.stop_event.set()

    def tick(self):
        """Refresh the timer card while recording or playing."""
        if self.mode == "recording" and self.recorder:
            self.timer_var.set(stopwatch(self.recorder.elapsed))
            self.timer_detail_var.set(f"{self.recorder.count} steps captured")
        elif self.mode == "playing":
            index, run = self.progress
            total = len(self.step_list.steps)
            options = self.play_options()
            runs = "∞" if options.repeats == 0 else str(options.repeats)
            self.timer_var.set(stopwatch(time.perf_counter() - self.play_started))
            self.timer_detail_var.set(f"Step {index + 1} / {total} · Run {run} of {runs}")
            if self.shown_row != index and str(index) in self.tree.get_children():
                self.tree.selection_set(str(index))
                self.tree.see(str(index))
                self.shown_row = index
        else:
            return
        self.root.after(40, self.tick)

    # ----- files ---------------------------------------------------------
    def confirm_discard(self, action: str) -> bool:
        """True when it is fine to replace the current recording."""
        if not self.step_list.dirty or not self.step_list.steps:
            return True
        name = self.file_path.name if self.file_path else "Untitled"
        answer = choose(
            self.root, action, f"Do you want to save the changes you made to {name}?",
            "Your changes will be lost if you don't save them.",
            buttons=[("discard", "Don't Save"), ("cancel", "Cancel"), ("save", "Save")],
            default="save", cancel="cancel",
        )
        if answer == "cancel":
            return False
        if answer == "save":
            return self.save_file()
        return True

    def new_recording(self):
        if self.busy or not self.confirm_discard("New recording"):
            return
        self.step_list.load([])
        self.file_path = None
        self.refresh_steps()
        self.log_message("New empty recording.")

    def open_file(self):
        if self.busy or not self.confirm_discard("Open a recording"):
            return
        DEFAULT_FOLDER.mkdir(parents=True, exist_ok=True)
        name = filedialog.askopenfilename(parent=self.root, title="Open recording",
                                          initialdir=DEFAULT_FOLDER, filetypes=FILE_TYPES)
        if not name:
            return
        try:
            steps = load_macro(Path(name))
        except MacroFileError as error:
            choose(self.root, "Open", "The recording could not be opened.", str(error))
            return
        self.step_list.load(steps)
        self.file_path = Path(name)
        self.refresh_steps()
        self.log_message(f"Opened {self.file_path.name} ({len(steps)} steps).")

    def save_file(self) -> bool:
        if self.busy:
            return False
        if self.file_path is None:
            return self.save_file_as()
        return self._write(self.file_path)

    def save_file_as(self) -> bool:
        if self.busy:
            return False
        DEFAULT_FOLDER.mkdir(parents=True, exist_ok=True)
        name = filedialog.asksaveasfilename(
            parent=self.root, title="Save recording", initialdir=DEFAULT_FOLDER,
            initialfile=self.file_path.name if self.file_path else "recording.mimic",
            defaultextension=FILE_SUFFIX, filetypes=FILE_TYPES)
        if not name:
            return False
        return self._write(Path(name))

    def _write(self, path: Path) -> bool:
        try:
            self.file_path = save_macro(path, self.step_list.steps)
        except OSError as error:
            choose(self.root, "Save", "The recording could not be saved.", str(error))
            return False
        self.step_list.mark_saved()
        self.update_summary()
        self.log_message(f"Saved {self.file_path.name}.")
        return True

    # ----- editing -------------------------------------------------------
    def _after_change(self, select=None, message=None):
        self.refresh_steps(select)
        if message:
            self.log_message(message)

    def _need_selection(self) -> list[int] | None:
        chosen = self.selected_indices()
        if not chosen:
            self.log_message("Select one or more steps in the list first.")
            return None
        return chosen

    def delete_selected(self):
        if self.busy or not (chosen := self._need_selection()):
            return
        self.step_list.delete(chosen)
        self._after_change([min(chosen[0], len(self.step_list.steps) - 1)],
                           f"Deleted {len(chosen)} step{'s' if len(chosen) > 1 else ''}.")

    def move_selected(self, offset: int):
        if self.busy or not (chosen := self._need_selection()):
            return
        self._after_change(self.step_list.move(chosen, offset))

    def duplicate_selected(self):
        if self.busy or not (chosen := self._need_selection()):
            return
        self._after_change(self.step_list.duplicate(chosen), f"Duplicated {len(chosen)} step(s).")

    def undo(self):
        if not self.busy and self.step_list.undo():
            self._after_change(message="Undone.")

    def redo(self):
        if not self.busy and self.step_list.redo():
            self._after_change(message="Redone.")

    def remove_moves(self):
        if self.busy:
            return
        removed = self.step_list.remove_mouse_moves()
        self._after_change(message=f"Removed {removed} mouse moves (timing kept)." if removed
                           else "There are no mouse moves to remove.")

    def set_delay_selected(self):
        if self.busy or not self.step_list.steps:
            return
        chosen = self.selected_indices() or list(range(len(self.step_list.steps)))
        scope = f"{len(chosen)} selected step(s)" if self.selected_indices() else "all steps"

        def validate(values):
            raw = values["delay"].strip().lower().replace(" ", "")
            if raw.startswith(("x", "×", "*")):
                try:
                    factor = float(raw[1:].replace(",", "."))
                except ValueError:
                    raise ValueError("After x, type a number, for example x0.5 or x2.") from None
                if factor < 0:
                    raise ValueError("The multiplier cannot be negative.")
                return ("scale", factor)
            return ("set", parse_ms(raw))

        result = ask(self.root, f"Set delay for {scope}", [
            Field("delay", "Delay (ms)", "100",
                  hint="Type milliseconds (250) to give every chosen step that delay, "
                       "or a multiplier (x0.5 = twice as fast, x2 = twice as slow)."),
        ], validate)
        if result is None:
            return
        mode, value = result
        if mode == "set":
            self.step_list.set_delay(chosen, value)
            message = f"Set the delay of {scope} to {ms(value)} ms."
        else:
            self.step_list.scale_delay(chosen, value)
            message = f"Multiplied the delay of {scope} by {value:g}."
        self._after_change(chosen if self.selected_indices() else None, message)

    def _insert_position(self) -> int:
        chosen = self.selected_indices()
        return chosen[-1] + 1 if chosen else len(self.step_list.steps)

    def insert_wait(self):
        if self.busy:
            return
        result = ask(self.root, "Insert a wait", [Field("delay", "Wait (ms)", "1000")],
                     lambda v: [Step("wait", delay=parse_ms(v["delay"], "Wait"))])
        if result:
            self._after_change(self.step_list.insert(self._insert_position(), result), "Inserted a wait.")

    def insert_key(self):
        if self.busy:
            return

        def validate(v):
            key = parse_user_key(v["key"])
            return [Step("key_down", delay=parse_ms(v["delay"]), key=key),
                    Step("key_up", delay=parse_ms(v["hold"], "Hold"), key=key)]

        result = ask(self.root, "Insert a key press", [
            Field("key", "Key", "enter", hint="One character (a, 7, /) or a name: enter, esc, tab, space, "
                                              "backspace, shift, ctrl, alt, up, down, f1…f12."),
            Field("delay", "Delay before (ms)", "100"),
            Field("hold", "Hold (ms)", "50"),
        ], validate)
        if result:
            self._after_change(self.step_list.insert(self._insert_position(), result), "Inserted a key press.")

    def insert_text(self):
        if self.busy:
            return
        result = ask(self.root, "Insert text to type", [
            Field("text", "Text", "", hint="Mimic types this text in one go when the step runs."),
            Field("delay", "Delay before (ms)", "100"),
        ], lambda v: [Step("text", delay=parse_ms(v["delay"]), text=self._need_text(v["text"]))])
        if result:
            self._after_change(self.step_list.insert(self._insert_position(), result), "Inserted text.")

    @staticmethod
    def _need_text(text: str) -> str:
        if not text:
            raise ValueError("Type the text to insert.")
        return text

    def insert_click(self):
        if self.busy:
            return

        def validate(v):
            x, y = parse_int(v["x"], "X"), parse_int(v["y"], "Y")
            return [Step("mouse_down", delay=parse_ms(v["delay"]), x=x, y=y, button=v["button"]),
                    Step("mouse_up", delay=0.05, x=x, y=y, button=v["button"])]

        result = ask(self.root, "Insert a click", [
            Field("button", "Button", "left", choices=BUTTONS),
            Field("x", "X", "0", pick_position=True),
            Field("y", "Y", "0", hint="Screen position in pixels. Pick in 3 s fills it from where the mouse is."),
            Field("delay", "Delay before (ms)", "100"),
        ], validate)
        if result:
            self._after_change(self.step_list.insert(self._insert_position(), result), "Inserted a click.")

    def edit_selected(self):
        if self.busy or not (chosen := self._need_selection()):
            return
        index = chosen[0]
        step = self.step_list.steps[index]
        delay = Field("delay", "Wait (ms)" if step.kind == "wait" else "Delay before (ms)", ms(step.delay))
        fields = [delay]
        if step.kind in ("move", "mouse_down", "mouse_up", "scroll"):
            if step.kind in ("mouse_down", "mouse_up"):
                fields.append(Field("button", "Button", step.button, choices=BUTTONS))
            fields += [Field("x", "X", str(step.x), pick_position=True), Field("y", "Y", str(step.y))]
            if step.kind == "scroll":
                fields.append(Field("dy", "Scroll amount", str(step.dy), hint="Positive scrolls up, negative down."))
        elif step.kind in ("key_down", "key_up"):
            shown = step.key[1:-1] if step.key and step.key.startswith("<") else key_label(step)
            fields.append(Field("key", "Key", shown, hint="One character or a name such as enter, esc, shift, f5."))
        elif step.kind == "text":
            fields.append(Field("text", "Text", step.text or ""))

        def validate(v):
            changes = {"delay": parse_ms(v["delay"])}
            if "x" in v:
                changes["x"], changes["y"] = parse_int(v["x"], "X"), parse_int(v["y"], "Y")
            if "button" in v:
                changes["button"] = v["button"]
            if "dy" in v:
                changes["dy"] = parse_int(v["dy"], "Scroll amount")
            if "key" in v and v["key"] != shown:
                changes["key"], changes["vk"] = parse_user_key(v["key"]), None
            if "text" in v:
                changes["text"] = self._need_text(v["text"])
            data = step.to_dict() | changes
            return Step.from_dict(data)

        new_step = ask(self.root, f"Edit step {index + 1}", fields, validate)
        if new_step is not None:
            self.step_list.replace(index, new_step)
            self._after_change([index], f"Edited step {index + 1}.")

    # ----- shutdown ------------------------------------------------------
    def close(self):
        if self.mode == "playing":
            self.stop_event.set()
        elif self.mode == "recording" and self.recorder:
            self.recorder.stop()
            self.mode = None
        if not self.confirm_discard("Close Mimic"):
            return
        if self.listener is not None:
            self.listener.stop()
        self.root.destroy()
