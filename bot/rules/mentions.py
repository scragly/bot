"""Detects total mentions exceeding the limit sent by a single user."""

from typing import Dict, Iterable, List, Optional, Tuple

from discord import Member, Message


async def apply(
    last_message: Message,
    recent_messages: List[Message],
    config: Dict[str, int]
) -> Optional[Tuple[str, Iterable[Member], Iterable[Message]]]:

    relevant_messages = tuple(
        msg
        for msg in recent_messages
        if msg.author == last_message.author
    )

    total_recent_mentions = sum(len(msg.mentions) for msg in relevant_messages)

    if total_recent_mentions > config['max']:
        return (
            f"sent {total_recent_mentions} mentions in {config['interval']}s",
            (last_message.author,),
            relevant_messages
        )
    return None
