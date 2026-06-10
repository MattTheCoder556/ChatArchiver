# Chat Archiver

A small desktop app that saves your **ChatGPT** and **Claude** chat history to plain
**Markdown** files on your computer. You log in once in a normal browser window; the app
remembers the session and never stores your password.

## What it does

- One window, one row per account: **Connect**, then **Export**.
- **Connect** opens a real, **visible** browser. You log in (your normal MFA/captcha
  works). When the app sees you're logged in, it closes the window and remembers the
  session. (If you're already logged in from before, it connects instantly.)
- **Export** runs in the **background** (headless) using the saved session — no window
  pops up unless the background session needs attention, in which case it opens one and
  retries. It's **incremental**: only new or changed conversations are written; unchanged
  ones are skipped. The log reports e.g. `3 new, 1 updated, 40 unchanged`. Output is one
  `.md` file per chat in a folder you choose:

  ```
  Chat Archive/
    chatgpt/2025-11-03_trip-planning-ideas_a1b2c3d4.md
    claude/2025-10-19_resume-feedback_9f8e7d6c.md
  ```

  Each file has YAML front-matter (title, dates, ids) and the full transcript as
  `## You` / `## Assistant` sections.

## Recommended: cookie-handoff (gets past Cloudflare)

ChatGPT and Claude sit behind Cloudflare, which blocks *automated* browsers (including
Playwright's Firefox — there's no stealth layer for it). The reliable way in is to **not
automate a browser at all**: stay logged into ChatGPT/Claude in your **normal everyday
browser**, and let the tool replay the sites' own APIs with your session, using a TLS
client that impersonates a real browser (so the `cf_clearance` you already earned still
applies). No automated browser, no Google binary.

```
pip install -r requirements.txt
python cookie_export.py --check     # verify it can reach your accounts (writes nothing)
python cookie_export.py             # export ChatGPT + Claude to Markdown (incremental)
```

- Auto-detects which of your browsers holds a live session (`--browser firefox|chrome|…`
  to force one). Snap/native/flatpak Firefox profiles are all found.
- `--providers claude` to do just one; `--out "~/Chat Archive"` to choose a folder.
- **Gemini** also runs without a login window (`--providers gemini`): it injects your real
  Google cookies into a headless browser and scrapes the page (no JSON API exists). This
  is **best-effort** — Gemini's sidebar is a virtualized, obfuscated Angular list, so a run
  may capture everything or, on a bad render, little/nothing. For a guaranteed copy of
  Gemini history use [Google Takeout](https://takeout.google.com) (My Activity → Gemini).

The Playwright/Firefox GUI below still works for providers that aren't behind a hard
Cloudflare challenge, but cookie-handoff is the path that just works for ChatGPT/Claude.

## Setup (one time)

You need Python 3.10+ installed. Then, in this folder:

```
install.bat       (double-click — installs dependencies + the browser)
```

or manually:

```
pip install -r requirements.txt
python -m playwright install firefox
```

## Run

```
start.bat         (double-click)
```

or `python run.py`.

## Build a double-click app (.exe)

To make a standalone Windows app that doesn't need Python:

```
build_exe.bat     (double-click — takes a few minutes)
```

This produces `dist\ChatArchiver\ChatArchiver.exe`. Copy the whole `dist\ChatArchiver`
folder anywhere and double-click the exe — no Python install required. The same exe runs
the scheduled background export (it calls itself with `--export`), so scheduling keeps
working from the packaged app.

- **Linux:** run `build_app.sh` *on a Linux machine* (PyInstaller can't cross-compile from
  Windows). You get `dist/ChatArchiver/ChatArchiver`. Automatic scheduling works here too
  (via a systemd **user** timer — see below).
- The packaged app does **not** bundle the Firefox browser — run
  `python -m playwright install firefox` once on the target machine (it caches Firefox
  under `~/.cache/ms-playwright`). No Google Chrome required, ever.

## Automatic export (scheduled)

The **Automatic export** section in the window registers an OS-level background task that
runs the exporter — **even when the app window is closed**. Works on both Windows and
Linux.

- Pick a frequency, then **Apply**. (Choose **Off** + Apply to cancel.) The controls
  change to match:
  - **Hourly** — `every [N] hours`
  - **Daily** — `every [N] days at [HH:MM]`
  - **Weekly** — `on [weekday] at [HH:MM]`
- At the scheduled time the headless exporter runs every connected account incrementally
  (no window appears) and writes a log to `~/.chatarchiver/logs/`.
- No admin/root needed. If a login has expired it logs the account as needing a re-login
  and skips it (it won't pop a window unattended) — just open the app and Connect again.

Under the hood:

- **Windows** — a `schtasks` task named `ChatArchiverExport` that launches
  `headless_export.py` via `pythonw` (no console window).
- **Linux** — a **systemd user timer** at
  `~/.config/systemd/user/chatarchiver-export.{service,timer}` (`Persistent=true`, so a
  missed run fires at next boot). Inspect it with
  `systemctl --user list-timers chatarchiver-export.timer`. It runs while you're logged
  in; to keep it firing when you're not, enable lingering once:
  `loginctl enable-linger $USER`. (`every N days`/`every N weeks` are approximated to the
  nearest systemd `OnCalendar` form — hourly, daily, and weekly-on-a-weekday are exact.)

You can also run a manual headless export any time:

```
python headless_export.py
```

## Closing the window keeps it running (system tray)

The app lives in the **system tray** (Windows notification area / Linux status tray).
**Closing the window doesn't quit** — it hides to the tray and scheduled exports keep
happening. The tray icon's menu lets you **Open** the window again, **Run export now**, or
**Quit** for real. (If a machine has no usable tray, closing the window simply quits, as
before.)

## Which browser it uses

The app drives **Mozilla Firefox** — specifically Playwright's own pinned Firefox build
(`python -m playwright install firefox`). That's a deliberate choice: **no Google Chrome,
no Chromium, no Google binary or telemetry anywhere in the loop.** There's nothing to
pick — the engine is fixed, and the same Firefox is used by scheduled background runs.

> Why not Chrome? Chrome's automation channel (CDP) is what older versions of this app
> used to beat captchas, but it meant launching Google's browser. Firefox keeps the whole
> pipeline off Google. The trade-off: bot-checks (e.g. Cloudflare on ChatGPT) are tuned
> against *headless* automation. We sidestep that by having you **log in once in a visible
> Firefox window** — a real human login — after which the headless export just reuses that
> session. If a headless run ever gets challenged, the app reopens a visible window.

## Provider status

| Provider | Export | Notes |
|----------|--------|-------|
| ChatGPT  | ✅ | Cookie-handoff: replays chatgpt.com's backend API with your browser session. |
| Claude   | ✅ | Cookie-handoff: replays claude.ai's API with your browser session. |
| Gemini   | 🧪 | Injects your Google cookies into headless Firefox and scrapes the DOM (no API). Best-effort — a run may capture everything or, on a bad render, little. |
| Grok     | ✅ | Cookie-handoff: replays grok.com's REST API (`/rest/app-chat/conversations` + `/{id}/responses`) with your browser session. |
| DeepSeek, Mistral (Le Chat), Perplexity, Poe, Copilot | 🚧 WIP | Wired into the UI (rows + "Log in" + login detection), but the per-service list/fetch endpoints aren't implemented yet. Export reports WIP instead of faking success. Each becomes ✅ once its endpoints are implemented + tested against a live login. (DeepSeek's auth lives in localStorage, not cookies — hardest of the set.) |

Adding Copilot / DeepSeek / Perplexity / Grok is one new file in `chatarchiver/providers/`.

## How it works / where things live

- `chatarchiver/app.py` — the Tkinter window.
- `chatarchiver/playwright_runner.py` — drives Firefox; login + export.
- `chatarchiver/providers/` — one file per chat service. **The site-specific, brittle
  bits live here** — if an export breaks because a site changed, this is what to fix.
- `chatarchiver/markdown_writer.py` — conversation → Markdown file.
- Saved logins live in `~/.chatarchiver/profiles/<provider>/` (a browser profile). Delete
  that folder to "log out".
- Incremental state lives in `~/.chatarchiver/manifests/<provider>.json` — it records each
  exported conversation's change-marker (its `updated_at` for ChatGPT/Claude, or a content
  hash for Gemini, which exposes no timestamp). Delete it to force a full re-export.

## Honest caveats

- These services have **no official export API**, so the app talks to the same private
  endpoints their own websites use. They can change without notice and break an exporter;
  the fix is localised to one provider file.
- The **login** window is visible on purpose — a real human sign-in is what gets past
  bot protection (Cloudflare etc.). The **export** then runs headless on that saved
  session; if it gets challenged it transparently reopens a visible window.
- Firefox (not Chrome) is a recent switch to keep Google out of the loop. ChatGPT sits
  behind Cloudflare, so if a headless export starts returning 0/auth errors, just open the
  app and **Connect** again to refresh the session.
- Not yet validated against live accounts on this machine — the first real run may surface
  a field-name tweak in a provider file.
