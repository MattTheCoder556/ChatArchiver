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

## Setup (one time)

You need Python 3.10+ installed. Then, in this folder:

```
install.bat       (double-click — installs dependencies + the browser)
```

or manually:

```
pip install -r requirements.txt
python -m playwright install chromium
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
  Windows). You get `dist/ChatArchiver/ChatArchiver`. Note: automatic scheduling is
  Windows-only for now (it uses Task Scheduler); everything else works.
- The build still expects **Google Chrome** to be installed on the target machine (the app
  drives real Chrome to beat captchas).

## Automatic export (scheduled)

The **Automatic export** section in the window registers a Windows scheduled task that
runs the exporter in the background — even when the app is closed.

- Pick a frequency, then **Apply**. (Choose **Off** + Apply to cancel.) The controls
  change to match:
  - **Hourly** — `every [N] hours`
  - **Daily** — `every [N] days at [HH:MM]`
  - **Weekly** — `on [weekday] at [HH:MM]`
- At the scheduled time the headless exporter runs every connected account incrementally
  (no window appears) and writes a log to `~/.chatarchiver/logs/`.
- It runs while you're logged into Windows; no admin rights needed. If a login has
  expired it logs "needs Connect" and skips that account (it won't pop a window
  unattended) — just open the app and Connect again.

Under the hood this is a `schtasks` task named `ChatArchiverExport` that launches
`headless_export.py` via `pythonw`. You can also run a manual headless export any time:

```
python headless_export.py
```

## Which browser it uses

The app drives a **Chromium-based** browser (that's what the captcha-beating relies on —
Firefox/Safari use different automation and would hit captchas). It picks one in this
order:

1. A **custom browser** if you set one (the *Browser (optional)* box) — point it at any
   Chromium browser's `.exe`: Brave, Vivaldi, Opera, a portable Chromium, etc.
2. **Google Chrome** (auto-detected)
3. **Microsoft Edge** (auto-detected; preinstalled on Windows 11)
4. Bundled Chromium (only if you ran `patchright install chromium`)

Leave the Browser box blank to just auto-detect Chrome/Edge. The setting is saved and is
also used by scheduled background runs.

## Provider status

| Provider | Export | Notes |
|----------|--------|-------|
| ChatGPT  | ✅ | Uses chatgpt.com's own backend API via your logged-in session. |
| Claude   | ✅ | Uses claude.ai's own API via your logged-in session. |
| Gemini   | 🧪 | Experimental DOM scraper (Google has no API). Login via Patchright works; export reads the rendered page, so selectors may need occasional tuning — a run that saves 0 means the sidebar markup changed. |

Adding Copilot / DeepSeek / Perplexity / Grok is one new file in `chatarchiver/providers/`.

## How it works / where things live

- `chatarchiver/app.py` — the Tkinter window.
- `chatarchiver/playwright_runner.py` — drives Chromium; login + export.
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
- The export browser window is **visible on purpose** — ChatGPT's bot protection is much
  more reliable against a real window than a hidden one. Let it do its thing.
- Not yet validated against live accounts on this machine — the first real run may surface
  a field-name tweak in a provider file.
