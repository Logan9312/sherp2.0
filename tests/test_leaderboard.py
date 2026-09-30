import asyncio
import logging
import sqlite3
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

from cogs import message_board as message_board_module
from cogs.helpers.leaderboard_store import LeaderboardStore
from cogs.leaderboard import (
    ONPHONE_BOARD,
    WALL_OF_SHAME_BOARD,
    Leaderboard,
    open_leaderboard_store,
    position_window,
    rank_scores,
    setup_leaderboard,
)
from cogs.starboard import Starboard
from cogs.wallofshame import WallOfShame

ONPHONE = "<:OnPhone:1062142401973588039>"
BAN = "<:ban:939740396286791741>"
GUILD_ID = 1
AUTHOR_ID = 100
MESSAGE_ID = 123


class FakeChannel:
    mention = "#general"

    def __init__(self, nsfw=False, channel_id=456):
        self._nsfw = nsfw
        self.id = channel_id
        self.send = AsyncMock()

    def is_nsfw(self):
        return self._nsfw


class FakeBot:
    def __init__(self, users=None):
        self._users = users or {}

    def get_channel(self, _channel_id):
        return SimpleNamespace()

    def get_emoji(self, _emoji_id):
        return None

    def get_user(self, user_id):
        return self._users.get(user_id)


def make_bot(users=None) -> discord.Client:
    return cast(discord.Client, FakeBot(users))


def make_store() -> LeaderboardStore:
    return LeaderboardStore(":memory:")


def make_user(user_id, bot=False) -> discord.User:
    return cast(discord.User, SimpleNamespace(id=user_id, bot=bot))


def make_message(
    message_id=MESSAGE_ID,
    author_id=AUTHOR_ID,
    author_bot=False,
    channel=None,
    guild_id: int | None = GUILD_ID,
    reactions=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        author=SimpleNamespace(id=author_id, bot=author_bot),
        guild=SimpleNamespace(id=guild_id) if guild_id is not None else None,
        channel=channel or FakeChannel(),
        reactions=reactions if reactions is not None else [],
    )


def make_reaction(count, emoji=ONPHONE, message=None) -> discord.Reaction:
    return cast(
        discord.Reaction,
        SimpleNamespace(emoji=emoji, count=count, message=message or make_message()),
    )


def make_forbidden():
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(
        cast(Any, response),
        {"code": 50013, "message": "Missing Permissions"},
    )


def scores(store, board="onphone", guild_id=GUILD_ID):
    return dict(store.get_scores(board, guild_id))


def react_all(store, user_ids, board="onphone", message_id=MESSAGE_ID):
    for user_id in user_ids:
        store.add_reaction(board, GUILD_ID, message_id, AUTHOR_ID, user_id)


def make_board_with_channel(board_cls, store):
    board = board_cls(make_bot(), leaderboard=store)
    board._build_embeds = AsyncMock(return_value=[])
    board._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    board_post = SimpleNamespace(id=789, add_reaction=AsyncMock(), edit=AsyncMock())
    board.board_channel = cast(
        Any,
        SimpleNamespace(
            send=AsyncMock(return_value=board_post),
            fetch_message=AsyncMock(return_value=board_post),
        ),
    )
    return board


# LeaderboardStore


def test_reaction_costs_one_point_and_removing_it_refunds():
    store = make_store()

    react_all(store, [201])
    assert scores(store) == {201: -1}

    store.remove_reaction("onphone", MESSAGE_ID, 201)
    assert scores(store) == {201: 0}


def test_duplicate_reaction_event_is_only_charged_once():
    store = make_store()

    react_all(store, [201, 201])

    assert scores(store) == {201: -1}


def test_boarding_rewards_every_reactor_and_the_author():
    store = make_store()
    react_all(store, [201, 202, 203])

    assert store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)

    assert scores(store) == {201: 1, 202: 1, 203: 1, AUTHOR_ID: 3}


def test_boarding_is_only_rewarded_once():
    store = make_store()
    react_all(store, [201, 202, 203])
    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)

    assert not store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)

    assert scores(store) == {201: 1, 202: 1, 203: 1, AUTHOR_ID: 3}


def test_removed_reactions_are_not_rewarded_on_boarding():
    store = make_store()
    react_all(store, [201, 202, 203])
    store.remove_reaction("onphone", MESSAGE_ID, 203)

    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)

    assert scores(store) == {201: 1, 202: 1, 203: 0, AUTHOR_ID: 2}


def test_late_reaction_is_free_and_counts_for_the_author():
    store = make_store()
    react_all(store, [201, 202, 203])
    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)

    react_all(store, [204])
    assert scores(store).get(204, 0) == 0
    assert scores(store)[AUTHOR_ID] == 4

    store.remove_reaction("onphone", MESSAGE_ID, 204)
    assert scores(store).get(204, 0) == 0
    assert scores(store)[AUTHOR_ID] == 3


def test_backer_keeps_reward_after_unreacting_and_is_not_charged_again():
    store = make_store()
    react_all(store, [201, 202, 203])
    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)

    store.remove_reaction("onphone", MESSAGE_ID, 201)
    assert scores(store)[201] == 1
    assert scores(store)[AUTHOR_ID] == 2

    react_all(store, [201])
    assert scores(store)[201] == 1
    assert scores(store)[AUTHOR_ID] == 3

    store.remove_reaction("onphone", MESSAGE_ID, 201)
    assert scores(store)[201] == 1
    assert scores(store)[AUTHOR_ID] == 2


def test_bot_author_earns_nothing_but_backers_are_rewarded():
    store = make_store()
    store.add_reaction("onphone", GUILD_ID, MESSAGE_ID, None, 201)
    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, None)
    store.add_reaction("onphone", GUILD_ID, MESSAGE_ID, None, 202)

    assert scores(store) == {201: 1}


def test_deleting_pending_message_refunds_reactors():
    store = make_store()
    react_all(store, [201, 202])

    store.forget_pending_message("onphone", MESSAGE_ID)
    assert scores(store) == {201: 0, 202: 0}

    react_all(store, [201])
    assert scores(store)[201] == -1


def test_deleting_boarded_message_keeps_rewards():
    store = make_store()
    react_all(store, [201, 202, 203])
    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)

    store.forget_pending_message("onphone", MESSAGE_ID)

    assert scores(store) == {201: 1, 202: 1, 203: 1, AUTHOR_ID: 3}


def test_clearing_reactions_on_pending_message_refunds_reactors():
    store = make_store()
    react_all(store, [201, 202])

    store.clear_reactions("onphone", MESSAGE_ID)

    assert scores(store) == {201: 0, 202: 0}


def test_clearing_reactions_on_boarded_message_resets_author_only():
    store = make_store()
    react_all(store, [201, 202, 203])
    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)
    react_all(store, [204])

    store.clear_reactions("onphone", MESSAGE_ID)

    assert scores(store) == {201: 1, 202: 1, 203: 1, AUTHOR_ID: 0}


def test_boarding_without_reactors_then_late_reaction():
    store = make_store()

    assert store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)
    react_all(store, [201])

    assert scores(store) == {AUTHOR_ID: 1}


def test_boards_and_guilds_are_scored_separately():
    store = make_store()
    store.add_reaction("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID, 201)
    store.add_reaction("ban", GUILD_ID, MESSAGE_ID, AUTHOR_ID, 202)
    store.add_reaction("onphone", 2, 999, AUTHOR_ID, 203)

    assert scores(store, "onphone") == {201: -1}
    assert scores(store, "ban") == {202: -1}
    assert scores(store, "onphone", guild_id=2) == {203: -1}


def test_scores_persist_across_reopening(tmp_path):
    path = str(tmp_path / "nested" / "leaderboard.db")
    store = LeaderboardStore(path)
    react_all(store, [201, 202, 203])
    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)
    store.close()

    reopened = LeaderboardStore(path)

    assert scores(reopened) == {201: 1, 202: 1, 203: 1, AUTHOR_ID: 3}
    assert not reopened.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)


def test_get_scores_orders_highest_first():
    store = make_store()
    react_all(store, [201, 202, 203])
    store.mark_boarded("onphone", GUILD_ID, MESSAGE_ID, AUTHOR_ID)
    react_all(store, [301], message_id=456)

    assert store.get_scores("onphone", GUILD_ID) == [
        (AUTHOR_ID, 3),
        (201, 1),
        (202, 1),
        (203, 1),
        (301, -1),
    ]


# Ranking


def test_rank_scores_gives_tied_users_the_same_rank():
    ranked = rank_scores([(4, 1), (3, 3), (1, 5), (2, 3)])

    assert [(e.rank, e.user_id, e.score) for e in ranked] == [
        (1, 1, 5),
        (2, 2, 3),
        (2, 3, 3),
        (4, 4, 1),
    ]


def test_rank_scores_adds_missing_user_with_zero_points():
    ranked = rank_scores([(1, 5), (2, -1)], include_user_id=3)

    assert [(e.rank, e.user_id, e.score) for e in ranked] == [
        (1, 1, 5),
        (2, 3, 0),
        (3, 2, -1),
    ]


def test_rank_scores_does_not_duplicate_existing_user():
    ranked = rank_scores([(1, 5), (2, -1)], include_user_id=2)

    assert [e.user_id for e in ranked] == [1, 2]


def test_position_window_shows_five_above_and_below():
    ranked = rank_scores([(user_id, 100 - user_id) for user_id in range(20)])

    window = position_window(ranked, 10)

    assert [e.user_id for e in window] == list(range(5, 16))


def test_position_window_is_cut_off_at_the_top_and_bottom():
    ranked = rank_scores([(user_id, 100 - user_id) for user_id in range(20)])

    assert [e.user_id for e in position_window(ranked, 1)] == list(range(0, 7))
    assert [e.user_id for e in position_window(ranked, 19)] == list(range(14, 20))


# Board integration


def test_onphone_reaction_is_scored_by_starboard():
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)

    asyncio.run(starboard.on_reaction_add(make_reaction(1), make_user(201)))

    assert scores(store) == {201: -1}


def test_removing_onphone_reaction_refunds_via_starboard():
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)
    msg = make_message()

    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=msg), make_user(201))
    )
    asyncio.run(
        starboard.on_reaction_remove(make_reaction(0, message=msg), make_user(201))
    )

    assert scores(store) == {201: 0}


@pytest.mark.parametrize(
    ("reaction", "user"),
    [
        (make_reaction(1), make_user(201, bot=True)),
        (make_reaction(1), make_user(AUTHOR_ID)),
        (make_reaction(1, message=make_message(channel=FakeChannel(nsfw=True))), None),
        (make_reaction(1, message=make_message(guild_id=None)), None),
        (make_reaction(1, emoji="👍"), None),
        (make_reaction(1, emoji=BAN), None),
    ],
    ids=["bot", "self", "nsfw", "no-guild", "other-emoji", "ban-emoji"],
)
def test_starboard_ignores_unscored_reactions(reaction, user):
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)

    asyncio.run(starboard.on_reaction_add(reaction, user or make_user(201)))

    assert scores(store) == {}


def test_starboard_ignores_reactions_in_board_channel():
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)
    msg = make_message(channel=FakeChannel(channel_id=starboard.board_channel_id))

    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=msg), make_user(201))
    )

    assert scores(store) == {}


def test_reaching_starboard_rewards_backers_and_author():
    store = make_store()
    starboard = make_board_with_channel(Starboard, store)
    msg = make_message()

    for count, user_id in enumerate([201, 202, 203], start=1):
        asyncio.run(
            starboard.on_reaction_add(
                make_reaction(count, message=msg), make_user(user_id)
            )
        )

    starboard.board_channel.send.assert_awaited_once()
    assert scores(store) == {201: 1, 202: 1, 203: 1, AUTHOR_ID: 3}

    asyncio.run(
        starboard.on_reaction_add(make_reaction(4, message=msg), make_user(204))
    )

    assert scores(store).get(204, 0) == 0
    assert scores(store)[AUTHOR_ID] == 4


def test_fallback_board_post_also_rewards():
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)
    starboard._build_embeds = AsyncMock(return_value=[])
    starboard._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    starboard.board_channel = cast(
        Any, SimpleNamespace(send=AsyncMock(side_effect=make_forbidden()))
    )
    source_channel = FakeChannel()
    source_channel.send = AsyncMock(
        return_value=SimpleNamespace(id=789, add_reaction=AsyncMock())
    )
    msg = make_message(channel=source_channel)
    react_all(store, [201, 202, 203])

    asyncio.run(starboard.create_board_post(make_reaction(3, message=msg)))

    source_channel.send.assert_awaited_once()
    assert scores(store) == {201: 1, 202: 1, 203: 1, AUTHOR_ID: 3}


def test_failed_board_post_rewards_nothing():
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)
    starboard._build_embeds = AsyncMock(return_value=[])
    starboard._get_open_msg_view = AsyncMock(return_value=SimpleNamespace())
    starboard.board_channel = cast(
        Any, SimpleNamespace(send=AsyncMock(side_effect=make_forbidden()))
    )
    source_channel = FakeChannel()
    source_channel.send = AsyncMock(side_effect=make_forbidden())
    msg = make_message(channel=source_channel)
    react_all(store, [201, 202, 203])

    with pytest.raises(discord.HTTPException):
        asyncio.run(starboard.create_board_post(make_reaction(3, message=msg)))

    assert scores(store) == {201: -1, 202: -1, 203: -1}


def test_starboard_post_from_other_emoji_rewards_onphone_backers():
    store = make_store()
    starboard = make_board_with_channel(Starboard, store)
    msg = make_message()

    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=msg), make_user(201))
    )
    asyncio.run(
        starboard.on_reaction_add(
            make_reaction(5, emoji="👍", message=msg), make_user(202)
        )
    )

    starboard.board_channel.send.assert_awaited_once()
    assert scores(store) == {201: 1, AUTHOR_ID: 1}


def test_wall_of_shame_scores_ban_reactions_on_its_own_leaderboard():
    store = make_store()
    wall = make_board_with_channel(WallOfShame, store)
    msg = make_message()

    asyncio.run(wall.on_reaction_add(make_reaction(1, message=msg), make_user(301)))
    for count, user_id in enumerate([201, 202, 203], start=1):
        asyncio.run(
            wall.on_reaction_add(
                make_reaction(count, emoji=BAN, message=msg), make_user(user_id)
            )
        )

    wall.board_channel.send.assert_awaited_once()
    assert scores(store, "ban") == {201: 1, 202: 1, 203: 1, AUTHOR_ID: 3}
    assert scores(store, "onphone") == {}


def test_deleting_pending_message_refunds_via_board():
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)
    msg = make_message()
    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=msg), make_user(201))
    )

    asyncio.run(starboard.on_message_delete(cast(discord.Message, msg)))

    assert scores(store) == {201: 0}


def test_bulk_deleting_messages_refunds_pending_reactors():
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)
    first = make_message(message_id=1)
    second = make_message(message_id=2)
    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=first), make_user(201))
    )
    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=second), make_user(202))
    )

    asyncio.run(
        starboard.on_bulk_message_delete(cast(list[discord.Message], [first, second]))
    )

    assert scores(store) == {201: 0, 202: 0}


def test_clearing_reactions_refunds_via_board():
    store = make_store()
    starboard = Starboard(make_bot(), leaderboard=store)
    msg = make_message()
    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=msg), make_user(201))
    )
    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=msg), make_user(202))
    )

    asyncio.run(starboard.on_reaction_clear_emoji(make_reaction(0, "👍", msg)))
    assert scores(store) == {201: -1, 202: -1}

    asyncio.run(starboard.on_reaction_clear_emoji(make_reaction(0, message=msg)))
    assert scores(store) == {201: 0, 202: 0}

    asyncio.run(
        starboard.on_reaction_add(make_reaction(1, message=msg), make_user(201))
    )
    asyncio.run(starboard.on_reaction_clear(cast(discord.Message, msg), []))
    assert scores(store) == {201: 0, 202: 0}


def test_leaderboard_database_errors_do_not_block_board(caplog):
    class BrokenStore:
        def add_reaction(self, *_args):
            raise sqlite3.OperationalError("database is locked")

        def mark_boarded(self, *_args):
            raise sqlite3.OperationalError("database is locked")

    starboard = make_board_with_channel(Starboard, cast(Any, BrokenStore()))

    with caplog.at_level(logging.ERROR, logger=message_board_module.logger.name):
        asyncio.run(starboard.on_reaction_add(make_reaction(3), make_user(201)))

    starboard.board_channel.send.assert_awaited_once()
    assert "Failed to update starboard leaderboard via add_reaction" in caplog.text
    assert "Failed to update starboard leaderboard via mark_boarded" in caplog.text


# Commands


class FakeGuild:
    def __init__(self, names):
        self.id = GUILD_ID
        self._names = names

    def get_member(self, user_id):
        name = self._names.get(user_id)
        return SimpleNamespace(display_name=name) if name else None


def make_cog(store, users=None) -> Leaderboard:
    return Leaderboard(make_bot(users), store)


def make_ctx(guild, user_id=201, dm_error=None):
    return SimpleNamespace(
        guild=guild,
        author=SimpleNamespace(id=user_id, send=AsyncMock(side_effect=dm_error)),
        send=AsyncMock(),
        reply=AsyncMock(),
    )


def make_interaction(guild, user_id=201):
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


def run_command(cog, command, ctx_or_interaction):
    # Runs a slash or prefix command's callback the way discord.py would.
    callback = cast(Any, command.callback)
    asyncio.run(callback(cog, ctx_or_interaction))


def seed_scores(store, board, points_by_user):
    for user_id, points in points_by_user.items():
        store._add_points(board, GUILD_ID, user_id, points)


def test_cog_registers_slash_and_prefix_commands():
    cog = make_cog(make_store())
    names = {"leaderboard", "wosleaderboard", "position", "wosposition"}

    assert {command.name for command in cog.get_commands()} == names
    assert {command.name for command in cog.get_app_commands()} == names


def test_leaderboard_embed_shows_top_ten():
    store = make_store()
    seed_scores(store, "onphone", {user_id: user_id for user_id in range(1, 13)})
    guild = FakeGuild({12: "twelve", 11: "e_l*even"})
    cog = make_cog(store, users={10: SimpleNamespace(display_name="ten")})

    embed = cog.build_top_embed(cast(discord.Guild, guild), ONPHONE_BOARD)

    assert embed.title == "OnPhone Leaderboard"
    lines = (embed.description or "").split("\n\n", 1)[1].split("\n")
    assert len(lines) == 10
    assert lines[0] == "**1.** twelve — 12 pts"
    assert lines[1] == "**2.** e\\_l\\*even — 11 pts"
    assert lines[2] == "**3.** ten — 10 pts"
    assert lines[9] == "**10.** <@3> — 3 pts"


def test_wos_leaderboard_uses_ban_scores():
    store = make_store()
    seed_scores(store, "onphone", {1: 5})
    seed_scores(store, "ban", {2: 1})
    cog = make_cog(store)

    embed = cog.build_top_embed(cast(discord.Guild, FakeGuild({})), WALL_OF_SHAME_BOARD)

    assert embed.title == "Wall of Shame Leaderboard"
    assert "**1.** <@2> — 1 pt" in (embed.description or "")
    assert "<@1>" not in (embed.description or "")


def test_empty_leaderboard_embed():
    cog = make_cog(make_store())

    embed = cog.build_top_embed(cast(discord.Guild, FakeGuild({})), ONPHONE_BOARD)

    assert "No scores yet." in (embed.description or "")


def test_position_embed_highlights_caller():
    store = make_store()
    seed_scores(store, "onphone", {user_id: 100 - user_id for user_id in range(20)})
    cog = make_cog(store)

    embed = cog.build_position_embed(
        cast(discord.Guild, FakeGuild({})), 10, ONPHONE_BOARD
    )

    description = embed.description or ""
    assert "You are **#11** of 20 with **90 pts**." in description
    assert "**11. <@10> — 90 pts** ← you" in description
    assert description.count("\n**") == 11


def test_position_embed_for_user_without_points():
    store = make_store()
    seed_scores(store, "onphone", {1: 2, 2: -1})
    cog = make_cog(store)

    embed = cog.build_position_embed(
        cast(discord.Guild, FakeGuild({})), 3, ONPHONE_BOARD
    )

    assert "You are **#2** of 3 with **0 pts**." in (embed.description or "")


def test_slash_leaderboard_is_visible_to_everyone():
    cog = make_cog(make_store())
    intr = make_interaction(FakeGuild({}))

    run_command(cog, cog.leaderboard_slash, intr)

    kwargs = intr.response.send_message.call_args.kwargs
    assert kwargs["embed"].title == "OnPhone Leaderboard"
    assert not kwargs.get("ephemeral", False)


def test_prefix_wosleaderboard_is_sent_to_the_channel():
    cog = make_cog(make_store())
    ctx = make_ctx(FakeGuild({}))

    run_command(cog, cog.wosleaderboard_prefix, ctx)

    assert ctx.send.call_args.kwargs["embed"].title == "Wall of Shame Leaderboard"
    ctx.author.send.assert_not_called()


def test_slash_position_replies_ephemerally():
    cog = make_cog(make_store())
    intr = make_interaction(FakeGuild({}))

    run_command(cog, cog.position_slash, intr)

    kwargs = intr.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].title == "Your OnPhone Leaderboard Position"


def test_slash_wosposition_replies_ephemerally():
    cog = make_cog(make_store())
    intr = make_interaction(FakeGuild({}))

    run_command(cog, cog.wosposition_slash, intr)

    kwargs = intr.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].title == "Your Wall of Shame Leaderboard Position"


def test_prefix_position_is_sent_by_dm():
    cog = make_cog(make_store())
    ctx = make_ctx(FakeGuild({}))

    run_command(cog, cog.position_prefix, ctx)

    ctx.author.send.assert_awaited_once()
    embed = ctx.author.send.call_args.kwargs["embed"]
    assert embed.title == "Your OnPhone Leaderboard Position"
    ctx.send.assert_not_called()


def test_prefix_position_with_closed_dms_points_to_slash_command():
    cog = make_cog(make_store())
    ctx = make_ctx(FakeGuild({}), dm_error=make_forbidden())

    run_command(cog, cog.wosposition_prefix, ctx)

    ctx.reply.assert_awaited_once()
    assert "/wosposition" in ctx.reply.call_args.args[0]
    assert ctx.reply.call_args.kwargs["delete_after"] > 0


def test_leaderboard_commands_require_a_server():
    cog = make_cog(make_store())
    ctx = make_ctx(None)
    intr = make_interaction(None)

    run_command(cog, cog.leaderboard_prefix, ctx)
    run_command(cog, cog.position_prefix, ctx)
    run_command(cog, cog.position_slash, intr)

    assert ctx.send.await_count == 2
    ctx.send.assert_awaited_with("Leaderboards only work in a server.")
    ctx.author.send.assert_not_called()
    intr.response.send_message.assert_awaited_once_with(
        "Leaderboards only work in a server.", ephemeral=True
    )


def test_leaderboard_is_not_loaded_without_a_database(tmp_path):
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("")

    store = open_leaderboard_store(str(not_a_directory / "leaderboard.db"))

    assert store is None
    with pytest.raises(RuntimeError):
        asyncio.run(setup_leaderboard(make_bot(), [], store))
