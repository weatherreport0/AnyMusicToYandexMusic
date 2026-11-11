"""Offline check of the VK bot flow against a fake VK API (no network).
Run: python test_vk_bot.py"""

import json
import os
import tempfile

import core
import vk_bot


class FakeMessages:
    def __init__(self):
        self.calls = []
        self._cmid = 100

    def send(self, **kw):
        self._cmid += 1
        self.calls.append(("send", kw))
        return [{"peer_id": kw["peer_ids"], "conversation_message_id": self._cmid}]

    def edit(self, **kw):
        self.calls.append(("edit", kw))
        return 1

    def sendMessageEventAnswer(self, **kw):
        self.calls.append(("answer", kw))
        return 1


class FakeApi:
    def __init__(self):
        self.messages = FakeMessages()


def buttons(call):
    return [b["action"] for row in json.loads(call[1]["keyboard"])["buttons"] for b in row]


def press(bot, cmid, payload):
    bot.on_event({
        "user_id": 7, "peer_id": 7, "event_id": "e", "conversation_message_id": cmid,
        "payload": payload,
    })


def main():
    core.TOKENS_FILE = os.path.join(tempfile.mkdtemp(), "tokens.json")
    results = [
        {"id": "a" * 11, "title": "Очень длинное название трека " * 3, "url": "u1", "duration": 212},
        {"id": "b" * 11, "title": "Short", "url": "u2", "duration": 0},
    ]
    playlists = [{"kind": 1000 + i, "title": f"PL{i}", "count": i} for i in range(7)]
    transfers = []

    core.search_youtube = lambda q, n=5: results
    core.check_ym_token = lambda t: "login"
    core.transfer_track = lambda key, track, kind, status: (
        transfers.append((key, track["url"], kind)), status.update("Готово!", force=True))

    api = FakeApi()
    bot = vk_bot.VkBot(api)
    bot._load_playlists = lambda uid: playlists
    calls = api.messages.calls
    msg = lambda text: bot.on_message({"peer_id": 7, "from_id": 7, "text": text})

    # No token yet -> asks to bind an account.
    msg("some song")
    assert "/token" in calls[-1][1]["message"]

    msg("/token abc")
    assert calls[-1][1]["message"] == "Аккаунт привязан: login"
    assert core.get_ym_token("vk:7") == "abc"

    # Search -> status message is edited into a track list.
    msg("some song")
    cmid = 103
    assert calls[-1][0] == "edit" and calls[-1][1]["conversation_message_id"] == cmid
    acts = buttons(calls[-1])
    assert len(acts) == 2 and all(a["type"] == "callback" for a in acts)
    assert all(len(a["label"]) <= 40 for a in acts) and acts[0]["label"].startswith("[3:32]")

    # Pick a track -> first page of playlists with a "next" button.
    press(bot, cmid, json.loads(acts[1]["payload"]))
    acts = buttons(calls[-1])
    assert [a["label"] for a in acts] == ["PL0 (0)", "PL1 (1)", "PL2 (2)", "PL3 (3)", "PL4 (4)", "Дальше →"]
    assert len(json.loads(calls[-1][1]["keyboard"])["buttons"]) <= 6

    # Next page.
    press(bot, cmid, json.loads(acts[-1]["payload"]))
    acts = buttons(calls[-1])
    assert [a["label"] for a in acts] == ["PL5 (5)", "PL6 (6)", "← Назад"]

    # Pick a playlist -> transfer runs once, keyboard is removed, state is cleared.
    press(bot, cmid, json.loads(acts[0]["payload"]))
    assert transfers == [("vk:7", "u2", 1005)]
    assert calls[-1][1]["message"] == "Готово!"
    assert json.loads(calls[-1][1]["keyboard"])["buttons"] == []

    # A stale button press does not start another transfer.
    press(bot, cmid, json.loads(acts[0]["payload"]))
    assert len(transfers) == 1 and "event_data" in calls[-1][1]

    msg("/logout")
    assert core.get_ym_token("vk:7") is None

    print("OK")


if __name__ == "__main__":
    main()
