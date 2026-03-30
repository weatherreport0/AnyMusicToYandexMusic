import json
import os
import re
import tempfile
import logging
import urllib.parse
from pathlib import Path

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
import yt_dlp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from yandex_music import Client

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(os.environ["TG_BOT_TOKEN"])
user_data: dict[int, dict] = {}

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_DIR = os.path.join(BOT_DIR, "ffmpeg")
CHROME_PROFILE = os.path.join(BOT_DIR, ".chrome_profile")
COOKIES_FILE = os.path.join(BOT_DIR, "cookies.txt")
TOKENS_FILE = os.path.join(BOT_DIR, "tokens.json")


# --- Per-user YM tokens ---


def _load_tokens() -> dict[str, str]:
    if os.path.isfile(TOKENS_FILE):
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_tokens(tokens: dict[str, str]):
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f)


def get_ym_token(tg_uid: int) -> str | None:
    tokens = _load_tokens()
    return tokens.get(str(tg_uid))


def set_ym_token(tg_uid: int, token: str):
    tokens = _load_tokens()
    tokens[str(tg_uid)] = token
    _save_tokens(tokens)


def get_ym(tg_uid: int) -> Client:
    token = get_ym_token(tg_uid)
    if not token:
        raise ValueError("no_token")
    return Client(token).init()


# --- YouTube ---


def _yt_base_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "js_runtimes": {"node": {}}, "remote_components": {"ejs:github"}}
    if os.path.isfile(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
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


def download_audio(url: str, output_dir: str) -> str:
    ydl_opts = {
        **_yt_base_opts(),
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
        "writethumbnail": True,
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


# --- Helpers ---


def fmt_dur(sec: int | None) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    return f" [{m}:{s:02d}]"


NO_TOKEN_MSG = (
    "Сначала привяжи свой аккаунт Яндекс Музыки:\n"
    "/token ТВОЙ_ТОКЕН\n\n"
    "Токен можно получить на:\nhttps://oauth.yandex.ru/authorize?"
    "response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d"
)


def require_token(tg_uid: int, chat_id: int, message_id: int | None = None) -> bool:
    if get_ym_token(tg_uid):
        return True
    if message_id:
        bot.edit_message_text(NO_TOKEN_MSG, chat_id, message_id)
    else:
        bot.send_message(chat_id, NO_TOKEN_MSG)
    return False


# --- Handlers ---


@bot.message_handler(commands=["start"])
def cmd_start(message):
    bot.reply_to(
        message,
        "Отправь название песни — найду на YouTube, скачаю "
        "и загружу в плейлист Яндекс Музыки.\n\n"
        "/token ТОКЕН — привязать аккаунт Яндекс Музыки\n"
        "/playlists — список плейлистов\n"
        "/logout — отвязать аккаунт",
    )


@bot.message_handler(commands=["token"])
def cmd_token(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(
            message,
            "Отправь токен после команды:\n/token ТВОЙ_ТОКЕН\n\n"
            "Получить токен:\nhttps://oauth.yandex.ru/authorize?"
            "response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d",
        )
        return

    token = parts[1].strip()
    try:
        client = Client(token).init()
        login = client.me.account.login or client.me.account.display_name or "OK"
    except Exception as e:
        bot.reply_to(message, f"Неверный токен: {e}")
        return

    set_ym_token(message.from_user.id, token)
    bot.reply_to(message, f"Аккаунт привязан: {login}")
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass


@bot.message_handler(commands=["logout"])
def cmd_logout(message):
    tokens = _load_tokens()
    key = str(message.from_user.id)
    if key in tokens:
        del tokens[key]
        _save_tokens(tokens)
        bot.reply_to(message, "Аккаунт отвязан.")
    else:
        bot.reply_to(message, "Аккаунт не привязан.")


@bot.message_handler(commands=["playlists"])
def cmd_playlists(message):
    uid = message.from_user.id
    if not require_token(uid, message.chat.id):
        return
    try:
        client = get_ym(uid)
    except Exception as e:
        bot.reply_to(message, f"Ошибка: {e}")
        return
    playlists = client.users_playlists_list()
    if not playlists:
        bot.reply_to(message, "Плейлистов нет.")
        return
    lines = [f"• {pl.title} ({pl.track_count} треков)" for pl in playlists]
    bot.reply_to(message, "Твои плейлисты:\n\n" + "\n".join(lines))


_YT_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]{11})'
)


def _get_yt_info(url: str) -> dict | None:
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


@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/"))
def search_song(message):
    query = message.text.strip()
    if not query:
        return

    uid = message.from_user.id
    if not require_token(uid, message.chat.id):
        return

    yt_match = _YT_URL_RE.search(query)
    if yt_match:
        video_url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
        status_msg = bot.reply_to(message, "Получаю информацию о видео...")
        try:
            info = _get_yt_info(video_url)
        except Exception as e:
            bot.edit_message_text(f"Ошибка: {e}", message.chat.id, status_msg.id)
            return
        if not info:
            bot.edit_message_text("Не удалось получить информацию.", message.chat.id, status_msg.id)
            return
        user_data[uid] = {"results": [info], "selected": info}
        try:
            client = get_ym(uid)
        except Exception as e:
            bot.edit_message_text(f"Ошибка: {e}", message.chat.id, status_msg.id)
            return
        playlists = client.users_playlists_list()
        if not playlists:
            bot.edit_message_text("Нет плейлистов в Яндекс Музыке.", message.chat.id, status_msg.id)
            return
        markup = InlineKeyboardMarkup()
        for pl in playlists:
            markup.add(InlineKeyboardButton(
                text=f"{pl.title} ({pl.track_count})", callback_data=f"pl:{pl.kind}"
            ))
        bot.edit_message_text(
            f"Трек: {info['title']}\n\nВыберите плейлист:",
            message.chat.id, status_msg.id, reply_markup=markup,
        )
        return

    status_msg = bot.reply_to(message, "Ищу на YouTube...")

    try:
        results = search_youtube(query)
    except Exception as e:
        bot.edit_message_text(f"Ошибка поиска: {e}", message.chat.id, status_msg.id)
        return

    if not results:
        bot.edit_message_text("Ничего не найдено.", message.chat.id, status_msg.id)
        return

    user_data[uid] = {"results": results}

    markup = InlineKeyboardMarkup()
    for i, r in enumerate(results):
        label = f"{r['title']}{fmt_dur(r.get('duration'))}"
        markup.add(InlineKeyboardButton(text=label[:64], callback_data=f"yt:{i}"))

    bot.edit_message_text(
        "Выберите трек:", message.chat.id, status_msg.id, reply_markup=markup
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("yt:"))
def select_track(call):
    uid = call.from_user.id
    idx = int(call.data.split(":")[1])

    state = user_data.get(uid)
    if not state or idx >= len(state.get("results", [])):
        bot.answer_callback_query(call.id, "Результаты устарели, попробуй снова.")
        return

    state["selected"] = state["results"][idx]

    if not require_token(uid, call.message.chat.id, call.message.id):
        bot.answer_callback_query(call.id)
        return

    try:
        client = get_ym(uid)
    except Exception as e:
        bot.edit_message_text(f"Ошибка: {e}", call.message.chat.id, call.message.id)
        bot.answer_callback_query(call.id)
        return

    playlists = client.users_playlists_list()

    if not playlists:
        bot.edit_message_text(
            "Нет плейлистов в Яндекс Музыке.", call.message.chat.id, call.message.id
        )
        bot.answer_callback_query(call.id)
        return

    markup = InlineKeyboardMarkup()
    for pl in playlists:
        markup.add(
            InlineKeyboardButton(
                text=f"{pl.title} ({pl.track_count})", callback_data=f"pl:{pl.kind}"
            )
        )

    track = state["selected"]
    bot.edit_message_text(
        f"Трек: {track['title']}\n\nВыберите плейлист:",
        call.message.chat.id,
        call.message.id,
        reply_markup=markup,
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pl:"))
def add_to_playlist(call):
    uid = call.from_user.id
    playlist_kind = int(call.data.split(":")[1])

    state = user_data.get(uid)
    if not state or "selected" not in state:
        bot.answer_callback_query(call.id, "Результаты устарели, попробуй снова.")
        return

    track = state["selected"]
    bot.edit_message_text(
        f"Скачиваю: {track['title']}...", call.message.chat.id, call.message.id
    )
    bot.answer_callback_query(call.id)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = download_audio(track["url"], tmpdir)

            bot.edit_message_text(
                "Загружаю в Яндекс Музыку...", call.message.chat.id, call.message.id
            )

            token = get_ym_token(uid)
            client = Client(token).init()
            upload_to_ym(token, client.me.account.uid, playlist_kind, file_path)

        bot.edit_message_text(
            f"Готово! «{track['title']}» загружен в плейлист.",
            call.message.chat.id,
            call.message.id,
        )
    except Exception as e:
        logger.exception("Upload failed")
        bot.edit_message_text(
            f"Ошибка: {e}", call.message.chat.id, call.message.id
        )

    user_data.pop(uid, None)


if __name__ == "__main__":
    logger.info("Bot started")
    bot.infinity_polling()
