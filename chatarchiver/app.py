"""The desktop window. One row per account: Connect, then Export.

Tkinter runs on the main thread; all browser work happens on background threads and
reports back through a thread-safe queue that the UI drains on a timer. That keeps the
window responsive while a browser is doing its thing.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import exe_updater, scheduler
from .cookie_fetch import COOKIE_PROVIDERS, WIP_PROVIDER_IDS, session_status, site_url
from .cookie_fetch import export as cookie_export
from .playwright_runner import open_for_login, run_export
from .providers import PROVIDERS
from .sessions import load_config, output_dir_from_config, save_config

_GREY, _AMBER, _GREEN, _RED = "#888888", "#b8860b", "#2e8b22", "#cc0000"

# ChatGPT/Claude sit behind Cloudflare, which blocks automated browsers — so for those we
# use cookie-handoff: you log in in your OWN browser and we replay their APIs with your
# session (see cookie_fetch.py). No Chrome, no Google binary. Other providers (Gemini)
# still go through the Playwright/Firefox path.


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Chat Archiver")
        root.geometry("700x900")
        root.minsize(600, 720)

        cfg = load_config()
        sched = cfg.get("schedule", {})

        self.q: queue.Queue = queue.Queue()
        self.output = tk.StringVar(value=str(output_dir_from_config()))
        self.status_lbls: dict[str, ttk.Label] = {}
        self.busy: set[str] = set()
        self._bar_pulsing = False

        # scheduling controls
        self.freq = tk.StringVar(value=sched.get("frequency", "Off"))
        self.day = tk.StringVar(value=sched.get("day", "Monday"))
        self.time = tk.StringVar(value=sched.get("time", "09:00"))
        self.interval = tk.StringVar(value=str(sched.get("interval", "1")))

        self._build()
        self.root.after(100, self._drain)
        self._refresh_schedule_status()
        # Frozen .exe: quietly check GitHub Releases on launch and offer any newer build.
        # (Source runs already auto-updated via git in run.py before this window opened.)
        if exe_updater.is_frozen():
            self.root.after(1500, lambda: self._check_updates(announce=False))

    # ---- layout ----
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Save Markdown to:").pack(side="left")
        ttk.Entry(top, textvariable=self.output).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Choose…", command=self._choose).pack(side="left")

        ttk.Label(self.root, text="   ChatGPT/Claude: log in in your own browser, then Export "
                                  "(no Chrome, no Google binary).",
                  foreground=_GREY).pack(fill="x", padx=10)

        btns = ttk.Frame(self.root)
        btns.pack(anchor="w", padx=10, pady=(2, 0))
        ttk.Button(btns, text="↻ Refresh sessions",
                   command=self._refresh_sessions).pack(side="left")
        ttk.Button(btns, text="⇩ Check for updates",
                   command=self._check_updates).pack(side="left", padx=6)

        box = ttk.LabelFrame(self.root, text="Accounts")
        box.pack(fill="x", **pad)
        for prov in PROVIDERS.values():
            row = ttk.Frame(box)
            row.pack(fill="x", padx=8, pady=6)
            ttk.Label(row, text=prov.label, width=30).pack(side="left")
            st = ttk.Label(row, text="—", foreground=_GREY, width=16)
            st.pack(side="left")
            self.status_lbls[prov.id] = st
            connect_label = "Log in" if prov.id in COOKIE_PROVIDERS else "Connect"
            ttk.Button(row, text=connect_label,
                       command=lambda p=prov: self._connect(p)).pack(side="left", padx=3)
            ttk.Button(row, text="Export",
                       command=lambda p=prov: self._export(p)).pack(side="left", padx=3)

        self._build_schedule()

        self.prog = ttk.Label(self.root, text="")
        self.prog.pack(fill="x", padx=10, pady=(6, 0))

        self.bar = ttk.Progressbar(self.root, mode="determinate")
        self.bar.pack(fill="x", padx=10, pady=(2, 6))

        self.log = tk.Text(self.root, height=12, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, **pad)
        self._log("ChatGPT/Claude: click 'Log in' (opens the site in your browser), sign in, "
                  "then 'Export'. No re-login needed once your browser has the session.")

        # run.py pulls the latest source on launch and leaves the outcome here; show it once.
        upd = os.environ.pop("CHATARCHIVER_UPDATE_MSG", "")
        if upd:
            self._log(f"[update] {upd}")

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

    def _persist_output(self) -> None:
        cfg = load_config()
        cfg["output_dir"] = self.output.get()
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
                elif kind == "bar":
                    done, total = payload
                    if total is None:                 # working, count not known yet → pulse
                        if not self._bar_pulsing:
                            self.bar.configure(mode="indeterminate")
                            self.bar.start(12)
                            self._bar_pulsing = True
                    else:
                        if self._bar_pulsing:
                            self.bar.stop()
                            self._bar_pulsing = False
                        self.bar.configure(mode="determinate", maximum=max(total, 1))
                        self.bar["value"] = done
                elif kind == "status":
                    pid, text, color = payload
                    self.status_lbls[pid].configure(text=text, foreground=color)
                elif kind == "sched":
                    text, color = payload
                    self.sched_lbl.configure(text=text, foreground=color)
                elif kind == "ask_restart":
                    self._prompt_restart(payload)
                elif kind == "ask_exe_update":
                    self._prompt_exe_update(*payload)
                elif kind == "apply_exe_update":
                    self._apply_exe_update(payload)
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
    def _refresh_sessions(self) -> None:
        """Flush cookies (WAL checkpoint) and re-check each provider's live session."""
        def work():
            self._post("log", "[refresh] Flushing cookies and re-checking sessions…")
            colors = {"ok": _GREEN, "stale": _AMBER, "out": _GREY, "error": _RED}
            try:
                st = session_status()
            except Exception as e:
                self._post("log", f"[refresh] ERROR: {e}")
                return
            for pid, (state, short, detail) in st.items():
                self._post("status", (pid, short, colors.get(state, _GREY)))
                if detail:
                    self._post("log", f"[{pid}] {detail}")
            self._post("log", "[refresh] Done.")

        threading.Thread(target=work, daemon=True).start()

    def _check_updates(self, announce: bool = True) -> None:
        """Check for a newer version. Two code paths depending on how we're running:

          • frozen .exe  -> compare against the latest GitHub Release; if newer, offer to
                            download it and swap the install in place (exe_updater).
          • from source  -> git fast-forward this checkout (updater.self_update).

        Source launches also auto-update via run.py; the frozen build auto-checks on launch
        (see __init__). `announce=False` keeps the background check quiet unless it finds one."""
        def work():
            if announce:
                self._post("log", "[update] Checking GitHub for a newer version…")
            if exe_updater.is_frozen():
                try:
                    avail, ver, url = exe_updater.check()
                except Exception as e:
                    if announce:
                        self._post("log", f"[update] check failed: {e}")
                    return
                if not avail:
                    if announce:
                        self._post("log", f"[update] up to date "
                                          f"(v{exe_updater.current_version()}).")
                    return
                self._post("log", f"[update] version {ver} is available.")
                self._post("ask_exe_update", (ver, url))
                return
            # running from source: git fast-forward
            try:
                from .updater import is_git_checkout, self_update
            except Exception as e:
                if announce:
                    self._post("log", f"[update] unavailable: {e}")
                return
            if not is_git_checkout():
                if announce:
                    self._post("log", "[update] not a git checkout or packaged build — "
                                      "nothing to update.")
                return
            try:
                updated, msg = self_update(log=lambda m: self._post("log", m))
            except Exception as e:
                self._post("log", f"[update] error: {e}")
                return
            if announce:
                self._post("log", f"[update] {msg}" if msg else "[update] already up to date.")
            if updated:
                self._post("ask_restart", msg)

        threading.Thread(target=work, daemon=True).start()

    def _prompt_restart(self, msg: str) -> None:
        """Ask (on the main thread) whether to relaunch so the pulled code takes effect."""
        if messagebox.askyesno("Update applied",
                               f"Chat Archiver was {msg}.\n\nRestart now to use the new "
                               "version?"):
            os.environ["CHATARCHIVER_NO_UPDATE"] = "1"   # just pulled; don't re-check on boot
            os.environ.pop("CHATARCHIVER_UPDATED", "")
            self.root.destroy()
            os.execv(sys.executable, [sys.executable, *sys.argv])

    def _prompt_exe_update(self, ver: str, url: str) -> None:
        """Frozen build: confirm, then download in the background and hand off to the swap
        helper. We only download after the user agrees, so launch stays fast."""
        if not messagebox.askyesno("Update available",
                                   f"Version {ver} is available "
                                   f"(you have {exe_updater.current_version()}).\n\n"
                                   "Download and install now? The app will close and reopen."):
            return

        def work():
            try:
                new_dir = exe_updater.download_and_stage(url, lambda m: self._post("log", m))
            except Exception as e:
                self._post("log", f"[update] download failed: {e}")
                return
            self._post("apply_exe_update", str(new_dir))

        threading.Thread(target=work, daemon=True).start()

    def _apply_exe_update(self, new_dir: str) -> None:
        """Spawn the detached swap helper, then quit so it can overwrite the running exe."""
        try:
            exe_updater.apply(Path(new_dir), lambda m: self._post("log", m))
        except Exception as e:
            self._post("log", f"[update] install failed: {e}")
            return
        self.root.after(400, self.root.destroy)   # let the log line render, then exit

    def _connect(self, prov) -> None:
        if prov.id in self.busy:
            return
        if prov.id in COOKIE_PROVIDERS:
            return self._connect_cookie(prov)
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

    def _connect_cookie(self, prov) -> None:
        """Cookie-handoff 'Log in': open the site in the user's own browser, then check
        whether a live session is now readable from it."""
        self.busy.add(prov.id)

        def work():
            self._post("status", (prov.id, "opening site…", _AMBER))
            try:
                webbrowser.open(site_url(prov.id))
            except Exception:
                pass
            self._post("log", f"[{prov.label}] Opened {prov.id} in your browser — "
                              f"log in there if needed, then click Export.")
            if prov.id == "gemini" or prov.id in WIP_PROVIDER_IDS:
                # Gemini check needs a headless browser; WIP providers have no exporter yet.
                # Either way, just confirm the session when you Export.
                label = "WIP — log in, then Export" if prov.id in WIP_PROVIDER_IDS \
                    else "log in, then Export"
                self._post("status", (prov.id, label, _GREY))
                self.busy.discard(prov.id)
                return
            res = cookie_export(providers=(prov.id,), write=False,
                                log=lambda m: self._post("log", m))
            self.busy.discard(prov.id)
            r = res.get(prov.id) or {}
            if "error" in r or not r:
                self._post("status", (prov.id, "log in, then Export", _GREY))
            else:
                self._post("status", (prov.id, "session found ✓", _GREEN))

        threading.Thread(target=work, daemon=True).start()

    def _export(self, prov) -> None:
        if prov.id in self.busy:
            return
        if prov.id in COOKIE_PROVIDERS:
            return self._export_cookie(prov)
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

    def _export_cookie(self, prov) -> None:
        """Cookie-handoff export: read the user's browser session, replay the API."""
        self.busy.add(prov.id)
        self._persist_output()
        out = self.output.get()

        def work():
            if prov.id in WIP_PROVIDER_IDS:
                self._post("status", (prov.id, "WIP", _AMBER))
                cookie_export(providers=(prov.id,), write=False,
                              log=lambda m: self._post("log", m))
                self._post("status", (prov.id, "WIP — not wired yet", _GREY))
                self.busy.discard(prov.id)
                return
            self._post("status", (prov.id, "exporting…", _AMBER))
            self._post("prog", f"{prov.label}: reading your browser session…")
            self._post("bar", (0, None))               # pulse until the count is known

            def progress(done, total, title):
                self._post("bar", (done, total))
                self._post("prog", f"{prov.label}: {done}/{total} — {title[:42]}")

            res = cookie_export(providers=(prov.id,), out_dir=out, write=True,
                                log=lambda m: self._post("log", m), progress=progress)
            self.busy.discard(prov.id)
            r = res.get(prov.id) or {}
            if "error" in r:
                self._post("status", (prov.id, "error", _RED))
                self._post("prog", "")
                self._post("bar", (0, 1))              # reset
                return
            summary = (f"{r.get('new', 0)} new, {r.get('updated', 0)} updated, "
                       f"{r.get('unchanged', 0)} unchanged"
                       + (f", {r['failed']} failed" if r.get("failed") else ""))
            total = r.get("total", 0) or 1
            self._post("bar", (total, total))          # fill to 100%
            self._post("status", (prov.id, "done ✓", _GREEN))
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
