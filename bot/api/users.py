from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from bot.bot import Bot
    from bot.api import APIClient


class APIUser:
    def __init__(self, client: APIClient, bot: Bot, id_: int, **_data):
        self._bot = bot
        self._client = client
        self.id = id_
        self.discord = self._bot.get_member(id_)

    def __repr__(self):
        return f"<APIUser {self.id}>"
