"""Standalone Telegram bot CLI for SC2 map-dev comms (send / fetch / setup).

This project's dedicated notification + play-test-media channel. It replaces
the old arrangement of borrowing the Trade-lab repo's notifier — that bot is
reserved for the user's separate trading work and must not receive SC2 traffic.

Stdlib only, like the rest of the toolkit.

Usage (how CLAUDE.md instructs agents):
    python scripts/sc2bot.py send --tag done   --title "SC2 Maps" "built + verified _dune_rift_2v2 v3"
    python scripts/sc2bot.py send --tag action --title "SC2 Maps" "need a play-test of the new ramps"
    python scripts/sc2bot.py send --photo previews/minimap.png "new minimap"
    python scripts/sc2bot.py fetch --wait 60      # pull play-test screenshots/videos into _tg/
    python scripts/sc2bot.py setup                # one-time, interactive (run it YOURSELF in a terminal)

Tags (leading emoji for quick scanning): done/failed/action/info.
`send` never raises; exit code 0/1 only — a failed notification must not
break the build that sent it. `fetch` reports errors loudly instead.

Config — token + chat id — lives OUTSIDE this public repo. Resolution order:
    1. $SC2BOT_CONFIG (explicit path)
    2. ~/OneDrive/Personal/sc2bot/telegram.json   (synced; survives machine moves)
    3. ~/.config/sc2bot/telegram.json
NEVER copy it into the repo tree; .gitignore has a safety net but don't test it.
(Do not resolve OneDrive via the %OneDrive% env var — it can point at a stale
unlinked account; the literal ~/OneDrive folder is the personal one.)

`setup` walks the one-time pairing: paste the token from @BotFather (into the
terminal prompt, never into a chat/repo), message the new bot once so it can
pin your chat_id, then it writes the config and sends a confirmation.

Media notes: Bot API getFile caps downloads at ~20 MB and updates are only
retained ~24 h — fetch soon after the user sends play-test videos.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = REPO_ROOT / "_tg"  # gitignored session media

TAG_EMOJI = {"done": "✅", "failed": "❌", "action": "⚠️", "info": "ℹ️"}


# ---------------------------------------------------------------- config

def config_candidates(explicit=None):
    out = []
    if explicit:
        out.append(Path(explicit))
    env = os.environ.get("SC2BOT_CONFIG")
    if env:
        out.append(Path(env))
    out.append(Path.home() / "OneDrive" / "Personal" / "sc2bot" / "telegram.json")
    out.append(Path.home() / ".config" / "sc2bot" / "telegram.json")
    return out


def load_config(explicit=None):
    for p in config_candidates(explicit):
        if p.is_file():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            if "token" in cfg and "chat_id" in cfg:
                return cfg, p
    return None, None


def state_path(cfg_path):
    return cfg_path.with_name("state.json")


def load_offset(cfg_path):
    p = state_path(cfg_path)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("offset", 0)
        except Exception:
            return 0
    return 0


def save_offset(cfg_path, offset):
    state_path(cfg_path).write_text(json.dumps({"offset": offset}), encoding="utf-8")


# ---------------------------------------------------------------- bot api

def _read_response(req, timeout, method):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            resp = json.load(e)
        except Exception:
            raise RuntimeError(f"{method}: HTTP {e.code}") from None
    if not resp.get("ok"):
        raise RuntimeError(f"{method}: {resp.get('description', resp)}")
    return resp["result"]


def api(token, method, params=None, timeout=35):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return _read_response(req, timeout, method)


def api_upload(token, method, params, field, path, timeout=180):
    path = Path(path)
    boundary = "----sc2bot" + os.urandom(12).hex()
    parts = []
    for k, v in params.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
        )
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
        f'filename="{path.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode("utf-8")
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return _read_response(req, timeout, method)


# ---------------------------------------------------------------- send

def cmd_send(args):
    cfg, _ = load_config(args.config)
    if not cfg:
        print("no config found - run: python scripts/sc2bot.py setup")
        return 1
    emoji = TAG_EMOJI.get(args.tag, TAG_EMOJI["info"])
    header = f"{emoji} {args.title}" if args.title else f"{emoji} SC2 Maps"
    text = header + ("\n" + args.message if args.message else "")
    try:
        if args.photo:
            api_upload(cfg["token"], "sendPhoto",
                       {"chat_id": cfg["chat_id"], "caption": args.message or ""},
                       "photo", args.photo)
        elif args.file:
            api_upload(cfg["token"], "sendDocument",
                       {"chat_id": cfg["chat_id"], "caption": args.message or ""},
                       "document", args.file)
        else:
            if not args.message:
                print("nothing to send (no message/photo/file)")
                return 1
            api(cfg["token"], "sendMessage", {"chat_id": cfg["chat_id"], "text": text})
        print("sent")
        return 0
    except Exception as e:  # never let a notification kill the caller
        print(f"send failed: {e}")
        return 1


# ---------------------------------------------------------------- fetch

_MEDIA_KEYS = ("photo", "video", "video_note", "animation", "document", "voice")


def _download(token, file_id, dest_dir, stem):
    info = api(token, "getFile", {"file_id": file_id})
    remote = info.get("file_path", "")
    ext = Path(remote).suffix or ".bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (stem + ext)
    url = f"https://api.telegram.org/file/bot{token}/{remote}"
    with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    return dest


def cmd_fetch(args):
    cfg, cfg_path = load_config(args.config)
    if not cfg:
        print("no config found - run: python scripts/sc2bot.py setup")
        return 1
    token = cfg["token"]
    offset = load_offset(cfg_path)
    params = {"timeout": args.wait}
    if offset:
        params["offset"] = offset + 1
    updates = api(token, "getUpdates", params, timeout=args.wait + 20)
    out_dir = Path(args.out) if args.out else MEDIA_DIR
    saved, texts, errors = 0, 0, 0
    for u in updates:
        offset = max(offset, u["update_id"])
        msg = u.get("message")
        if not msg or msg.get("chat", {}).get("id") != cfg["chat_id"]:
            continue  # ignore strangers - bot usernames are public
        when = datetime.fromtimestamp(msg.get("date", time.time())).strftime("%Y%m%d_%H%M%S")
        if msg.get("text"):
            texts += 1
            print(f"[msg {when}] {msg['text']}")
        if msg.get("caption"):
            print(f"[caption {when}] {msg['caption']}")
        for kind in _MEDIA_KEYS:
            if kind not in msg:
                continue
            obj = msg[kind]
            if kind == "photo":
                obj = obj[-1]  # sizes ascending; take the largest
            try:
                dest = _download(token, obj["file_id"], out_dir,
                                 f"{when}_{kind}_{obj.get('file_unique_id', 'x')}")
                saved += 1
                print(f"[saved] {dest}")
            except Exception as e:
                errors += 1
                print(f"[error] {kind} download failed (>20 MB files are not fetchable "
                      f"via the Bot API): {e}")
    if updates:
        save_offset(cfg_path, offset)
    print(f"done: {texts} message(s), {saved} file(s) saved to {out_dir}, {errors} error(s)")
    return 0 if errors == 0 else 1


# ---------------------------------------------------------------- setup

def cmd_setup(args):
    print("SC2 Telegram bot - one-time setup")
    print("Prerequisite: create the bot with @BotFather (/newbot) and have its token ready.\n")

    if args.config:
        target = Path(args.config)
    elif (Path.home() / "OneDrive").is_dir():
        target = Path.home() / "OneDrive" / "Personal" / "sc2bot" / "telegram.json"
    else:
        target = Path.home() / ".config" / "sc2bot" / "telegram.json"

    existing, existing_path = load_config(args.config)
    if existing:
        ans = input(f"Config already exists at {existing_path} - overwrite? [y/N] ").strip().lower()
        if ans != "y":
            print("aborted; existing config untouched")
            return 1
        target = existing_path

    token = input("Paste the bot token from @BotFather: ").strip()
    if ":" not in token:
        print("that does not look like a bot token (expected <digits>:<hash>)")
        return 1

    me = api(token, "getMe")
    username = me.get("username", "?")
    print(f"\nToken OK - bot is @{username}")
    print(f"Now open Telegram and send ANY message to @{username} "
          f"(this pins your chat id). Waiting up to 120 s...")

    chat_id, last_id, deadline = None, 0, time.time() + 120
    while time.time() < deadline and chat_id is None:
        for u in api(token, "getUpdates", {"timeout": 20, "offset": last_id + 1}, timeout=40):
            last_id = max(last_id, u["update_id"])
            msg = u.get("message")
            if msg and msg.get("chat", {}).get("type") == "private":
                chat_id = msg["chat"]["id"]
                who = msg["chat"].get("first_name") or msg["chat"].get("username") or chat_id
                print(f"Got it - chat id {chat_id} ({who})")
                break
    if chat_id is None:
        print("no message received - nothing written; run setup again")
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"token": token, "chat_id": chat_id, "bot_username": username}, indent=2),
        encoding="utf-8",
    )
    save_offset(target, last_id)  # ack pairing messages so the first fetch starts clean
    api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": f"✅ SC2 map bot connected on {socket.gethostname()}.\n"
                f"This channel carries Supreme Commander 2 map-dev updates and play-test uploads.",
    })
    print(f"\nDone. Config written to: {target}")
    print("Confirmation message sent. Try it any time with:")
    print('  python scripts/sc2bot.py send --tag info "hello from the toolkit"')
    return 0


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="Standalone Telegram bot for SC2 map-dev comms.")
    p.add_argument("--config", default=None, help="Explicit config path (overrides discovery).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("send", help="Send a message / photo / file to the user.")
    s.add_argument("message", nargs="?", default="", help="Message body (or caption).")
    s.add_argument("--tag", default="info", choices=list(TAG_EMOJI), help="Leading emoji tag.")
    s.add_argument("--title", default=None, help="Optional short title line.")
    s.add_argument("--photo", default=None, help="Path of an image to send.")
    s.add_argument("--file", default=None, help="Path of a document to send.")
    s.set_defaults(fn=cmd_send)

    f = sub.add_parser("fetch", help="Pull new messages + play-test media into _tg/.")
    f.add_argument("--wait", type=int, default=0, help="Long-poll seconds (default: return at once).")
    f.add_argument("--out", default=None, help=f"Output dir (default {MEDIA_DIR}).")
    f.set_defaults(fn=cmd_fetch)

    st = sub.add_parser("setup", help="Interactive one-time pairing (run in your own terminal).")
    st.set_defaults(fn=cmd_setup)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
