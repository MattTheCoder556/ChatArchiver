"""The desktop window. One row per account: Connect, then Export.

Tkinter runs on the main thread; all browser work happens on background threads and
reports back through a thread-safe queue that the UI drains on a timer. That keeps the
window responsive while a browser is doing its thing.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import scheduler
from .playwright_runner import open_for_login, run_export
from .providers import PROVIDERS
from .sessions import load_config, output_dir_from_config, save_config

_GREY, _AMBER, _GREEN, _RED = "#888888", "#b8860b", "#2e8b22", "#cc0000"

# Browser must be Chromium-based (the captcha-beating drives Chromium via CDP).
_NON_CHROMIUM = ("firefox", "waterfox", "librewolf", "palemoon", "seamonkey",
                 "safari", "iexplore", "torbrowser", "tor browser")
_CHROMIUM = ("chrome", "msedge", "edge", "brave", "vivaldi", "opera", "operagx",
             "chromium", "thorium", "ungoogled", "arc")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Chat Archiver")
        root.geometry("680x640")
        root.minsize(580, 560)

        cfg = load_config()
        sched = cfg.get("schedule", {})

        self.q: queue.Queue = queue.Queue()
        self.output = tk.StringVar(value=str(output_dir_from_config()))
        self.browser_path = tk.StringVar(value=cfg.get("browser_path", ""))
        self.status_lbls: dict[str, ttk.Label] = {}
        self.busy: set[str] = set()

        # scheduling controls
        self.freq = tk.StringVar(value=sched.get("frequency", "Off"))
        self.day = tk.StringVar(value=sched.get("day", "Monday"))
        self.time = tk.StringVar(value=sched.get("time", "09:00"))
        self.interval = tk.StringVar(value=str(sched.get("interval", "1")))

        self._build()
        self.root.after(100, self._drain)
        self._refresh_schedule_status()

    # ---- layout ----
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Save Markdown to:").pack(side="left")
        ttk.Entry(top, textvariable=self.output).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Choose…", command=self._choose).pack(side="left")

        brow = ttk.Frame(self.root)
        brow.pack(fill="x", padx=10)
        ttk.Label(brow, text="Browser (optional):").pack(side="left")
        ttk.Entry(brow, textvariable=self.browser_path).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(brow, text="Browse…", command=self._choose_browser).pack(side="left")
        ttk.Button(brow, text="Clear", command=self._clear_browser).pack(side="left", padx=(3, 0))
        ttk.Label(self.root, text="   Leave blank to auto-detect Chrome or Edge. "
                                  "Or pick any Chromium browser (Brave, Vivaldi, Opera).",
                  foreground=_GREY).pack(fill="x", padx=10)

        box = ttk.LabelFrame(self.root, text="Accounts")
        box.pack(fill="x", **pad)
        for prov in PROVIDERS.values():
            row = ttk.Frame(box)
            row.pack(fill="x", padx=8, pady=6)
            ttk.Label(row, text=prov.label, width=30).pack(side="left")
            st = ttk.Label(row, text="not connected", foreground=_GREY, width=14)
            st.pack(side="left")
            self.status_lbls[prov.id] = st
            ttk.Button(row, text="Connect",
                       command=lambda p=prov: self._connect(p)).pack(side="left", padx=3)
            ttk.Button(row, text="Export",
                       command=lambda p=prov: self._export(p)).pack(side="left", padx=3)

        self._build_schedule()

        self.prog = ttk.Label(self.root, text="")
        self.prog.pack(fill="x", **pad)

        self.log = tk.Text(self.root, height=12, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, **pad)
        self._log("Step 1: Connect an account (log in once). Step 2: Export.")

    def _build_schedule(self) -> None:
        box = ttk.LabelFrame(self.root, text="Automatic export (runs in the background)")
        box.pack(fill="x", padx=10, pady=6)

        row = ttk.Frame(box)
        row.pack(fill="x", padx=8, pady=6)

        # All widgets live in one grid; _update_schedule_fields shows/hides per frequency.
        ttk.Label(row, text="Run:").grid(row=0, column=0, sticky="w")
        ttk.OptionMenu(row, self.freq, self.freq.get(), "Off", "Hourly", "Daily", "Weekly",
                       command=lambda *_: self._update_schedule_fields()
                       ).grid(row=0, column=1, padx=4)

        self.w_every = ttk.Label(row, text="every")
        self.w_interval = ttk.Spinbox(row, from_=1, to=99, width=4, textvariable=self.interval)
        self.w_unit = ttk.Label(row, text="days")
        self.w_on = ttk.Label(row, text="on")
        self.w_day = ttk.OptionMenu(row, self.day, self.day.get(), *scheduler.DAY_NAMES)
        self.w_at = ttk.Label(row, text="at")
        self.w_time = ttk.Entry(row, textvariable=self.time, width=7)

        self.w_every.grid(row=0, column=2, padx=(8, 2))
        self.w_interval.grid(row=0, column=3)
        self.w_unit.grid(row=0, column=4, padx=(2, 0))
        self.w_on.grid(row=0, column=5, padx=(8, 2))
        self.w_day.grid(row=0, column=6)
        self.w_at.grid(row=0, column=7, padx=(8, 2))
        self.w_time.grid(row=0, column=8)
        ttk.Button(row, text="Apply", command=self._apply_schedule).grid(row=0, column=9, padx=10)

        self.sched_lbl = ttk.Label(box, text="", foreground=_GREY)
        self.sched_lbl.pack(fill="x", padx=8, pady=(0, 6))
        self._update_schedule_fields()

    def _update_schedule_fields(self) -> None:
        """Show only the controls relevant to the chosen frequency."""
        freq = self.freq.get()
        every = {"Hourly", "Daily"}                  # show 'every N'
        timed = {"Daily", "Weekly"}                  # show 'at HH:MM'
        weekly = freq == "Weekly"                     # show 'on <day>'
        self.w_unit.configure(text="hours" if freq == "Hourly" else "days")

        def vis(widget, shown):
            widget.grid() if shown else widget.grid_remove()

        vis(self.w_every, freq in every)
        vis(self.w_interval, freq in every)
        vis(self.w_unit, freq in every)
        vis(self.w_on, weekly)
        vis(self.w_day, weekly)
        vis(self.w_at, freq in timed)
        vis(self.w_time, freq in timed)

    # ---- helpers (main thread only) ----
    def _choose(self) -> None:
        d = filedialog.askdirectory(initialdir=self.output.get() or str(Path.home()))
        if d:
            self.output.set(d)
            self._persist_output()

    def _choose_browser(self) -> None:
        f = filedialog.askopenfilename(
            title="Pick a Chromium-based browser (.exe)",
            initialdir=r"C:\Program Files",
            filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
        if not f:
            return
        name = Path(f).name.lower()
        if any(b in name for b in _NON_CHROMIUM):
            messagebox.showerror(
                "Not a Chromium browser",
                f"{Path(f).name} isn't Chromium-based, so the app can't control it — "
                "that's why it opened but the page never loaded.\n\n"
                "Pick a Chromium-based browser instead: Chrome, Edge, Brave, Vivaldi or "
                "Opera. (Or use Clear to auto-detect Chrome/Edge.)")
            return
        self.browser_path.set(f)
        self._persist_output()
        note = "" if any(b in name for b in _CHROMIUM) else \
            "  — heads up: if this isn't Chromium-based, captchas will fail"
        self._log(f"[browser] Will use: {f}{note}")

    def _clear_browser(self) -> None:
        self.browser_path.set("")
        self._persist_output()
        self._log("[browser] Cleared — back to auto-detect (Chrome / Edge).")

    def _persist_output(self) -> None:
        cfg = load_config()
        cfg["output_dir"] = self.output.get()
        cfg["browser_path"] = self.browser_path.get().strip()
        save_config(cfg)

    def _log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "prog":
                    self.prog.configure(text=payload)
                elif kind == "status":
                    pid, text, color = payload
                    self.status_lbls[pid].configure(text=text, foreground=color)
                elif kind == "sched":
                    text, color = payload
                    self.sched_lbl.configure(text=text, foreground=color)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _post(self, kind: str, payload) -> None:
        self.q.put((kind, payload))

    # ---- scheduling ----
    def _apply_schedule(self) -> None:
        freq, day, time_ = self.freq.get(), self.day.get(), self.time.get()
        try:
            n = max(1, int(self.interval.get()))
        except Exception:
            n = 1
        cfg = load_config()
        cfg["schedule"] = {"frequency": freq, "day": day, "time": time_, "interval": n}
        cfg["output_dir"] = self.output.get()      # the scheduled run reads this
        cfg["browser_path"] = self.browser_path.get().strip()
        save_config(cfg)

        def work():
            try:
                if freq == "Off":
                    scheduler.clear_schedule()
                    self._post("sched", ("Automatic export is off.", _GREY))
                    self._post("log", "[schedule] Automatic export turned off.")
                else:
                    scheduler.set_schedule(freq, day, time_, n)
                    when = self._describe_when(freq, day, time_, n)
                    self._post("sched", (f"Scheduled: {when}.", _GREEN))
                    self._post("log", f"[schedule] Will export {when} (background).")
            except Exception as e:
                self._post("sched", (f"Couldn't set schedule: {e}", _RED))
                self._post("log", f"[schedule] ERROR: {e}")

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _describe_when(freq: str, day: str, time_: str, n: int) -> str:
        if freq == "Hourly":
            return "every hour" if n == 1 else f"every {n} hours"
        if freq == "Daily":
            return f"every day at {time_}" if n == 1 else f"every {n} days at {time_}"
        return f"every {day} at {time_}" if n == 1 else f"every {n} weeks on {day} at {time_}"

    def _refresh_schedule_status(self) -> None:
        def work():
            try:
                st = scheduler.status()
            except Exception:
                return
            if st.get("scheduled"):
                nxt = st.get("Next Run Time", "")
                self._post("sched", (f"Scheduled ✓  next run: {nxt}".strip(), _GREEN))
            else:
                self._post("sched", ("Automatic export is off.", _GREY))

        threading.Thread(target=work, daemon=True).start()

    # ---- actions (spawn worker threads) ----
    def _connect(self, prov) -> None:
        if prov.id in self.busy:
            return
        self.busy.add(prov.id)

        def work():
            self._post("status", (prov.id, "opening…", _AMBER))
            self._post("log", f"[{prov.label}] Opening a browser — log in, then wait.")
            stop = threading.Event()
            try:
                ok = open_for_login(prov, lambda m: self._post("log", f"[{prov.label}] {m}"), stop)
            except Exception as e:
                self._post("log", f"[{prov.label}] ERROR: {e}")
                self._post("status", (prov.id, "error", _RED))
                return
            finally:
                self.busy.discard(prov.id)
            if ok:
                self._post("status", (prov.id, "connected ✓", _GREEN))
                self._post("log", f"[{prov.label}] Connected. You can Export now.")
            else:
                self._post("status", (prov.id, "not connected", _GREY))
                self._post("log", f"[{prov.label}] No login detected (timed out).")

        threading.Thread(target=work, daemon=True).start()

    def _export(self, prov) -> None:
        if prov.id in self.busy:
            return
        self.busy.add(prov.id)
        self._persist_output()
        out = Path(self.output.get())

        def work():
            self._post("status", (prov.id, "exporting…", _AMBER))
            self._post("prog", f"{prov.label}: starting in the background…")

            def progress(i, total, title):
                self._post("prog", f"{prov.label}: {i}/{total} — {title[:48]}")

            try:
                s = run_export(prov, out, lambda m: self._post("log", m), progress)
            except NotImplementedError as e:
                self._post("log", f"[{prov.label}] {e}")
                self._post("status", (prov.id, "unsupported", _GREY))
                self._post("prog", "")
                return
            except Exception as e:
                self._post("log", f"[{prov.label}] ERROR: {e}")
                self._post("status", (prov.id, "error", _RED))
                self._post("prog", "")
                return
            finally:
                self.busy.discard(prov.id)

            summary = (f"{s['new']} new, {s['updated']} updated, {s['unchanged']} unchanged"
                       + (f", {s['failed']} failed" if s.get("failed") else ""))
            self._post("log", f"[{prov.label}] Done — {summary} (of {s['total']}) → {s['out_dir']}")
            self._post("status", (prov.id, "connected ✓", _GREEN))
            self._post("prog", f"{prov.label}: {summary}")

        threading.Thread(target=work, daemon=True).start()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()
