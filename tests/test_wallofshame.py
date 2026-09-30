import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

from cogs.wallofshame import WallOfShame

BAN_EMOJI = "<:ban:939740396286791741>"


class FakeChannel:
    mention = "#general"

    def __init__(self, nsfw=False, channel_id=456):
        self._nsfw = nsfw
        self.id = channel_id
        self.send = AsyncMock()

    def is_nsfw(self):
        return self._nsfw


class FakeBot:
    def get_channel(self, _channel_id):
        return SimpleNamespace()


def make_bot() -> discord.Client:
    return cast(discord.Client, FakeBot())


def make_user() -> discord.User:
    return cast(discord.User, SimpleNamespace())


def make_reaction_ns(
    count, emoji=BAN_EMOJI, channel=None, reactions=None
) -> SimpleNamespace:
    return SimpleNamespace(
        emoji=emoji,
        count=count,
        message=SimpleNamespace(
            id=123,
            channel=channel or FakeChannel(),
            reactions=reactions or [],
        ),
    )


def make_reaction(
    count, emoji=BAN_EMOJI, channel=None, reactions=None
) -> discord.Reaction:
    return cast(discord.Reaction, make_reaction_ns(count, emoji, channel, reactions))


def make_forbidden():
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(
        cast(Any, response),
        {"code": 50013, "message": "Missing Permissions"},
    )


def test_wall_of_shame_requires_three_ban_reactions_before_posting():
    wall = WallOfShame(make_bot())
    wall.create_board_post = AsyncMock()
    wall.update_reaction_count = AsyncMock()

    asyncio.run(wall.on_reaction_add(make_reaction(1), make_user()))
    asyncio.run(wall.on_reaction_add(make_reaction(2), make_user()))

    wall.create_board_post.assert_not_called()
    wall.update_reaction_count.assert_not_called()

    asyncio.run(wall.on_reaction_add(make_reaction(3), make_user()))

    wall.create_board_post.assert_awaited_once()
    wall.update_reaction_count.assert_not_called()


def test_wall_of_shame_ignores_other_emoji_at_any_count():
    wall = WallOfShame(make_bot())
    wall.create_board_post = AsyncMock()
    wall.update_reaction_count = AsyncMock()

    for count in (3, 5, 10):
        asyncio.run(wall.on_reaction_add(make_reaction(count, emoji="👍"), make_user()))

    wall.create_board_post.assert_not_called()
    wall.update_reaction_count.assert_not_called()


def test_wall_of_shame_title_only_includes_ban_reactions():
    wall = WallOfShame(make_bot())
    reactions = [
        SimpleNamespace(emoji=BAN_EMOJI, count=3),
        SimpleNamespace(emoji="👍", count=7),
    ]
    react = make_reaction(3, reactions=reactions)

    assert wall._get_title(react) == f"{BAN_EMOJI} x **3** |#general"


def test_wall_of_shame_deletes_post_when_ban_drops_below_threshold():
    wall = WallOfShame(make_bot())
    shamed_msg = SimpleNamespace(delete=AsyncMock())
    wall_channel = SimpleNamespace(fetch_message=AsyncMock(return_value=shamed_msg))
    wall.board_msgs[123] = {
        "post_id": 789,
        "emoji": BAN_EMOJI,
        "channel": wall_channel,
    }
    reactions = [SimpleNamespace(emoji=BAN_EMOJI, count=2)]

    asyncio.run(
        wall.on_reaction_remove(make_reaction(2, reactions=reactions), make_user())
    )

    shamed_msg.delete.assert_awaited_once()
    assert wall.board_msgs == {}


def test_wall_of_shame_does_not_delete_source_message():
    wall = WallOfShame(make_bot())
    wall._build_embeds = AsyncMock(return_value=[])
    wall._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    shamed_msg = SimpleNamespace(id=789, add_reaction=AsyncMock())
    wall_channel = SimpleNamespace(send=AsyncMock(return_value=shamed_msg))
    wall.board_channel = cast(Any, wall_channel)
    react_ns = make_reaction_ns(5)
    react_ns.message.delete = AsyncMock()

    asyncio.run(wall.on_reaction_add(cast(discord.Reaction, react_ns), make_user()))

    react_ns.message.delete.assert_not_called()
    wall_channel.send.assert_awaited_once()


def test_wall_of_shame_reacts_to_post_with_ban_emoji():
    wall = WallOfShame(make_bot())
    wall._build_embeds = AsyncMock(return_value=[])
    wall._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    shamed_msg = SimpleNamespace(id=789, add_reaction=AsyncMock())
    wall.board_channel = cast(
        Any, SimpleNamespace(send=AsyncMock(return_value=shamed_msg))
    )

    asyncio.run(wall.create_board_post(make_reaction(3)))

    shamed_msg.add_reaction.assert_awaited_once_with(BAN_EMOJI)
    assert wall.board_msgs[123] == {
        "post_id": 789,
        "emoji": BAN_EMOJI,
        "channel": wall.board_channel,
    }


def test_wall_of_shame_ignores_reactions_in_wall_of_shame_channel():
    wall = WallOfShame(make_bot())
    wall.create_board_post = AsyncMock()
    wall.update_reaction_count = AsyncMock()

    asyncio.run(
        wall.on_reaction_add(
            make_reaction(3, channel=FakeChannel(channel_id=wall.board_channel_id)),
            make_user(),
        )
    )

    wall.create_board_post.assert_not_called()
    wall.update_reaction_count.assert_not_called()


def test_wall_of_shame_forbidden_posts_fallback_to_source_channel():
    wall = WallOfShame(make_bot())
    original_embed = discord.Embed(description="original message")
    wall._build_embeds = AsyncMock(return_value=[original_embed])
    wall._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    wall.board_channel = cast(
        Any, SimpleNamespace(send=AsyncMock(side_effect=make_forbidden()))
    )
    fallback_msg = SimpleNamespace(id=789, add_reaction=AsyncMock())
    source_channel = FakeChannel()
    source_channel.send = AsyncMock(return_value=fallback_msg)

    asyncio.run(wall.create_board_post(make_reaction(3, channel=source_channel)))

    source_channel.send.assert_awaited_once()
    _, kwargs = source_channel.send.call_args
    assert len(kwargs["embeds"]) == 2
    petition_embed = kwargs["embeds"][1]
    assert "I would love to wall-of-shame your message" in petition_embed.description
    assert f"Please {BAN_EMOJI} my petition" in petition_embed.description
    assert (
        petition_embed.to_dict()["image"]["url"]
        == "https://media1.tenor.com/m/zdkG7NnnREoAAAAd/propaganda.gif"
    )
    fallback_msg.add_reaction.assert_awaited_once_with(BAN_EMOJI)
    assert wall.board_msgs[123] == {
        "post_id": 789,
        "emoji": BAN_EMOJI,
        "channel": source_channel,
    }
