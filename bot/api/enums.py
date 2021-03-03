from __future__ import annotations

from enum import Enum

import logging

log = logging.getLogger(__name__)


class InfractionType(Enum):
    note = "note"
    warning = "warning"
    watch = "watch"
    mute = "mute"
    kick = "kick"
    ban = "ban"
    superstar = "superstar"

    def __str__(self):
        return self.value
