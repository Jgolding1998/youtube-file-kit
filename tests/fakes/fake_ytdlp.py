"""Stand-in for yt-dlp used by tests. Behaviour is chosen by the video id."""

import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
url = args[args.index("--") + 1]
vid = url.rsplit("=", 1)[-1]
home = next(a.split(":", 1)[1] for i, a in enumerate(args) if args[i - 1] == "--paths" and a.startswith("home:"))
out = Path(home)


def meta(**extra):
    data = {"id": vid, "title": "Test video", "duration": 10, "live_status": "not_live"}
    data.update(extra)
    print("YFKMETA " + json.dumps(data), flush=True)


def fail(message):
    sys.stderr.write(f"ERROR: [youtube] {vid}: {message}\n")
    sys.exit(1)


record = {"argv": args, "env": sorted(os.environ), "cwd": os.getcwd()}

if vid == "okokokokok1":
    meta()
    (out / "record.json").write_text(json.dumps(record))
    (out / "media.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4000)
elif vid == "evilname001":
    meta(title='../../CON <>:"|?*\u202etxt.exe')
    (out / "media.webm").write_bytes(b"x" * 100)
elif vid == "privatevid1":
    fail("Private video. Sign in if you've been granted access to this video. Use --cookies " + "\\".join(("C:", "Users", "fixture", "cookies.txt")))
elif vid == "agelimited1":
    fail("Sign in to confirm your age. This video may be inappropriate for some users.")
elif vid == "regionlock1":
    fail("The uploader has not made this video available in your country")
elif vid == "drmvideo001":
    fail("This video is DRM protected")
elif vid == "membersonly":
    fail("Join this channel to get access to members-only content like this video")
elif vid == "botcheck001":
    fail("Sign in to confirm you\u2019re not a bot. Use --cookies-from-browser")
elif vid == "removed0001":
    fail("Video unavailable. This video has been removed by the uploader")
elif vid == "leaky000001":
    fail("weird failure at " + "\\".join(("C:", "Users", "fixture", "secret-value.txt")))
elif vid == "toolong0001":
    meta(duration=999999)
    time.sleep(30)
elif vid == "livenow0001":
    meta(live_status="is_live", duration=None)
    time.sleep(30)
elif vid == "bigfile0001":
    meta()
    with open(out / "media.mp4.part", "wb") as f:
        for _ in range(200):
            f.write(b"\x00" * 65536)
            f.flush()
            time.sleep(0.02)
elif vid == "sleepy00001":
    meta()
    time.sleep(60)
elif vid == "nofile00001":
    meta()
elif vid == "wrongext001":
    meta()
    (out / "media.exe").write_bytes(b"MZ")
else:
    fail("Video unavailable")
