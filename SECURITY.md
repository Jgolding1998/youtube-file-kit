# Security

YouTube File Kit is a local, single-user app. It is meant to be safe to leave running while you browse other sites, and to run yt-dlp with as little reach as practical. It is not an operating-system sandbox.

## Reporting

Please use GitHub's private vulnerability reporting on this repository if it is enabled. Otherwise open an issue that describes the problem without exploit details, or use the [feedback form](https://github.com/Jgolding1998/Jgolding1998/issues/new?template=feedback.yml).

## Controls

Web server

- Listens on `127.0.0.1` only. Requests must carry `Host: 127.0.0.1:<port>` or `localhost:<port>`, which blocks DNS rebinding.
- Any supplied `Origin` must match the request's local Host. Every non-GET request also needs that Origin and a random per-run `X-CSRF-Token`. Duplicate security headers and cross-site `Sec-Fetch-Site` values are refused.
- Request bodies need a declared length and are capped at 4 KB. Bodies longer than declared are cut off.
- Strict Content-Security-Policy (`default-src 'none'`, self-hosted script and style only), `nosniff`, `no-referrer`, `DENY` framing, `no-store`. No inline script, no remote assets, no thumbnails or embeds. Text is set with `textContent`.
- Error replies use fixed messages. Raw yt-dlp output, paths and server details are never sent to the browser. Access logging is off.

Links

- Only `youtube.com`, `www.youtube.com`, `m.youtube.com` and `youtu.be` with an 11-character video id. Credentials, ports, fragments, percent-encoding, non-ASCII text, playlists and unknown query fields are rejected. The URL passed to yt-dlp is rebuilt from the id, after `--`.

yt-dlp process

- Fixed argument list, `shell=False`, no stdin, `python -I` (isolated mode).
- Environment holds only `PATH` (FFmpeg and Deno folders), a private home and temp folder, and `SYSTEMROOT`/`WINDIR` on Windows. Secrets, proxies and `PYTHONPATH` from your shell are not passed on.
- Configuration files, plugins (`--no-plugin-dirs` and `YTDLP_NO_PLUGINS`), remote components, cookies, browser cookies, `.netrc`, `--exec`, batch files, cache, updates, file URLs and other extractors are all off. Downloads use yt-dlp's native downloader over HTTPS formats only. The only postprocessing is FFmpeg merging (or none with `--no-ffmpeg`).
- Output goes to a fixed name inside a new random folder. Existing files are never overwritten; your browser decides where the final file is saved.
- Limits: wall-clock timeout (30 min), individual file size checked every half second (2 GB default), video length (3 h default), live streams refused, two jobs at once plus a queue of four, at most eight finished files available for 30 minutes. A merge temporarily retains its input streams, so combined output and temp storage can use twice the file limit per running job (4 GB by default). The half-second polling can overshoot these thresholds between checks. Final file size and metadata are checked after process exit as well; every nonzero exit remains an error.
- File responses acquire a lease before sending headers. Expiry, retention and shutdown retire a file from new requests while an existing response keeps it until its handle closes, including errors and disconnects. Retired files remain charged alongside queued and running jobs. With default limits there are at most ten charged file/job slots; admission can refuse new work while transfers remain open. Range and HEAD responses release leases too. Server path offloading is disabled so a file cannot be released before the actual transfer ends.
- On Windows an isolated base-Python helper (`-I -S`) waits on a private, bounded release message. The app assigns it to a Job Object before sending its PID; the helper checks that this matches its own PID before starting the fixed downloader argv. A redirecting launcher therefore fails closed. A cancellation, expired deadline or assignment failure leaves the gate closed. The real child has no stdin, retains the fixed argv and sanitized environment, and inherits the memory, process-count and kill-on-close controls. One additional process slot is reserved for the fixed helper; its memory stays within the shared job memory cap. On Linux and macOS the tree runs in its own session and is killed as a group; there is no memory cap there, because an address-space limit would break Deno.

Deno

- Used only if found on `PATH`. yt-dlp runs it without any `--allow-*` permission, with `--no-remote`, `--no-npm` and `--cached-only`. Challenge scripts come from the pinned `yt-dlp-ejs` package. Deno's update check is off and its cache lives in the job folder.

Supply chain

- `requirements.txt` pins exact versions with sha256 hashes from PyPI. Install with `--require-hashes --only-binary=:all:`. CI uses actions pinned to commit SHAs, read-only permissions and no secrets.
- Keep optional FFmpeg and Deno installations patched using their official download links. Python dependency checks do not cover these native tools.

## Known limits

- This is process containment, not a sandbox: yt-dlp, FFmpeg and Deno run as your user and can read what your user can read.
- The isolated Windows helper starts before assignment, but executes only the fixed gate code and imports the standard library. Supplied argv starts after successful assignment. OS process creation and Job Object calls cannot be interrupted by the Python deadline; it is checked before and after those calls, and no downloader is released if the budget expired during setup.
- Any local program running as your user can reach the loopback port. The CSRF token stops web pages, not local software.
