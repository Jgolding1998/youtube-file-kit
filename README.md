# YouTube File Kit

A small local web app: paste one YouTube link, press **Download**, and your browser saves the video file. It runs only on your computer at `http://127.0.0.1:8765`. There is no account, no API key, no telemetry and no hosted version.

It is a thin, locked-down front end for [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## What it does and does not do

- Accepts one link at a time: `youtube.com/watch?v=...`, `youtu.be/...` or `youtube.com/shorts/...`.
- Saves public videos that YouTube serves to an anonymous visitor.
- Does **not** handle playlists, channels, live streams, premieres, or other sites.
- Does **not** sign in, read browser cookies or `.netrc`, or try to get around private, members-only, age-restricted, region-locked or DRM-protected videos. Those show a plain message instead.
- YouTube changes often. Some public videos may still fail, depending on your network and YouTube's checks. Updating to a newer yt-dlp release is the usual fix.

Only save videos you have the right to save, and follow YouTube's Terms of Service.

## Requirements

- Python 3.11 or newer.
- Optional: [FFmpeg](https://ffmpeg.org/) on `PATH`. With it, the app can join separate video and audio streams (up to 1080p MP4). Without it, only single-file formats are used, which on YouTube are often 360p.
- Recommended: [Deno](https://deno.com/) 2.3 or newer on `PATH`. yt-dlp needs a JavaScript runtime for full YouTube support. Without one, YouTube may offer fewer formats or none. See [JavaScript runtime](#javascript-runtime).

## Start

Windows (PowerShell):

```powershell
.\run.ps1
```

macOS or Linux:

```sh
./run.sh
```

The first run creates a `.venv` folder and installs the exact pinned packages from `requirements.txt` with hash checking. Later runs refresh the packages when that file changes. The app then opens in your browser. Press Ctrl+C in the terminal to stop it.

Manual equivalent:

```sh
python -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.txt   # Windows: .venv\Scripts\python.exe
.venv/bin/python -m youtube_file_kit
```

Options (all optional):

| Flag | Default | Meaning |
| --- | --- | --- |
| `--port N` | 8765 | Local port |
| `--no-browser` | off | Do not open a browser tab |
| `--max-minutes N` | 180 | Longest video allowed |
| `--max-mb N` | 2048 | Largest file allowed |
| `--no-ffmpeg` | off | Single-file formats only |
| `--no-deno` | off | Do not let yt-dlp use Deno |

## How it works

1. The link is parsed strictly and rebuilt as `https://www.youtube.com/watch?v=<id>`. Anything else is rejected before yt-dlp runs.
2. yt-dlp runs as a separate process with a fixed command line, no shell, a minimal environment, its own empty home and temp folders, a time limit, a size limit and a limit of two downloads at once (four more can wait).
3. The finished file is offered to your browser with a safe name made from the video title. Files stay in a private temp folder for 30 minutes (at most 8 kept), and the folder is deleted when the app stops.

The yt-dlp command line always includes `--ignore-config`, `--no-plugin-dirs`, `--no-remote-components`, `--no-cookies`, `--no-cookies-from-browser`, `--no-exec`, `--no-batch-file`, `--no-cache-dir`, `--no-playlist`, `--use-extractors youtube`, `--downloader native` and `--proxy ""` (direct connection; system proxies are not used). See [SECURITY.md](SECURITY.md) for the full list of controls.

### JavaScript runtime

Current yt-dlp solves YouTube's JavaScript challenges with an external runtime plus the `yt-dlp-ejs` scripts. This app:

- uses Deno only, found on `PATH` when the app starts (`--no-deno` turns it off);
- uses the challenge scripts from the pinned `yt-dlp-ejs` package, and passes `--no-remote-components` so nothing is fetched from npm or GitHub at run time;
- relies on yt-dlp running Deno with no permissions (`--no-prompt --no-remote --no-npm --cached-only`, no `--allow-*` flags), so the script cannot use the network or files;
- gives Deno a throwaway cache folder and turns off its update check.

## Tests

```sh
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
python -m pytest -q
```

The tests use a fake yt-dlp program that exercises the real process runner, environment, limits and error handling. They never contact YouTube.

An optional single real request downloads yt-dlp's own public test video, capped at 10 MB and 90 seconds:

```sh
YFK_REAL_NETWORK=1 python -m pytest -q -s tests/test_real_network.py
```

When this was checked on 2026-10-29 with yt-dlp 2026.10.26, Deno 2.9.1 and no cookies, YouTube answered "This video is unavailable" for that test video from the development machine. The app reported this as a plain message. Results depend on YouTube and your network.

Browser check (needs Node.js, Playwright and Chrome; all traffic outside the app is blocked):

```sh
PYTHON=.venv/bin/python PLAYWRIGHT_MODULE=/path/to/node_modules/playwright CHROME_PATH=/path/to/chrome node tests/browser/ui_check.cjs
```

## Third-party software

Installed by pip, not copied into this repository: yt-dlp (Unlicense), yt-dlp-ejs (Unlicense, MIT and ISC), Starlette (BSD-3-Clause), Uvicorn (BSD-3-Clause), AnyIO (MIT), h11 (MIT), Click (BSD-3-Clause), idna (BSD-3-Clause), typing_extensions (PSF-2.0). FFmpeg and Deno are optional programs you install yourself, under their own licenses.

## Feedback

[Send feedback](https://github.com/Jgolding1998/Jgolding1998/issues/new?template=feedback.yml)

## License

MIT, see [LICENSE](LICENSE).
