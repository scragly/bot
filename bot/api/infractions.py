from __future__ import annotations

import typing as t

import pendulum
from discord import Member, Emoji

from .users import APIUser
from .enums import InfractionType

import logging

if t.TYPE_CHECKING:
    from bot.bot import Bot
    from .client import APIClient

log = logging.getLogger(__name__)


class Infraction:

    _type_mapping = dict()

    def __new__(cls, client: APIClient, bot: Bot, data, **kwargs):
        """Use the type-specific classes for Infractions."""
        if cls is Infraction:
            try:
                newcls = Infraction._type_mapping[InfractionType(data["type"])]
            except (KeyError, ValueError):
                raise ValueError(f"Unexpected infraction type encountered: {data['type']}")
            else:
                return object.__new__(newcls)

        return super().__new__(cls)

    def __init_subclass__(cls, **kwargs):
        Infraction._type_mapping[cls.type] = cls

    def __init__(self, client: APIClient, bot: Bot, data):
        self._bot = bot
        self._client = bot.api_client

        self.id: int = data["id"]
        self.hidden: bool = data["hidden"]
        self.active: bool = data["active"]
        self.user: APIUser = APIUser(client, bot, data["user"])
        self.actor: APIUser = APIUser(client, bot, data["actor"])
        self.reason: str = data["reason"]
        self._inserted_at: pendulum.DateTime = pendulum.parse(data["inserted_at"])
        self._expires_at: t.Optional[pendulum.DateTime] = None
        if data.get("expires_at"):
            self._expires_at = pendulum.parse(data["expires_at"])

    @property
    def type(self) -> InfractionType:
        """Return the infraction type the subclass represents."""
        raise NotImplementedError("`type` is a required attribute for subclasses.")

    @property
    def has_expiry(self) -> bool:
        """Return True if the infraction type supports expiries."""
        raise NotImplementedError("`has_expiry` is a required attribute for subclasses.")

    @property
    def icon_id(self) -> int:
        """Return the Discord emoji ID to use for the infraction type."""
        raise NotImplementedError("`icon_id` is a required attribute for subclasses.")

    @property
    def past_tense(self) -> str:
        """Return the best past-tense version of the name for the infraction type."""
        raise NotImplementedError("`past_tense` is a required attribute for subclasses.")

    def __repr__(self):
        """Return a useful representation string for debugging output."""
        return f"<{self.__class__.__name__} {self.id}>"

    def __str__(self):
        """Return a human-presentable string for the infraction for usage in Discord messages."""
        return f"`{self.id}:` {self.icon} {self.past_tense} {self.created_duration}"

    @property
    def icon(self) -> Emoji:
        """Return the Discord emoji object from the icon_id."""
        return self._bot.get_emoji(self.icon_id)

    @property
    def created_string(self, *, date_only: bool = False) -> str:
        """
        Formatted creation datetime for human-friendly eyes.

        Example: ""2020-09-21 00:17", or "2020-09-21" if date_only=True.
        """
        if date_only:
            return self._inserted_at.to_date_string()

        return self._inserted_at.format("YYYY-MM-DD HH:mm")

    @property
    def created_duration(self) -> str:
        """
        Returns a friendly string showing approximate time since creation.

        Example: "2 hours ago"
        """
        return self._inserted_at.diff_for_humans()

    @property
    def expiry_string(self, date_only: bool = True) -> str:
        """
        Formatted expiry datetime for human-friendly eyes.

        Example: "2020-09-21" if date_only, "2020-09-21 00:17:28" if full.
        """
        if not self.has_expiry:
            return ""

        if not self._expires_at:
            return "permanent"

        return self._expires_at.to_datetime_string()

    @property
    def expiry_duration(self) -> str:
        """
        Returns a friendly string showing approximate time until expiry.

        Examples: "2 hours ago", "in 2 days", "in 14 minutes"
        """
        if not self.has_expiry:
            return ""

        if not self._expires_at:
            return "never"

        return self._expires_at.diff_for_humans()

    @property
    def total_duration(self) -> str:
        """
        Returns a friendly string showing the approximate total duration of infraction.

        Returns an empty string if permanent or has no duration.
        """
        if not self.has_expiry:
            return ""

        if not self._expires_at:
            return "permanently"

        return f"for {self._expires_at.diff_for_humans(self._inserted_at, absolute=True)}"

    async def _post(
        self,
        actor: t.Union[Member, APIUser, int, None] = None,
        type_: str = "warn",
    ) -> Infraction:
        params = {
            "actor"
        }

        return await self._client.post(
            'bot/infractions',
            params={
                'active': 'true',
                'type': infr_type,
                'user__id': str(user.id)
            }
        )

    @classmethod
    async def convert(cls, ctx, argument):
        """Discord commands argument converter."""
        pass


class Note(Infraction):
    type = InfractionType.note
    has_expiry = False
    icon_id = 470326274204631046
    past_tense = "Noted"


class Warn(Infraction):
    type = InfractionType.warning
    has_expiry = False
    icon_id = 470326274238447633
    past_tense = "Warned"


class Watch(Infraction):
    type = InfractionType.watch
    has_expiry = True
    icon_id = 470326274489843714
    past_tense = "Watched"


class Mute(Infraction):
    type = InfractionType.mute
    has_expiry = True
    icon_id = 472472640100106250
    past_tense = "Muted"


class Kick(Infraction):
    type = InfractionType.kick
    has_expiry = True
    icon_id = 469952898089091082
    past_tense = "Kicked"


class Ban(Infraction):
    type = InfractionType.ban
    has_expiry = True
    icon_id = 469952898026045441
    past_tense = "Banned"


class Superstar(Infraction):
    type = InfractionType.superstar
    has_expiry = True
    icon_id = 636288201258172446
    past_tense = "Superstarred"
