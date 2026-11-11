"""Telegram front-end."""

import os

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import core
from core import logger

if core.PROXY_URL:
    telebot.apihelper.proxy = {"https": core.PROXY_URL, "http": core.PROXY_URL}

bot = telebot.TeleBot(os.environ["TG_BOT_TOKEN"])
user_data: dict[int, dict] = {}

NO_TOKEN_MSG = (
    "Сначала привяжи свой аккаунт Яндекс Музыки:\n"
    "/token ТВОЙ_ТОКЕН\n\n"
    f"Токен можно получить на:\n{core.TOKEN_URL}"
)


def require_token(tg_uid: int, chat_id: int, message_id: int | None = None) -> bool:
    if core.get_ym_token(tg_uid):
        return True
    if message_id:
        bot.edit_message_text(NO_TOKEN_MSG, chat_id, message_id)
    else:
        bot.send_message(chat_id, NO_TOKEN_MSG)
    return False


def playlists_markup(playlists) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup()
    for pl in playlists:
        markup.add(InlineKeyboardButton(
            text=f"{pl.title} ({pl.track_count})", callback_data=f"pl:{pl.kind}"
        ))
    return markup


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
            f"Получить токен:\n{core.TOKEN_URL}",
        )
        return

    token = parts[1].strip()
    try:
        login = core.check_ym_token(token)
    except Exception as e:
        bot.reply_to(message, f"Неверный токен: {e}")
        return

    core.set_ym_token(message.from_user.id, token)
    bot.reply_to(message, f"Аккаунт привязан: {login}")
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass


@bot.message_handler(commands=["logout"])
def cmd_logout(message):
    if core.delete_ym_token(message.from_user.id):
        bot.reply_to(message, "Аккаунт отвязан.")
    else:
        bot.reply_to(message, "Аккаунт не привязан.")


@bot.message_handler(commands=["playlists"])
def cmd_playlists(message):
    uid = message.from_user.id
    if not require_token(uid, message.chat.id):
        return
    try:
        client = core.get_ym(uid)
    except Exception as e:
        bot.reply_to(message, f"Ошибка: {e}")
        return
    playlists = client.users_playlists_list()
    if not playlists:
        bot.reply_to(message, "Плейлистов нет.")
        return
    lines = [f"• {pl.title} ({pl.track_count} треков)" for pl in playlists]
    bot.reply_to(message, "Твои плейлисты:\n\n" + "\n".join(lines))


@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/"))
def search_song(message):
    query = message.text.strip()
    if not query:
        return

    uid = message.from_user.id
    if not require_token(uid, message.chat.id):
        return

    yt_match = core.YT_URL_RE.search(query)
    if yt_match:
        video_url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
        status_msg = bot.reply_to(message, "Получаю информацию о видео...")
        try:
            info = core.get_yt_info(video_url)
        except Exception as e:
            bot.edit_message_text(f"Ошибка: {e}", message.chat.id, status_msg.id)
            return
        if not info:
            bot.edit_message_text("Не удалось получить информацию.", message.chat.id, status_msg.id)
            return
        user_data[uid] = {"results": [info], "selected": info}
        try:
            client = core.get_ym(uid)
        except Exception as e:
            bot.edit_message_text(f"Ошибка: {e}", message.chat.id, status_msg.id)
            return
        playlists = client.users_playlists_list()
        if not playlists:
            bot.edit_message_text("Нет плейлистов в Яндекс Музыке.", message.chat.id, status_msg.id)
            return
        bot.edit_message_text(
            f"Трек: {info['title']}\n\nВыберите плейлист:",
            message.chat.id, status_msg.id, reply_markup=playlists_markup(playlists),
        )
        return

    status_msg = bot.reply_to(message, "Ищу на YouTube...")

    try:
        results = core.search_youtube(query)
    except Exception as e:
        bot.edit_message_text(f"Ошибка поиска: {e}", message.chat.id, status_msg.id)
        return

    if not results:
        bot.edit_message_text("Ничего не найдено.", message.chat.id, status_msg.id)
        return

    user_data[uid] = {"results": results}

    markup = InlineKeyboardMarkup()
    for i, r in enumerate(results):
        label = f"{r['title']}{core.fmt_dur(r.get('duration'))}"
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
        client = core.get_ym(uid)
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

    track = state["selected"]
    bot.edit_message_text(
        f"Трек: {track['title']}\n\nВыберите плейлист:",
        call.message.chat.id,
        call.message.id,
        reply_markup=playlists_markup(playlists),
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

    chat_id = call.message.chat.id
    msg_id = call.message.id
    status = core.StatusUpdater(lambda text: bot.edit_message_text(text, chat_id, msg_id))
    bot.answer_callback_query(call.id)

    core.transfer_track(uid, state["selected"], playlist_kind, status)
    user_data.pop(uid, None)


if __name__ == "__main__":
    logger.info("Bot started")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
