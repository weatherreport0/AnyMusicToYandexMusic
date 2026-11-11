"""VK front-end: a community bot (Bots Long Poll API) with the same flow as the
Telegram bot. Needs VK_GROUP_TOKEN in .env; the community must have messages,
bot capabilities and Long Poll API (events: message_new, message_event) enabled."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll

import core
from core import logger

PAGE_SIZE = 5  # playlists per keyboard page (VK inline keyboards hold at most 6 rows)
LABEL_MAX = 40  # VK button label limit

HELP_MSG = (
    "Отправь название песни или ссылку на YouTube — найду, скачаю "
    "и загружу в плейлист Яндекс Музыки.\n\n"
    "/token ТОКЕН — привязать аккаунт Яндекс Музыки\n"
    "/playlists — список плейлистов\n"
    "/logout — отвязать аккаунт"
)
NO_TOKEN_MSG = (
    "Сначала привяжи свой аккаунт Яндекс Музыки:\n"
    "/token ТВОЙ_ТОКЕН\n\n"
    f"Токен можно получить на:\n{core.TOKEN_URL}"
)
STALE_MSG = "Результаты устарели, попробуй снова."


def user_key(vk_uid: int) -> str:
    return f"vk:{vk_uid}"


def _clip(label: str) -> str:
    return label if len(label) <= LABEL_MAX else label[: LABEL_MAX - 1] + "…"


def _button(label: str, payload: dict) -> dict:
    return {
        "action": {
            "type": "callback",
            "label": _clip(label),
            "payload": json.dumps(payload, ensure_ascii=False),
        }
    }


def _keyboard(rows: list[list[dict]]) -> str:
    return json.dumps({"inline": True, "buttons": rows}, ensure_ascii=False)


EMPTY_KEYBOARD = _keyboard([])


def tracks_keyboard(results: list[dict]) -> str:
    return _keyboard([
        [_button(f"{core.fmt_dur(r.get('duration')).strip()} {r['title']}".strip(), {"a": "yt", "i": i})]
        for i, r in enumerate(results)
    ])


def playlists_keyboard(playlists: list[dict], page: int) -> str:
    pages = max(1, -(-len(playlists) // PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = playlists[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [
        [_button(f"{pl['title']} ({pl['count']})", {"a": "pl", "k": pl["kind"]})]
        for pl in chunk
    ]
    nav = []
    if page > 0:
        nav.append(_button("← Назад", {"a": "pg", "p": page - 1}))
    if page < pages - 1:
        nav.append(_button("Дальше →", {"a": "pg", "p": page + 1}))
    if nav:
        rows.append(nav)
    return _keyboard(rows)


class VkBot:
    def __init__(self, api):
        self.api = api  # vk_api "api" object: api.messages.send(...)
        self.user_data: dict[int, dict] = {}

    # --- VK API wrappers ---

    def send(self, peer_id: int, text: str, keyboard: str | None = None) -> int:
        """Send a message; return its conversation_message_id (for later edits)."""
        params = {"peer_ids": peer_id, "message": text, "random_id": 0, "dont_parse_links": 1}
        if keyboard:
            params["keyboard"] = keyboard
        return self.api.messages.send(**params)[0]["conversation_message_id"]

    def edit(self, peer_id: int, cmid: int, text: str, keyboard: str | None = None):
        self.api.messages.edit(
            peer_id=peer_id,
            conversation_message_id=cmid,
            message=text,
            keyboard=keyboard or EMPTY_KEYBOARD,
            dont_parse_links=1,
        )

    def answer_event(self, event: dict, text: str | None = None):
        params = {
            "event_id": event["event_id"],
            "user_id": event["user_id"],
            "peer_id": event["peer_id"],
        }
        if text:
            params["event_data"] = json.dumps(
                {"type": "show_snackbar", "text": text}, ensure_ascii=False
            )
        try:
            self.api.messages.sendMessageEventAnswer(**params)
        except Exception:
            logger.debug("sendMessageEventAnswer failed", exc_info=True)

    # --- Helpers ---

    def _load_playlists(self, uid: int) -> list[dict]:
        client = core.get_ym(user_key(uid))
        return [
            {"kind": pl.kind, "title": pl.title, "count": pl.track_count}
            for pl in client.users_playlists_list()
        ]

    def _show_playlists(self, uid: int, peer_id: int, cmid: int, page: int = 0):
        state = self.user_data[uid]
        if "playlists" not in state:
            try:
                state["playlists"] = self._load_playlists(uid)
            except Exception as e:
                self.edit(peer_id, cmid, f"Ошибка: {e}")
                return
        if not state["playlists"]:
            self.edit(peer_id, cmid, "Нет плейлистов в Яндекс Музыке.")
            return
        self.edit(
            peer_id, cmid,
            f"Трек: {state['selected']['title']}\n\nВыберите плейлист:",
            playlists_keyboard(state["playlists"], page),
        )

    # --- Incoming messages ---

    def on_message(self, msg: dict):
        peer_id = msg["peer_id"]
        uid = msg["from_id"]
        text = (msg.get("text") or "").strip()
        if uid <= 0 or not text:
            return

        cmd, _, arg = text.partition(" ")
        cmd = cmd.lower().lstrip("/")
        arg = arg.strip()

        if cmd in ("start", "help", "начать", "помощь"):
            self.send(peer_id, HELP_MSG)
        elif cmd in ("token", "токен"):
            self.cmd_token(peer_id, uid, arg)
        elif cmd in ("logout", "выйти"):
            if core.delete_ym_token(user_key(uid)):
                self.send(peer_id, "Аккаунт отвязан.")
            else:
                self.send(peer_id, "Аккаунт не привязан.")
        elif cmd in ("playlists", "плейлисты"):
            self.cmd_playlists(peer_id, uid)
        elif text.startswith("/"):
            self.send(peer_id, HELP_MSG)
        else:
            self.search(peer_id, uid, text)

    def cmd_token(self, peer_id: int, uid: int, token: str):
        if not token:
            self.send(
                peer_id,
                f"Отправь токен после команды:\n/token ТВОЙ_ТОКЕН\n\nПолучить токен:\n{core.TOKEN_URL}",
            )
            return
        try:
            login = core.check_ym_token(token)
        except Exception as e:
            self.send(peer_id, f"Неверный токен: {e}")
            return
        core.set_ym_token(user_key(uid), token)
        self.send(peer_id, f"Аккаунт привязан: {login}")

    def cmd_playlists(self, peer_id: int, uid: int):
        if not core.get_ym_token(user_key(uid)):
            self.send(peer_id, NO_TOKEN_MSG)
            return
        try:
            playlists = self._load_playlists(uid)
        except Exception as e:
            self.send(peer_id, f"Ошибка: {e}")
            return
        if not playlists:
            self.send(peer_id, "Плейлистов нет.")
            return
        lines = [f"• {pl['title']} ({pl['count']} треков)" for pl in playlists]
        self.send(peer_id, "Твои плейлисты:\n\n" + "\n".join(lines))

    def search(self, peer_id: int, uid: int, query: str):
        if not core.get_ym_token(user_key(uid)):
            self.send(peer_id, NO_TOKEN_MSG)
            return

        yt_match = core.YT_URL_RE.search(query)
        if yt_match:
            video_url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
            cmid = self.send(peer_id, "Получаю информацию о видео...")
            try:
                info = core.get_yt_info(video_url)
            except Exception as e:
                self.edit(peer_id, cmid, f"Ошибка: {e}")
                return
            if not info:
                self.edit(peer_id, cmid, "Не удалось получить информацию.")
                return
            self.user_data[uid] = {"results": [info], "selected": info}
            self._show_playlists(uid, peer_id, cmid)
            return

        cmid = self.send(peer_id, "Ищу на YouTube...")
        try:
            results = core.search_youtube(query)
        except Exception as e:
            self.edit(peer_id, cmid, f"Ошибка поиска: {e}")
            return
        if not results:
            self.edit(peer_id, cmid, "Ничего не найдено.")
            return
        self.user_data[uid] = {"results": results}
        self.edit(peer_id, cmid, "Выберите трек:", tracks_keyboard(results))

    # --- Button presses ---

    def on_event(self, event: dict):
        uid = event["user_id"]
        peer_id = event["peer_id"]
        cmid = event["conversation_message_id"]
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        action = payload.get("a")
        state = self.user_data.get(uid)

        if action == "yt":
            idx = int(payload.get("i", -1))
            if not state or not 0 <= idx < len(state.get("results", [])):
                self.answer_event(event, STALE_MSG)
                return
            state["selected"] = state["results"][idx]
            self.answer_event(event)
            if not core.get_ym_token(user_key(uid)):
                self.edit(peer_id, cmid, NO_TOKEN_MSG)
                return
            self._show_playlists(uid, peer_id, cmid)

        elif action == "pg":
            if not state or "selected" not in state:
                self.answer_event(event, STALE_MSG)
                return
            self.answer_event(event)
            self._show_playlists(uid, peer_id, cmid, int(payload.get("p", 0)))

        elif action == "pl":
            if not state or "selected" not in state:
                self.answer_event(event, STALE_MSG)
                return
            self.answer_event(event)
            # Drop the state first so a second press cannot start a second upload.
            self.user_data.pop(uid, None)
            status = core.StatusUpdater(lambda text: self.edit(peer_id, cmid, text))
            core.transfer_track(user_key(uid), state["selected"], int(payload["k"]), status)

        else:
            self.answer_event(event)


def _group_id(api) -> int:
    if os.environ.get("VK_GROUP_ID", "").strip():
        return int(os.environ["VK_GROUP_ID"])
    resp = api.groups.getById()
    groups = resp["groups"] if isinstance(resp, dict) else resp
    return int(groups[0]["id"])


def main():
    session = vk_api.VkApi(token=os.environ["VK_GROUP_TOKEN"], api_version="5.199")
    api = session.get_api()
    group_id = _group_id(api)
    bot = VkBot(api)
    pool = ThreadPoolExecutor(max_workers=4)

    def run(handler, obj):
        try:
            handler(obj)
        except Exception:
            logger.exception("VK handler failed")

    logger.info("VK bot started (group %s)", group_id)
    while True:
        try:
            longpoll = VkBotLongPoll(session, group_id, wait=25)
            for event in longpoll.listen():
                if event.type == VkBotEventType.MESSAGE_NEW:
                    pool.submit(run, bot.on_message, event.obj["message"])
                elif event.type == VkBotEventType.MESSAGE_EVENT:
                    pool.submit(run, bot.on_event, dict(event.obj))
        except Exception as e:
            logger.error("VK long poll error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
