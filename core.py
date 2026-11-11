"""Shared logic for the Telegram and VK front-ends: YouTube search/download,
Yandex Music upload, per-user token storage."""

import json
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path

import dns.resolver
import requests
import yt_dlp
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from yandex_music import Client

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BOT_DIR, ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("anymusic")

FFMPEG_DIR = os.path.join(BOT_DIR, "ffmpeg")
COOKIES_FILE = os.path.join(BOT_DIR, "cookies.txt")
TOKENS_FILE = os.path.join(BOT_DIR, "tokens.json")
BUNDLED_NODE = os.path.join(BOT_DIR, "runtime", "node22", "bin", "node")

# Telegram and YouTube are unreachable directly from the server's ISP, so they
# go through a proxy (PROXY_URL); Yandex Music and VK are reached directly.
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
YT_PROXY = os.environ.get("YT_PROXY", "").strip() or PROXY_URL

TOKEN_URL = (
    "https://oauth.yandex.ru/authorize?"
    "response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d"
)


# --- DNS fallback ---
# The ISP mangles DNS answers for www.youtube.com from public resolvers,
# so when the system resolver fails, ask DNS_FALLBACK (the router by default).

_orig_getaddrinfo = socket.getaddrinfo
_fallback_resolver = dns.resolver.Resolver(configure=False)
_fallback_resolver.nameservers = [
    s.strip() for s in os.environ.get("DNS_FALLBACK", "192.168.1.1").split(",") if s.strip()
]
_fallback_resolver.lifetime = 5


def _getaddrinfo_with_fallback(host, *args, **kwargs):
    try:
        return _orig_getaddrinfo(host, *args, **kwargs)
    except socket.gaierror:
        if isinstance(host, bytes):
            host = host.decode()
        if not isinstance(host, str):
            raise
        try:
            ip = _fallback_resolver.resolve(host, "A")[0].to_text()
        except Exception:
            raise socket.gaierror(socket.EAI_NONAME, f"Failed to resolve {host}") from None
        logger.debug("DNS fallback: %s -> %s", host, ip)
        return _orig_getaddrinfo(ip, *args, **kwargs)


socket.getaddrinfo = _getaddrinfo_with_fallback


# --- Formatting helpers ---


def make_progress_bar(percent: float, width: int = 20) -> str:
    percent = max(0.0, min(100.0, percent))
    filled = int(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {int(percent)}%"


def fmt_size(b: float) -> str:
    if b < 1024:
        return f"{b:.0f} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    else:
        return f"{b / 1024 ** 2:.1f} MB"


def fmt_dur(sec: int | None) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    return f" [{m}:{s:02d}]"


class StatusUpdater:
    """Throttled message editor: `edit(text)` is called at most once per interval."""

    def __init__(self, edit, interval: float = 3.0):
        self._edit = edit
        self.interval = interval
        self._last_time = 0.0
        self._last_text = ""

    def update(self, text: str, force: bool = False):
        now = time.time()
        if text == self._last_text:
            return
        if not force and (now - self._last_time) < self.interval:
            return
        try:
            self._edit(text)
            self._last_text = text
            self._last_time = now
        except Exception:
            pass


# --- Per-user YM tokens ---
# Keys are Telegram user ids ("123") or VK user ids with a prefix ("vk:123").

_tokens_lock = threading.Lock()


def _load_tokens() -> dict[str, str]:
    if os.path.isfile(TOKENS_FILE):
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_tokens(tokens: dict[str, str]):
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f)


def get_ym_token(user_key) -> str | None:
    return _load_tokens().get(str(user_key))


def set_ym_token(user_key, token: str):
    with _tokens_lock:
        tokens = _load_tokens()
        tokens[str(user_key)] = token
        _save_tokens(tokens)


def delete_ym_token(user_key) -> bool:
    with _tokens_lock:
        tokens = _load_tokens()
        if str(user_key) not in tokens:
            return False
        del tokens[str(user_key)]
        _save_tokens(tokens)
        return True


def check_ym_token(token: str) -> str:
    """Validate a token; return the account login. Raises on a bad token."""
    client = Client(token).init()
    return client.me.account.login or client.me.account.display_name or "OK"


def get_ym(user_key) -> Client:
    token = get_ym_token(user_key)
    if not token:
        raise ValueError("no_token")
    return Client(token).init()


# --- YouTube ---

YT_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]{11})'
)


def _yt_base_opts() -> dict:
    node = {"path": BUNDLED_NODE} if os.path.isfile(BUNDLED_NODE) else {}
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "js_runtimes": {"node": node},
        "remote_components": {"ejs:github"},
    }
    if os.path.isfile(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    if YT_PROXY:
        opts["proxy"] = YT_PROXY
    return opts


def search_youtube(query: str, max_results: int = 5) -> list[dict]:
    ydl_opts = {**_yt_base_opts(), "extract_flat": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        return [
            {
                "id": e["id"],
                "title": e.get("title", "Unknown"),
                "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                "duration": e.get("duration", 0),
            }
            for e in result.get("entries", [])
            if e
        ]


def get_yt_info(url: str) -> dict | None:
    ydl_opts = {**_yt_base_opts(), "extract_flat": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if info:
            return {
                "id": info["id"],
                "title": info.get("title", "Unknown"),
                "url": url,
                "duration": info.get("duration", 0),
            }
    return None


def download_audio(url: str, output_dir: str, progress_cb=None) -> str:
    def _progress_hook(d):
        if progress_cb is None:
            return
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                percent = d.get("downloaded_bytes", 0) / total * 100
                progress_cb("download", percent, d.get("speed"))
        elif d["status"] == "finished":
            progress_cb("converting", 100, None)

    def _pp_hook(d):
        if progress_cb is None:
            return
        if d.get("status") == "started":
            progress_cb("converting", 100, None)

    ydl_opts = {
        **_yt_base_opts(),
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
        "writethumbnail": True,
        "progress_hooks": [_progress_hook],
        "postprocessor_hooks": [_pp_hook],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            },
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
            },
            {
                "key": "EmbedThumbnail",
            },
        ],
    }
    if os.path.isdir(FFMPEG_DIR):
        ydl_opts["ffmpeg_location"] = FFMPEG_DIR

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        return str(Path(filename).with_suffix(".mp3"))


# --- Yandex Music upload ---


def upload_to_ym(token: str, uid: int, playlist_kind: int, file_path: str) -> str:
    filename = os.path.basename(file_path)

    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))

    headers = {"Authorization": f"OAuth {token}"}
    params = {
        "uid": str(uid),
        "playlist-id": f"{uid}:{playlist_kind}",
        "path": filename,
    }

    resp = session.post(
        "https://api.music.yandex.net/loader/upload-url",
        headers=headers,
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()

    upload_url = data["post-target"]
    ugc_track_id = data["ugc-track-id"]

    with open(file_path, "rb") as f:
        up_resp = session.post(
            upload_url, files={"file": (filename, f, "audio/mpeg")}, timeout=300
        )
        up_resp.raise_for_status()

    return ugc_track_id


def transfer_track(user_key, track: dict, playlist_kind: int, status: StatusUpdater):
    """Download `track` from YouTube and upload it to the user's playlist,
    reporting progress through `status`."""
    import tempfile

    title = track["title"]
    status.update(f"Скачиваю: {title}\n{make_progress_bar(0)}", force=True)

    def on_progress(phase, percent, speed):
        if phase == "download":
            speed_str = f" | {fmt_size(speed)}/s" if speed else ""
            status.update(f"Скачиваю: {title}\n{make_progress_bar(percent)}{speed_str}")
        elif phase == "converting":
            status.update(f"Конвертирую в MP3...\n{title}", force=True)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = download_audio(track["url"], tmpdir, progress_cb=on_progress)
            status.update(
                f"Загружаю в Яндекс Музыку ({fmt_size(os.path.getsize(file_path))})...\n{title}",
                force=True,
            )
            token = get_ym_token(user_key)
            client = Client(token).init()
            upload_to_ym(token, client.me.account.uid, playlist_kind, file_path)
        status.update(f"Готово! «{title}» загружен в плейлист.", force=True)
    except Exception as e:
        logger.exception("Upload failed")
        status.update(f"Ошибка: {e}", force=True)
