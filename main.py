# Pomodoro Pro+ (patched timer stable single-file)
# Based on your uploaded main.py — fixes:
# - Proper after_id tracking
# - cancel_tick() helper
# - No duplicate _tick() scheduling
# - Session start functions cancel previous tick before scheduling
# - Added a status label so status() works
# - General cleanup of nested/misplaced defs

import os
import sys
import json
import csv
import time
import datetime
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText

# Optional dependencies
try:
    import pygame
    PYGAME_OK = True
except Exception:
    PYGAME_OK = False

try:
    import pyperclip
    PYPERCLIP_OK = True
except Exception:
    PYPERCLIP_OK = False

try:
    import pystray
    from PIL import Image, ImageDraw
    PYSTRAY_OK = True
except Exception:
    PYSTRAY_OK = False

try:
    from win10toast import ToastNotifier
    TOASTER_OK = True
except Exception:
    TOASTER_OK = False

# Platform checks
IS_WINDOWS = sys.platform.startswith("win")

# Data files
DATA_DIR = os.path.join(os.path.dirname(__file__), "pomo_data")
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")
LOG_FILE = os.path.join(DATA_DIR, "sessions_log.csv")
JOURNAL_FILE = os.path.join(DATA_DIR, "journal.json")
HEATMAP_FILE = os.path.join(DATA_DIR, "heatmap.json")

# Default config
DEFAULT_CONFIG = {
    "pomodoro_min": 25,
    "short_break_min": 5,
    "long_break_min": 15,
    "cycles_before_long_break": 4,
    "auto_start_next": True,
    "volume": 0.6,
    "theme": "dark",
    "ambient_enabled": True,
    "ambient_volume": 0.3,
    "lofi_playlist": [],
    "alarm_sound": None,
    "smart_enabled": True
}

# Themes
THEMES = {
    "dark": {"bg": "#121212", "fg": "#e6e6e6", "accent": "#00c2a8", "panel": "#1e1e1e"},
    "light": {"bg": "#f2f6f9", "fg": "#1f2937", "accent": "#0ea5ff", "panel": "#ffffff"},
    "coffee": {"bg": "#2f1b0c", "fg": "#f3e9de", "accent": "#d99058", "panel": "#3b2c20"},
    "nature": {"bg": "#0f2d17", "fg": "#e8f6ec", "accent": "#41a388", "panel": "#123926"},
    "ocean": {"bg": "#022b3a", "fg": "#cdeef4", "accent": "#3dd3c1", "panel": "#033a47"},
}

# Utility helpers
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("Save error:", e)

# Load or create config, stats, tasks
config = load_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
stats = load_json(STATS_FILE, {"total_pomodoros": 0, "total_minutes": 0, "streak": 0, "last_date": ""})
tasks = load_json(TASKS_FILE, [])
journal = load_json(JOURNAL_FILE, {})
heatmap = load_json(HEATMAP_FILE, {})

# Create log file header if doesn't exist
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "start_time", "duration_min", "type", "task", "notes"])

# Initialize pygame mixer if available
if PYGAME_OK:
    try:
        pygame.mixer.init()
    except Exception as e:
        print("pygame mixer init error:", e)
        PYGAME_OK = False

# Toast notifier
toaster = ToastNotifier() if (TOASTER_OK and IS_WINDOWS) else None

# Alarm play function (cross-platform)
def play_alarm_file(path=None):
    if path and os.path.exists(path):
        if PYGAME_OK:
            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.set_volume(config.get("volume", 0.6))
                pygame.mixer.music.play()
                return
            except:
                pass
        # fallback for Windows
        if IS_WINDOWS:
            try:
                import winsound
                winsound.PlaySound(path, winsound.SND_ASYNC)
                return
            except:
                pass
    # generic beep fallback
    if IS_WINDOWS:
        try:
            import winsound
            winsound.Beep(1000, 700)
            winsound.Beep(1200, 700)
            return
        except:
            pass
    # else: simple bell
    print("\a")

# Notification utility
def notify(title, message):
    try:
        if toaster:
            toaster.show_toast(title, message, threaded=True, duration=5)
        else:
            root = tk._default_root
            if root:
                root.after(10, lambda: messagebox.showinfo(title, message))
            else:
                print(title, message)
    except Exception as e:
        print("notify error:", e)

# Blocking websites (dangerous) — disabled by default
def block_websites(hosts_list, enable=True):
    hosts_path = r"C:\Windows\System32\drivers\etc\hosts" if IS_WINDOWS else "/etc/hosts"
    marker_start = "# POMODORO_PRO_PLUS BLOCK START"
    marker_end = "# POMODORO_PRO_PLUS BLOCK END"
    try:
        with open(hosts_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        raise PermissionError("Unable to read hosts file: " + str(e))

    if enable:
        if marker_start in content:
            return True
        entries = "\n".join(f"127.0.0.1\t{h}" for h in hosts_list)
        block_section = f"\n{marker_start}\n{entries}\n{marker_end}\n"
        try:
            with open(hosts_path, "a", encoding="utf-8") as f:
                f.write(block_section)
            return True
        except Exception as e:
            raise PermissionError("Unable to write hosts file: " + str(e))
    else:
        if marker_start in content:
            start = content.index(marker_start)
            end = content.index(marker_end) + len(marker_end)
            new_content = content[:start] + content[end:]
            try:
                with open(hosts_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                return True
            except Exception as e:
                raise PermissionError("Unable to update hosts file: " + str(e))
        return True

# Smart timer adjuster (simple heuristic)
def smart_adjust(session_history, base_minutes):
    if not config.get("smart_enabled", True):
        return base_minutes
    recent = session_history[-8:]
    pauses = sum(1 for d, c, p in recent if p > 0)
    completes = sum(1 for d, c, p in recent if c)
    if pauses >= 3:
        return max(15, base_minutes - 5)
    if completes >= 4:
        return min(60, base_minutes + 5)
    return base_minutes

# ---------------- GUI APP ----------------
class PomodoroApp(tk.Tk):
    def __init__(self):
        super().__init__()
        # used to store after() id so we can cancel duplicate timers
        self.after_id = None

        self.title("Clock & Pomodoro Pro+")
        self.geometry("980x680")
        self.resizable(False, False)
        self.theme_name = config.get("theme", "dark")
        self.theme = THEMES.get(self.theme_name, THEMES["dark"])
        self.configure(bg=self.theme["bg"])

        # App state
        self.running = False
        self.paused = False
        self.current_seconds = 0
        self.mode = "idle"  # 'focus', 'short_break', 'long_break', 'idle'
        self.cycle_count = 0
        self.session_history = []  # (duration_min, completed_bool, paused_count)
        self.paused_count = 0
        self.task_selected = None
        self.ambient_playing = False
        self.lofi_index = 0

        # Load stats
        self.stats = stats
        self.tasks = tasks
        self.journal = journal
        self.heatmap = heatmap

        # Build UI
        self._build_ui()
        self._update_ui_loop()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if PYSTRAY_OK:
            self._setup_tray_icon_async()

    def _build_ui(self):
        header = tk.Frame(self, bg=self.theme["panel"], bd=0)
        header.place(x=12, y=12, width=956, height=80)
        title = tk.Label(header, text="Clock & Pomodoro Pro+", font=("Segoe UI", 20, "bold"),
                         bg=self.theme["panel"], fg=self.theme["accent"])
        title.place(x=18, y=12)
        subtitle = tk.Label(header, text="Focus. Track. Improve.", font=("Segoe UI", 10), bg=self.theme["panel"], fg=self.theme["fg"])
        subtitle.place(x=20, y=46)

        left_panel = tk.Frame(self, bg=self.theme["panel"])
        left_panel.place(x=12, y=104, width=480, height=420)

        self.canvas = tk.Canvas(left_panel, width=300, height=300, bg=self.theme["panel"], highlightthickness=0)
        self.canvas.place(x=90, y=10)
        self.circle_center = (150, 150)
        self.circle_radius = 120
        self._draw_static_circle()

        self.time_var = tk.StringVar(value="00:00")
        self.time_lbl = tk.Label(left_panel, textvariable=self.time_var, font=("Consolas", 26, "bold"),
                                 bg=self.theme["panel"], fg=self.theme["accent"])
        self.time_lbl.place(x=150, y=130, anchor="center")

        self.mode_var = tk.StringVar(value="Idle")
        mode_lbl = tk.Label(left_panel, textvariable=self.mode_var, font=("Segoe UI", 12), bg=self.theme["panel"], fg=self.theme["fg"])
        mode_lbl.place(x=240, y=250)

        btn_frame = tk.Frame(left_panel, bg=self.theme["panel"])
        btn_frame.place(x=70, y=270, width=340, height=90)
        start_btn = tk.Button(btn_frame, text="Start", width=9, command=self.start)
        start_btn.grid(row=0, column=0, padx=6, pady=6)
        pause_btn = tk.Button(btn_frame, text="Pause", width=9, command=self.pause)
        pause_btn.grid(row=0, column=1, padx=6, pady=6)
        skip_btn = tk.Button(btn_frame, text="Skip", width=9, command=self.skip)
        skip_btn.grid(row=0, column=2, padx=6, pady=6)
        reset_btn = tk.Button(btn_frame, text="Reset", width=9, command=self.reset)
        reset_btn.grid(row=0, column=3, padx=6, pady=6)

        ambient_frame = tk.LabelFrame(left_panel, text="Ambient / Lofi", bg=self.theme["panel"], fg=self.theme["fg"])
        ambient_frame.place(x=12, y=360, width=456, height=48)
        self.ambient_var = tk.BooleanVar(value=config.get("ambient_enabled", True))
        ambient_chk = tk.Checkbutton(ambient_frame, text="Play ambient", variable=self.ambient_var, bg=self.theme["panel"], fg=self.theme["fg"], command=self.toggle_ambient)
        ambient_chk.place(x=10, y=6)
        tk.Button(ambient_frame, text="Add Track", command=self.add_lofi_track).place(x=140, y=6)
        tk.Button(ambient_frame, text="Next", command=self.play_next_lofi).place(x=220, y=6)
        tk.Button(ambient_frame, text="Stop", command=self.stop_ambient).place(x=260, y=6)

        right_panel = tk.Frame(self, bg=self.theme["panel"])
        right_panel.place(x=504, y=104, width=464, height=420)

        task_frame = tk.LabelFrame(right_panel, text="Tasks", bg=self.theme["panel"], fg=self.theme["fg"])
        task_frame.place(x=12, y=8, width=440, height=160)
        self.task_listbox = tk.Listbox(task_frame, bg=self.theme["bg"], fg=self.theme["fg"], selectbackground=self.theme["accent"])
        self.task_listbox.place(x=8, y=8, width=300, height=120)
        self.task_listbox.bind("<<ListboxSelect>>", self.on_task_select)
        task_btn_frame = tk.Frame(task_frame, bg=self.theme["panel"])
        task_btn_frame.place(x=316, y=8, width=110, height=120)
        tk.Button(task_btn_frame, text="Add", command=self.add_task).pack(pady=6, fill="x")
        tk.Button(task_btn_frame, text="Edit", command=self.edit_task).pack(pady=6, fill="x")
        tk.Button(task_btn_frame, text="Remove", command=self.remove_task).pack(pady=6, fill="x")

        stats_frame = tk.LabelFrame(right_panel, text="Stats", bg=self.theme["panel"], fg=self.theme["fg"])
        stats_frame.place(x=12, y=176, width=220, height=120)
        self.total_p_var = tk.StringVar(value=f"Total Pomodoros: {self.stats.get('total_pomodoros', 0)}")
        tk.Label(stats_frame, textvariable=self.total_p_var, bg=self.theme["panel"], fg=self.theme["fg"]).place(x=10, y=8)
        self.total_min_var = tk.StringVar(value=f"Total Minutes: {self.stats.get('total_minutes', 0)}")
        tk.Label(stats_frame, textvariable=self.total_min_var, bg=self.theme["panel"], fg=self.theme["fg"]).place(x=10, y=36)
        self.streak_var = tk.StringVar(value=f"Streak: {self.stats.get('streak', 0)}")
        tk.Label(stats_frame, textvariable=self.streak_var, bg=self.theme["panel"], fg=self.theme["fg"]).place(x=10, y=64)

        journal_frame = tk.LabelFrame(right_panel, text="Session Journal", bg=self.theme["panel"], fg=self.theme["fg"])
        journal_frame.place(x=240, y=176, width=212, height=120)
        self.journal_btn = tk.Button(journal_frame, text="View Journal", command=self.view_journal)
        self.journal_btn.place(x=10, y=8, width=188)
        self.export_btn = tk.Button(journal_frame, text="Export Logs", command=self.export_logs)
        self.export_btn.place(x=10, y=48, width=188)

        heatmap_frame = tk.LabelFrame(right_panel, text="Focus Heatmap (7d)", bg=self.theme["panel"], fg=self.theme["fg"])
        heatmap_frame.place(x=12, y=304, width=440, height=100)
        self.heatmap_canvas = tk.Canvas(heatmap_frame, width=416, height=64, bg=self.theme["panel"], highlightthickness=0)
        self.heatmap_canvas.place(x=8, y=10)
        self.draw_heatmap()

        bottom_frame = tk.Frame(self, bg=self.theme["bg"])
        bottom_frame.place(x=12, y=540, width=956, height=128)

        alarm_frame = tk.LabelFrame(bottom_frame, text="Alarm", bg=self.theme["bg"], fg=self.theme["fg"])
        alarm_frame.place(x=12, y=8, width=320, height=110)
        tk.Label(alarm_frame, text="Alarm Time (HH:MM:SS):", bg=self.theme["bg"], fg=self.theme["fg"]).place(x=8, y=8)
        self.alarm_entry = tk.Entry(alarm_frame, width=12)
        self.alarm_entry.place(x=12, y=30)
        tk.Button(alarm_frame, text="Set Alarm", command=self.set_alarm).place(x=150, y=28)
        tk.Button(alarm_frame, text="Choose Sound", command=self.choose_alarm_sound).place(x=12, y=56)

        mini_frame = tk.LabelFrame(bottom_frame, text="Mini Timer", bg=self.theme["bg"], fg=self.theme["fg"])
        mini_frame.place(x=344, y=8, width=200, height=110)
        tk.Button(mini_frame, text="Show Mini", command=self.show_mini).place(x=18, y=12)
        tk.Button(mini_frame, text="Hide Mini", command=self.hide_mini).place(x=18, y=48)

        settings_frame = tk.LabelFrame(bottom_frame, text="Settings", bg=self.theme["bg"], fg=self.theme["fg"])
        settings_frame.place(x=556, y=8, width=380, height=110)
        tk.Button(settings_frame, text="Change Theme", command=self.change_theme).place(x=8, y=8)
        tk.Button(settings_frame, text="Toggle Smart Mode", command=self.toggle_smart).place(x=120, y=8)
        tk.Button(settings_frame, text="Block Sites (opt)", command=self.block_sites_prompt).place(x=8, y=48)
        tk.Button(settings_frame, text="Clear Stats", command=self.clear_stats).place(x=220, y=48)

        # Status label
        self.status_label = tk.Label(
            bottom_frame,
            text="Ready",
            anchor='w',
            bg=self.theme["panel"],
            fg=self.theme["fg"],
            relief="flat"  # or "solid" if you want a border
        )
        self.status_label.place(x=0, y=668, width=980, height=32)


        self.refresh_task_list()
        self.mini_window = None

    def _draw_static_circle(self):
        self.canvas.delete("all")
        cx, cy = self.circle_center
        r = self.circle_radius
        self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, outline=self.theme["fg"], width=2)
        self.canvas.create_oval(cx-(r-12), cy-(r-12), cx+(r-12), cy+(r-12), fill=self.theme["panel"], outline="")

    def _draw_progress(self, fraction):
        self._draw_static_circle()
        cx, cy = self.circle_center
        r = self.circle_radius - 12
        start = -90
        extent = fraction * 360
        # create arc without capstyle for compatibility
        self.canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=start, extent=extent, style="arc", outline=self.theme["accent"], width=14)
        self.canvas.create_oval(cx-30, cy-30, cx+30, cy+30, fill=self.theme["panel"], outline="")

    # New helper: cancel any scheduled tick
    def cancel_tick(self):
        if getattr(self, "after_id", None) is not None:
            try:
                self.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    # Timer control functions (fixed)
    def start(self):
        # If idle, start a fresh focus session
        if self.mode == "idle":
            self._start_focus()
            return
        # otherwise resume or ensure tick running
        if not self.running:
            self.running = True
            self.paused = False
            # schedule tick if not scheduled already
            if getattr(self, "after_id", None) is None:
                self.after_id = self.after(1000, self._tick)
        self.status("Started")

    def pause(self):
        if self.running:
            self.paused = not self.paused
            if self.paused:
                self.paused_count += 1
                self.status("Paused")
            else:
                self.status("Resumed")
                # ensure tick is scheduled
                if getattr(self, "after_id", None) is None:
                    self.after_id = self.after(1000, self._tick)

    def skip(self):
        # skip current session
        self._end_session(completed=False, skipped=True)

    def reset(self):
        self.cancel_tick()
        self.running = False
        self.paused = False
        self.mode = "idle"
        self.current_seconds = 0
        self.mode_var.set("Idle")
        self.time_var.set("00:00")
        self._draw_progress(0)
        self.status("Reset")

    def _start_focus(self):
        self.cancel_tick()
        base = config.get("pomodoro_min", 25)
        base_adj = smart_adjust(self.session_history, base)
        self.current_seconds = int(base_adj * 60)
        self.mode = "focus"
        self.running = True
        self.paused = False
        self.mode_var.set("Focus")
        # schedule tick (only one)
        self.after_id = self.after(1000, self._tick)

    def _start_short_break(self):
        self.cancel_tick()
        base = config.get("short_break_min", 5)
        base_adj = smart_adjust(self.session_history, base)
        self.current_seconds = int(base_adj * 60)
        self.mode = "short_break"
        self.running = True
        self.paused = False
        self.mode_var.set("Short Break")
        self.after_id = self.after(1000, self._tick)

    def _start_long_break(self):
        self.cancel_tick()
        base = config.get("long_break_min", 15)
        base_adj = smart_adjust(self.session_history, base)
        self.current_seconds = int(base_adj * 60)
        self.mode = "long_break"
        self.running = True
        self.paused = False
        self.mode_var.set("Long Break")
        self.after_id = self.after(1000, self._tick)

    def _tick(self):
        # Main countdown loop (single-scheduled)
        # clear stored after_id at entry because we'll set a new one at end
        self.after_id = None

        if not self.running:
            return
        if self.paused:
            # schedule next check while paused
            self.after_id = self.after(1000, self._tick)
            return
        if self.current_seconds <= 0:
            self._end_session(completed=True)
            return
        mins = self.current_seconds // 60
        secs = self.current_seconds % 60
        self.time_var.set(f"{mins:02}:{secs:02}")
        total = {
            "focus": config.get("pomodoro_min", 25) * 60,
            "short_break": config.get("short_break_min", 5) * 60,
            "long_break": config.get("long_break_min", 15) * 60
        }.get(self.mode, 1)
        frac = 1 - (self.current_seconds / max(1, total))
        self._draw_progress(frac)
        self.current_seconds -= 1
        # schedule exactly one next call and store id
        self.after_id = self.after(1000, self._tick)

    def _end_session(self, completed=True, skipped=False):
        # cancel any scheduled tick to avoid overlaps when starting next session
        self.cancel_tick()

        duration_min = 0
        if self.mode != "idle":
            if self.mode == "focus":
                duration_min = config.get("pomodoro_min", 25)
            elif self.mode == "short_break":
                duration_min = config.get("short_break_min", 5)
            elif self.mode == "long_break":
                duration_min = config.get("long_break_min", 15)
        now = datetime.datetime.now()
        start_time = now.strftime("%Y-%m-%d %H:%M:%S")
        task_name = self.task_selected.get("title") if self.task_selected else ""
        notes = ""
        if self.mode == "focus" and completed:
            notes = simpledialog.askstring("Session Journal", "What did you accomplish in this session?", parent=self)
            if notes:
                date_key = now.strftime("%Y-%m-%d")
                if date_key not in self.journal:
                    self.journal[date_key] = []
                self.journal[date_key].append({"time": start_time, "task": task_name, "notes": notes})
                save_json(JOURNAL_FILE, self.journal)
        if self.mode == "focus" and completed and not skipped:
            self.stats["total_pomodoros"] = self.stats.get("total_pomodoros", 0) + 1
            self.stats["total_minutes"] = self.stats.get("total_minutes", 0) + duration_min
            self.session_history.append((duration_min, True, self.paused_count))
            today = datetime.date.today().isoformat()
            if self.stats.get("last_date") == today:
                self.stats["streak"] = self.stats.get("streak", 0) + 1
            else:
                last = self.stats.get("last_date")
                if last:
                    last_date = datetime.date.fromisoformat(last)
                    if (datetime.date.today() - last_date).days == 1:
                        self.stats["streak"] = self.stats.get("streak", 0) + 1
                    else:
                        self.stats["streak"] = 1
                else:
                    self.stats["streak"] = 1
                self.stats["last_date"] = today
        else:
            self.session_history.append((duration_min, False, self.paused_count))
        self.paused_count = 0
        save_json(STATS_FILE, self.stats)
        with open(LOG_FILE, "a", newline='', encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([now.date().isoformat(), now.time().isoformat(timespec='seconds'), duration_min, self.mode, task_name, notes or ""])
        if completed:
            notify("Session Complete", f"{self.mode.replace('_',' ').title()} finished.")
            play_alarm_file(config.get("alarm_sound"))

        # Next mode logic
        if self.mode == "focus":
            self.cycle_count += 1
            if self.cycle_count % config.get("cycles_before_long_break", 4) == 0:
                self._start_long_break()
            else:
                self._start_short_break()
        elif self.mode in ("short_break", "long_break"):
            if config.get("auto_start_next", True):
                # start next focus (cancel_tick is called inside _start_focus)
                self._start_focus()
            else:
                self.running = False
                self.mode = "idle"
                self.mode_var.set("Idle")
                self.time_var.set("00:00")
        self.total_p_var.set(f"Total Pomodoros: {self.stats.get('total_pomodoros', 0)}")
        self.total_min_var.set(f"Total Minutes: {self.stats.get('total_minutes', 0)}")
        self.streak_var.set(f"Streak: {self.stats.get('streak', 0)}")
        self.draw_heatmap()

    # Task manager functions
    def refresh_task_list(self):
        self.task_listbox.delete(0, tk.END)
        for t in self.tasks:
            title = t.get("title", "Untitled")
            est = t.get("est_pomodoros", 0)
            done = t.get("done", False)
            label = f"[{'X' if done else ' '}] {title} ({est})"
            self.task_listbox.insert(tk.END, label)

    def on_task_select(self, evt):
        sel = self.task_listbox.curselection()
        if not sel:
            self.task_selected = None
            return
        idx = sel[0]
        self.task_selected = self.tasks[idx]

    def add_task(self):
        title = simpledialog.askstring("Add Task", "Task title:", parent=self)
        if not title:
            return
        est = simpledialog.askinteger("Estimate", "Estimated pomodoros:", parent=self, minvalue=0, initialvalue=1)
        task = {"title": title, "est_pomodoros": est or 1, "done": False}
        self.tasks.append(task)
        save_json(TASKS_FILE, self.tasks)
        self.refresh_task_list()

    def edit_task(self):
        sel = self.task_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select", "Select a task first.")
            return
        idx = sel[0]
        task = self.tasks[idx]
        new_title = simpledialog.askstring("Edit Task", "Task title:", initialvalue=task["title"], parent=self)
        if new_title:
            task["title"] = new_title
        new_est = simpledialog.askinteger("Estimate", "Estimated pomodoros:", initialvalue=task.get("est_pomodoros",1), parent=self, minvalue=0)
        if new_est is not None:
            task["est_pomodoros"] = new_est
        save_json(TASKS_FILE, self.tasks)
        self.refresh_task_list()

    def remove_task(self):
        sel = self.task_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select", "Select a task first.")
            return
        idx = sel[0]
        if messagebox.askyesno("Remove", "Remove selected task?"):
            self.tasks.pop(idx)
            save_json(TASKS_FILE, self.tasks)
            self.refresh_task_list()

    # Ambient audio and playlist functions
    def add_lofi_track(self):
        if not PYGAME_OK:
            messagebox.showwarning("Missing package", "pygame is required for audio playback.")
            return
        files = filedialog.askopenfilenames(title="Select audio files (mp3/wav)", filetypes=[("Audio", "*.mp3 *.wav")])
        if files:
            config.setdefault("lofi_playlist", [])
            config["lofi_playlist"].extend(files)
            save_json(CONFIG_FILE, config)
            messagebox.showinfo("Added", f"Added {len(files)} tracks to playlist.")

    def play_next_lofi(self):
        if not PYGAME_OK:
            messagebox.showwarning("Missing package", "pygame is required for audio playback.")
            return
        pl = config.get("lofi_playlist", [])
        if not pl:
            messagebox.showinfo("Playlist", "No tracks in playlist. Add some.")
            return
        self.lofi_index = (self.lofi_index + 1) % len(pl)
        path = pl[self.lofi_index]
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(config.get("ambient_volume", 0.3))
            pygame.mixer.music.play(-1)
            self.ambient_playing = True
            self.status("Playing ambient")
        except Exception as e:
            messagebox.showerror("Audio error", f"Cannot play file: {e}")

    def toggle_ambient(self):
        if not PYGAME_OK:
            messagebox.showwarning("Missing package", "pygame required.")
            self.ambient_var.set(False)
            return
        if self.ambient_var.get():
            pl = config.get("lofi_playlist", [])
            if not pl:
                messagebox.showinfo("Add tracks", "Add tracks to playlist first.")
                self.ambient_var.set(False)
                return
            if not pygame.mixer.music.get_busy():
                try:
                    path = pl[self.lofi_index]
                    pygame.mixer.music.load(path)
                    pygame.mixer.music.set_volume(config.get("ambient_volume", 0.3))
                    pygame.mixer.music.play(-1)
                    self.ambient_playing = True
                except Exception as e:
                    messagebox.showerror("Audio error", e)
            else:
                pygame.mixer.music.unpause()
                self.ambient_playing = True
        else:
            pygame.mixer.music.pause()
            self.ambient_playing = False

    def stop_ambient(self):
        if PYGAME_OK:
            pygame.mixer.music.stop()
            self.ambient_playing = False
            self.ambient_var.set(False)

    # Alarm functions
    def choose_alarm_sound(self):
        f = filedialog.askopenfilename(title="Choose alarm sound (wav/mp3)", filetypes=[("Audio","*.wav *.mp3")])
        if f:
            config["alarm_sound"] = f
            save_json(CONFIG_FILE, config)
            messagebox.showinfo("Alarm", "Alarm sound set.")

    def set_alarm(self):
        t = self.alarm_entry.get().strip()
        try:
            if len(t.split(":")) == 2:
                t_full = t + ":00"
            else:
                t_full = t
            datetime.datetime.strptime(t_full, "%H:%M:%S")
        except:
            messagebox.showerror("Invalid", "Time must be HH:MM or HH:MM:SS")
            return
        threading.Thread(target=self._alarm_monitor, args=(t_full,), daemon=True).start()
        self.status(f"Alarm set for {t_full}")

    def _alarm_monitor(self, t_full):
        self.status("Alarm monitoring...")
        while True:
            now = datetime.datetime.now().strftime("%H:%M:%S")
            if now == t_full:
                notify("Alarm", f"Alarm time {t_full}")
                play_alarm_file(config.get("alarm_sound"))
                break
            time.sleep(1)
        self.status("Alarm triggered")

    # Mini window
    def show_mini(self):
        if self.mini_window and tk.Toplevel.winfo_exists(self.mini_window):
            return
        self.mini_window = tk.Toplevel(self)
        self.mini_window.title("Mini Timer")
        self.mini_window.geometry("200x80+50+50")
        self.mini_window.attributes("-topmost", True)
        self.mini_label = tk.Label(self.mini_window, textvariable=self.time_var, font=("Consolas", 18, "bold"))
        self.mini_label.pack(expand=True)

    def hide_mini(self):
        if self.mini_window:
            try:
                self.mini_window.destroy()
            except:
                pass
            self.mini_window = None

    # Dashboard, export, journal
    def view_journal(self):
        win = tk.Toplevel(self)
        win.title("Session Journal")
        win.geometry("600x400")
        tv = ScrolledText(win, width=80, height=24)
        tv.pack(fill="both", expand=True)
        content = json.dumps(self.journal, indent=2)
        tv.insert("1.0", content)
        tv.config(state="disabled")

    def export_logs(self):
        dest = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")])
        if not dest:
            return
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as src:
                data = src.read()
            with open(dest, "w", encoding="utf-8") as dst:
                dst.write(data)
            messagebox.showinfo("Export", f"Logs exported to {dest}")
        except Exception as e:
            messagebox.showerror("Export error", str(e))

    # Heatmap drawing
    def draw_heatmap(self):
        self.heatmap_canvas.delete("all")
        today = datetime.date.today()
        days = [(today - datetime.timedelta(days=i)).isoformat() for i in reversed(range(7))]
        values = [self.heatmap.get(d, 0) for d in days]
        maxv = max(values) if values else 1
        w = 56
        for i, d in enumerate(days):
            val = values[i]
            r = 34
            g = max(34, 150 - int(100 * (1 - (val / maxv))) ) if maxv>0 else 34
            b = 60
            color = f"#{r:02x}{g:02x}{b:02x}"
            x = 8 + i * (w + 2)
            self.heatmap_canvas.create_rectangle(x, 8, x+w, 56, fill=color, outline="")
            self.heatmap_canvas.create_text(x + w/2, 58, text=d[-2:], fill=self.theme["fg"], font=("Segoe UI", 8))

    # Settings and utilities
    def change_theme(self):
        choices = list(THEMES.keys())
        choice = simpledialog.askstring("Theme", f"Choose theme: {', '.join(choices)}", parent=self)
        if choice and choice in THEMES:
            self.theme_name = choice
            config["theme"] = choice
            save_json(CONFIG_FILE, config)
            self.theme = THEMES[choice]
            self._apply_theme()

    def toggle_smart(self):
        config["smart_enabled"] = not config.get("smart_enabled", True)
        save_json(CONFIG_FILE, config)
        messagebox.showinfo("Smart Mode", f"Smart adjustments {'enabled' if config['smart_enabled'] else 'disabled'}")

    def block_sites_prompt(self):
        if not IS_WINDOWS and not messagebox.askokcancel("Warning", "Editing hosts file requires root privileges. Proceed?"):
            return
        sites = simpledialog.askstring("Block Sites", "Enter comma-separated hostnames to block (e.g., facebook.com, instagram.com):", parent=self)
        if not sites:
            return
        hosts = [s.strip() for s in sites.split(",") if s.strip()]
        confirm = messagebox.askyesno("Confirm", f"This will add entries to the system hosts file to block: {hosts}\nYou must run the app with admin privileges.\nProceed?")
        if confirm:
            try:
                block_websites(hosts, enable=True)
                messagebox.showinfo("Blocked", "Websites blocked (check hosts file).")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def clear_stats(self):
        if messagebox.askyesno("Clear Stats", "Clear all stored statistics?"):
            self.stats = {"total_pomodoros": 0, "total_minutes": 0, "streak": 0}
            save_json(STATS_FILE, self.stats)
            self.total_p_var.set(f"Total Pomodoros: {self.stats['total_pomodoros']}")
            self.total_min_var.set(f"Total Minutes: {self.stats['total_minutes']}")
            self.streak_var.set(f"Streak: {self.stats['streak']}")

    def status(self, text):
        try:
            self.status_label.config(text=str(text))
        except:
            pass

    # Tray icon (async to avoid blocking)
    def _setup_tray_icon_async(self):
        def _run_tray():
            img = Image.new('RGB', (64, 64), color=(50, 50, 50))
            d = ImageDraw.Draw(img)
            d.rectangle((8, 8, 56, 56), fill=(10, 150, 120))
            menu = pystray.Menu(
                pystray.MenuItem("Show", lambda: self._on_tray_show()),
                pystray.MenuItem("Exit", lambda: self._on_tray_exit())
            )
            icon = pystray.Icon("PomodoroProPlus", img, "Pomodoro Pro+", menu)
            self.tray_icon = icon
            icon.run()
        t = threading.Thread(target=_run_tray, daemon=True)
        t.start()

    def _on_tray_show(self):
        try:
            self.after(0, self.deiconify)
            if hasattr(self, "tray_icon"):
                try:
                    self.tray_icon.stop()
                except:
                    pass
        except:
            pass

    def _on_tray_exit(self):
        try:
            if hasattr(self, "tray_icon"):
                self.tray_icon.stop()
        except:
            pass
        self.quit()

    def _on_close(self):
        save_json(CONFIG_FILE, config)
        save_json(STATS_FILE, self.stats)
        save_json(TASKS_FILE, self.tasks)
        save_json(JOURNAL_FILE, self.journal)
        save_json(HEATMAP_FILE, self.heatmap)
        try:
            if PYGAME_OK:
                pygame.mixer.quit()
        except:
            pass
        self.destroy()

    def _update_ui_loop(self):
        self.total_p_var.set(f"Total Pomodoros: {self.stats.get('total_pomodoros', 0)}")
        self.total_min_var.set(f"Total Minutes: {self.stats.get('total_minutes', 0)}")
        self.streak_var.set(f"Streak: {self.stats.get('streak', 0)}")
        self.refresh_task_list()
        save_json(CONFIG_FILE, config)
        save_json(STATS_FILE, self.stats)
        save_json(TASKS_FILE, self.tasks)
        save_json(JOURNAL_FILE, self.journal)
        save_json(HEATMAP_FILE, self.heatmap)
        self.after(5000, self._update_ui_loop)

    def _apply_theme(self):
        self.configure(bg=self.theme["bg"])
        messagebox.showinfo("Theme", "Theme changed. The app will restart to fully apply the new theme.")
        os.execv(sys.executable, ['python'] + sys.argv)

# ---------------- run app ----------------
if __name__ == "__main__":
    app = PomodoroApp()
    app.mainloop()
