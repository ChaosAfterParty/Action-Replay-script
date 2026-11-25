# Mimic

Mimic is a small Windows desktop app that records what you do with the mouse and keyboard, lets you edit the
recording step by step, saves it to a file, and replays it as often as you like.

It runs only on your computer. It needs no internet connection and sends nothing anywhere.

## What it does

1. **Record** mouse moves, clicks, scrolling and key presses, with the exact pause before each one.
2. **Edit** the recording as a list of steps: change, delete, move, copy, or insert new steps.
3. **Change the timing** of any step, of a selection, or of the whole recording.
4. **Save and open** recordings as `.mimic` files (by default in `Documents\Mimic`).
5. **Replay** once, a set number of times (up to 100), or until you stop it, at 0.25× to 4× speed.

## Controls

| Control | What it does |
| --- | --- |
| **Record** / **F9** | The round button on the left of the stopwatch. Starts a new recording; it then turns into **Stop** (press it or **F9** again to finish). F8, F9 and F10 are never recorded. |
| **Play** / **F10** | The round button on the right. Replays the recording with the settings below; it then turns into **Stop**. |
| **Stop** / **Esc** | **Esc** stops playback from anywhere. While recording, Esc is recorded like any other key, so you can use it in a macro (and delete it afterwards if you pressed it by mistake). |
| **Keep on Top** / **F8** | Keeps Mimic above other windows. |
| **☰ menu** | New (Ctrl+N), Open (Ctrl+O), Save (Ctrl+S), Save As (Ctrl+Shift+S), and the Capture Settings. Mimic asks before throwing away unsaved changes. |
| **Window buttons** | Red closes, yellow minimises, green fills the screen (or double-click the title bar). Drag the title bar to move the window and the left, right or bottom edge to resize it. |
| **Playback Speed** | 0.25× to 4×. 2× halves every pause, 0.5× doubles them. |
| **Repeat** / **Forever** | How many times to play the recording, or loop until stopped. |
| **Pause Between Repeats** | Extra wait between two runs, 0 to 10 seconds. |
| **Capture Settings** (in the ☰ menu) | Tick what the next recording captures: mouse moves, clicks & scroll, keyboard. Turn off mouse moves for smaller, steadier recordings: clicks still go to the right place. |
| **Show Log / Hide Log** | Shows or hides the activity log (hidden when Mimic starts). |

## Editing a recording

Select one or more steps in the list (Shift-click or Ctrl-click for several, Ctrl+A for all).

| Button | What it does |
| --- | --- |
| **Edit** (or double-click, or Enter) | Change the step: its delay, position, button, key or text. |
| **Delete** (or the Delete key) | Remove the selected steps, for example a stray Esc at the end. |
| **▲ Up / ▼ Down** | Move the selected steps. |
| **Duplicate** | Copy the selected steps right after them. |
| **Set Delay** | Type milliseconds (`250`) to give the selected steps that delay, or a multiplier (`x0.5` = twice as fast, `x2` = twice as slow). With nothing selected it applies to every step. |
| **+ Wait / + Key / + Text / + Click** | Insert a new step after the selection (or at the end). **+ Click** has **Pick in 3 s**: move the mouse to the spot and Mimic fills in the position. |
| **Remove Mouse Moves** | Drops all mouse moves but keeps the timing. |
| **Undo / Redo** | Ctrl+Z / Ctrl+Y. Every change can be undone. |

Each step's **delay** is the pause before it runs. The total length is shown under the list.

## Project files

| File | Responsibility |
| --- | --- |
| `main.py` | Launcher. Restarts with the project's `.venv` automatically, turns on real-pixel mouse positions, starts the window. `--check` and `--smoke-test` verify the install. |
| `src/application.py` | App state: recording, playback, hotkeys, files, and every edit command. |
| `src/main_window.py` | Builds the window (controls on the left, step editor on the right), the edit dialogs, colours and fonts. |
| `src/macro_engine.py` | The engine: the step model, editing with undo/redo, recording and replaying through pynput, and saving `.mimic` files. |
| `assets/icon.png` / `assets/icon.ico` | The window icon and the Windows icon. |
| `assets/icons/` | Button icons from [Lucide](https://lucide.dev) (ISC license), drawn a little bolder. |
| `requirements.txt` | Lists the Python packages required to run the project. |
| `.gitignore` | Keeps the virtual environment and Python cache files out of git. |
| `LICENSE` | The project license (Apache 2.0). |

## Install on Windows

Requirements: Windows 10 or 11, Python 3 with the `py` launcher, PowerShell.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
py main.py --check
```

Start it with:

```powershell
py main.py
```

You do not need to activate the virtual environment: the launcher finds `.venv` and uses it.

## Limits

- Replays use screen positions. If you change display scaling, resolution or monitor layout, clicks can land in
  the wrong place.
- Windows does not let a normal program control windows that run as administrator. To automate those, run Mimic
  as administrator too.
- Some games and security tools ignore or block simulated input.
- A recording of your keyboard contains everything you typed, passwords included. Keep `.mimic` files private
  and delete steps you do not want stored.
- Antivirus tools sometimes flag programs that record or simulate input, especially unsigned ones.

## License

See `LICENSE` for the project license terms.
