import asyncio
import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord

from cogs import message_board as message_board_module
from cogs.starboard import Starboard


class FakeChannel:
    mention = "#general"

    def __init__(self, nsfw=False, channel_id=456):
        self._nsfw = nsfw
        self.id = channel_id
        self.send = AsyncMock()

    def is_nsfw(self):
        return self._nsfw


class FakeBot:
    def __init__(self, emojis=None):
        self._emojis = emojis or {}

    def get_channel(self, _channel_id):
        return SimpleNamespace()

    def get_emoji(self, emoji_id):
        return self._emojis.get(emoji_id)


def make_bot(emojis=None) -> discord.Client:
    return cast(discord.Client, FakeBot(emojis))


def make_user() -> discord.User:
    return cast(discord.User, SimpleNamespace())


def make_reaction_ns(
    count, emoji="<:OnPhone:1062142401973588039>", channel=None, reactions=None
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
    count, emoji="<:OnPhone:1062142401973588039>", channel=None, reactions=None
) -> discord.Reaction:
    return cast(discord.Reaction, make_reaction_ns(count, emoji, channel, reactions))


def make_forbidden():
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(
        cast(Any, response),
        {"code": 50013, "message": "Missing Permissions"},
    )


def test_starboard_requires_three_matching_reactions_before_posting():
    starboard = Starboard(make_bot())
    starboard.create_board_post = AsyncMock()
    starboard.update_reaction_count = AsyncMock()

    asyncio.run(starboard.on_reaction_add(make_reaction(1), make_user()))
    asyncio.run(starboard.on_reaction_add(make_reaction(2), make_user()))

    starboard.create_board_post.assert_not_called()
    starboard.update_reaction_count.assert_not_called()

    asyncio.run(starboard.on_reaction_add(make_reaction(3), make_user()))

    starboard.create_board_post.assert_awaited_once()
    starboard.update_reaction_count.assert_not_called()


def test_starboard_ignores_other_emoji_under_five_reactions():
    starboard = Starboard(make_bot())
    starboard.create_board_post = AsyncMock()
    starboard.update_reaction_count = AsyncMock()

    asyncio.run(starboard.on_reaction_add(make_reaction(3, emoji="👍"), make_user()))

    starboard.create_board_post.assert_not_called()
    starboard.update_reaction_count.assert_not_called()


def test_starboard_posts_other_emoji_at_five_reactions():
    starboard = Starboard(make_bot())
    starboard.create_board_post = AsyncMock()
    starboard.update_reaction_count = AsyncMock()

    asyncio.run(starboard.on_reaction_add(make_reaction(4, emoji="👍"), make_user()))

    starboard.create_board_post.assert_not_called()
    starboard.update_reaction_count.assert_not_called()

    asyncio.run(starboard.on_reaction_add(make_reaction(5, emoji="👍"), make_user()))

    starboard.create_board_post.assert_awaited_once()
    starboard.update_reaction_count.assert_not_called()


def test_starboard_ignores_custom_emoji_bot_cannot_resolve():
    starboard = Starboard(make_bot())
    starboard.create_board_post = AsyncMock()
    starboard.update_reaction_count = AsyncMock()

    asyncio.run(
        starboard.on_reaction_add(
            make_reaction(5, emoji="<:someEmoji:1234567890123456789>"), make_user()
        )
    )

    starboard.create_board_post.assert_not_called()
    starboard.update_reaction_count.assert_not_called()


def test_starboard_ignores_custom_emoji_bot_cannot_use():
    unusable_emoji = SimpleNamespace(is_usable=lambda: False)
    starboard = Starboard(make_bot(emojis={1234567890123456789: unusable_emoji}))
    starboard.create_board_post = AsyncMock()
    starboard.update_reaction_count = AsyncMock()

    asyncio.run(
        starboard.on_reaction_add(
            make_reaction(5, emoji="<:someEmoji:1234567890123456789>"), make_user()
        )
    )

    starboard.create_board_post.assert_not_called()
    starboard.update_reaction_count.assert_not_called()


def test_starboard_posts_renderable_custom_emoji_at_five_reactions():
    usable_emoji = SimpleNamespace(is_usable=lambda: True)
    starboard = Starboard(make_bot(emojis={1234567890123456789: usable_emoji}))
    starboard.create_board_post = AsyncMock()
    starboard.update_reaction_count = AsyncMock()

    asyncio.run(
        starboard.on_reaction_add(
            make_reaction(4, emoji="<:someEmoji:1234567890123456789>"), make_user()
        )
    )

    starboard.create_board_post.assert_not_called()

    asyncio.run(
        starboard.on_reaction_add(
            make_reaction(5, emoji="<:someEmoji:1234567890123456789>"), make_user()
        )
    )

    starboard.create_board_post.assert_awaited_once()
    starboard.update_reaction_count.assert_not_called()


def test_starboard_ignores_ban_emoji_at_any_count():
    starboard = Starboard(make_bot())
    starboard.create_board_post = AsyncMock()
    starboard.update_reaction_count = AsyncMock()

    for count in (3, 5, 10):
        asyncio.run(
            starboard.on_reaction_add(
                make_reaction(count, emoji="<:ban:939740396286791741>"),
                make_user(),
            )
        )

    starboard.create_board_post.assert_not_called()
    starboard.update_reaction_count.assert_not_called()


def test_board_embed_uses_default_avatar_when_author_has_none():
    starboard = Starboard(make_bot())
    default_avatar = "https://cdn.discordapp.com/embed/avatars/0.png"
    msg = SimpleNamespace(
        content="hello",
        system_content="",
        attachments=[],
        author=SimpleNamespace(
            display_name="author",
            avatar=None,
            display_avatar=SimpleNamespace(url=default_avatar),
        ),
    )

    embed = starboard._get_board_embed(cast(discord.Message, msg))

    assert embed.author.icon_url == default_avatar


def test_starboard_title_includes_all_qualifying_reactions():
    starboard = Starboard(make_bot())
    reactions = [
        SimpleNamespace(emoji="<:OnPhone:1062142401973588039>", count=3),
        SimpleNamespace(emoji="👍", count=5),
        SimpleNamespace(emoji="👎", count=4),
    ]
    react = make_reaction(5, emoji="👍", reactions=reactions)

    assert (
        starboard._get_title(react)
        == "<:OnPhone:1062142401973588039> x **3** 👍 x **5** |#general"
    )


def test_starboard_title_excludes_ban_reactions():
    starboard = Starboard(make_bot())
    reactions = [
        SimpleNamespace(emoji="<:OnPhone:1062142401973588039>", count=3),
        SimpleNamespace(emoji="<:ban:939740396286791741>", count=7),
    ]
    react = make_reaction(3, reactions=reactions)

    assert (
        starboard._get_title(react)
        == "<:OnPhone:1062142401973588039> x **3** |#general"
    )


def test_starboard_updates_existing_post_when_new_emoji_qualifies():
    starboard = Starboard(make_bot())
    starboarded_msg = SimpleNamespace(edit=AsyncMock())
    starboard_channel = SimpleNamespace(
        fetch_message=AsyncMock(return_value=starboarded_msg)
    )
    starboard.board_msgs[123] = {
        "post_id": 789,
        "emoji": "<:OnPhone:1062142401973588039>",
        "channel": starboard_channel,
    }
    reactions = [
        SimpleNamespace(emoji="<:OnPhone:1062142401973588039>", count=3),
        SimpleNamespace(emoji="👍", count=5),
    ]

    asyncio.run(
        starboard.on_reaction_add(
            make_reaction(5, emoji="👍", reactions=reactions),
            make_user(),
        )
    )

    starboarded_msg.edit.assert_awaited_once_with(
        content="<:OnPhone:1062142401973588039> x **3** 👍 x **5** |#general"
    )


def test_starboard_removes_reaction_from_title_when_it_drops_below_threshold():
    starboard = Starboard(make_bot())
    starboarded_msg = SimpleNamespace(edit=AsyncMock())
    starboard_channel = SimpleNamespace(
        fetch_message=AsyncMock(return_value=starboarded_msg)
    )
    starboard.board_msgs[123] = {
        "post_id": 789,
        "emoji": "<:OnPhone:1062142401973588039>",
        "channel": starboard_channel,
    }
    reactions = [
        SimpleNamespace(emoji="<:OnPhone:1062142401973588039>", count=2),
        SimpleNamespace(emoji="👍", count=5),
    ]

    asyncio.run(
        starboard.on_reaction_remove(
            make_reaction(
                2, emoji="<:OnPhone:1062142401973588039>", reactions=reactions
            ),
            make_user(),
        )
    )

    starboarded_msg.edit.assert_awaited_once_with(content="👍 x **5** |#general")


def test_starboard_deletes_post_when_no_reactions_still_qualify():
    starboard = Starboard(make_bot())
    starboarded_msg = SimpleNamespace(delete=AsyncMock())
    starboard_channel = SimpleNamespace(
        fetch_message=AsyncMock(return_value=starboarded_msg)
    )
    starboard.board_msgs[123] = {
        "post_id": 789,
        "emoji": "<:OnPhone:1062142401973588039>",
        "channel": starboard_channel,
    }
    reactions = [SimpleNamespace(emoji="<:OnPhone:1062142401973588039>", count=2)]

    asyncio.run(
        starboard.on_reaction_remove(
            make_reaction(
                2, emoji="<:OnPhone:1062142401973588039>", reactions=reactions
            ),
            make_user(),
        )
    )

    starboarded_msg.delete.assert_awaited_once()
    assert starboard.board_msgs == {}


def test_starboard_ignores_reactions_in_starboard_channel():
    starboard = Starboard(make_bot())
    starboard.create_board_post = AsyncMock()
    starboard.update_reaction_count = AsyncMock()

    asyncio.run(
        starboard.on_reaction_add(
            make_reaction(
                5,
                emoji="👍",
                channel=FakeChannel(channel_id=starboard.board_channel_id),
            ),
            make_user(),
        )
    )

    starboard.create_board_post.assert_not_called()
    starboard.update_reaction_count.assert_not_called()


def test_starboard_reacts_to_post_with_starboarded_emoji():
    starboard = Starboard(make_bot())
    starboard._build_embeds = AsyncMock(return_value=[])
    starboard._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    starboarded_msg = SimpleNamespace(id=789, add_reaction=AsyncMock())
    starboard_channel = SimpleNamespace(send=AsyncMock(return_value=starboarded_msg))
    starboard.board_channel = cast(Any, starboard_channel)

    asyncio.run(starboard.create_board_post(make_reaction(5, emoji="👍")))

    starboarded_msg.add_reaction.assert_awaited_once_with("👍")
    assert starboard.board_msgs[123] == {
        "post_id": 789,
        "emoji": "👍",
        "channel": starboard.board_channel,
    }


def test_starboard_does_not_fallback_when_only_add_reaction_fails(caplog):
    starboard = Starboard(make_bot())
    starboard._build_embeds = AsyncMock(return_value=[])
    starboard._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    starboarded_msg = SimpleNamespace(
        id=789,
        add_reaction=AsyncMock(side_effect=make_forbidden()),
    )
    starboard_channel = SimpleNamespace(send=AsyncMock(return_value=starboarded_msg))
    starboard.board_channel = cast(Any, starboard_channel)
    source_channel = FakeChannel()

    with caplog.at_level(logging.WARNING, logger=message_board_module.logger.name):
        asyncio.run(
            starboard.create_board_post(
                make_reaction(5, emoji="👍", channel=source_channel)
            )
        )

    starboard_channel.send.assert_awaited_once()
    source_channel.send.assert_not_called()
    assert starboard.board_msgs[123] == {
        "post_id": 789,
        "emoji": "👍",
        "channel": starboard.board_channel,
    }
    assert "Failed to add reaction to starboard post" in caplog.text
    assert "board_post_id=789" in caplog.text


def test_starboard_forbidden_onphone_case_posts_fallback_to_source_channel(caplog):
    starboard = Starboard(make_bot())
    original_embed = discord.Embed(description="original message")
    starboard._build_embeds = AsyncMock(return_value=[original_embed])
    starboard._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    starboard.board_channel = cast(
        Any, SimpleNamespace(send=AsyncMock(side_effect=make_forbidden()))
    )
    fallback_msg = SimpleNamespace(id=789, add_reaction=AsyncMock())
    source_channel = FakeChannel()
    source_channel.send = AsyncMock(return_value=fallback_msg)

    with caplog.at_level(logging.WARNING, logger=message_board_module.logger.name):
        asyncio.run(
            starboard.create_board_post(make_reaction(3, channel=source_channel))
        )

    source_channel.send.assert_awaited_once()
    _, kwargs = source_channel.send.call_args
    assert len(kwargs["embeds"]) == 2
    petition_embed = kwargs["embeds"][1]
    assert "I would love to starboard your message" in petition_embed.description
    assert (
        "Please <:OnPhone:1062142401973588039> my petition"
        in petition_embed.description
    )
    assert "tenor.com" not in petition_embed.description
    assert (
        petition_embed.to_dict()["image"]["url"]
        == "https://media1.tenor.com/m/zdkG7NnnREoAAAAd/propaganda.gif"
    )
    fallback_msg.add_reaction.assert_awaited_once_with("<:OnPhone:1062142401973588039>")
    assert starboard.board_msgs[123] == {
        "post_id": 789,
        "emoji": "<:OnPhone:1062142401973588039>",
        "channel": source_channel,
    }
    assert "Failed to send starboard post to configured channel" in caplog.text
    assert "source_message_id=123" in caplog.text
    assert f"board_channel_id={starboard.board_channel_id}" in caplog.text


def test_starboard_forbidden_other_emoji_posts_fallback_to_source_channel():
    starboard = Starboard(make_bot())
    original_embed = discord.Embed(description="original message")
    starboard._build_embeds = AsyncMock(return_value=[original_embed])
    starboard._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    starboard.board_channel = cast(
        Any, SimpleNamespace(send=AsyncMock(side_effect=make_forbidden()))
    )
    fallback_msg = SimpleNamespace(id=789, add_reaction=AsyncMock())
    source_channel = FakeChannel()
    source_channel.send = AsyncMock(return_value=fallback_msg)

    asyncio.run(
        starboard.create_board_post(
            make_reaction(5, emoji="👍", channel=source_channel)
        )
    )

    source_channel.send.assert_awaited_once()
    _, kwargs = source_channel.send.call_args
    assert len(kwargs["embeds"]) == 2
    petition_embed = kwargs["embeds"][1]
    assert (
        "Please <:OnPhone:1062142401973588039> my petition"
        in petition_embed.description
    )
    assert (
        petition_embed.to_dict()["image"]["url"]
        == "https://media1.tenor.com/m/zdkG7NnnREoAAAAd/propaganda.gif"
    )
    fallback_msg.add_reaction.assert_awaited_once_with("👍")
    assert starboard.board_msgs[123] == {
        "post_id": 789,
        "emoji": "👍",
        "channel": source_channel,
    }


def test_starboard_delete_failure_is_logged(caplog):
    starboard = Starboard(make_bot())
    starboard_channel = SimpleNamespace(
        fetch_message=AsyncMock(side_effect=make_forbidden())
    )
    starboard.board_msgs[123] = {
        "post_id": 789,
        "emoji": "<:OnPhone:1062142401973588039>",
        "channel": starboard_channel,
    }

    with caplog.at_level(logging.WARNING, logger=message_board_module.logger.name):
        asyncio.run(starboard.delete_board_post(123))

    assert "Failed to delete starboard post" in caplog.text
    assert "source_message_id=123" in caplog.text
    assert "board_post_id=789" in caplog.text
