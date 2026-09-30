from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from .helpers.board_store import BACKER_REWARD, REACTION_COST, BoardStore
from .starboard import STARBOARD_ONPHONE_EMOJI_STR, Starboard
from .wallofshame import WALL_OF_SHAME_BAN_EMOJI_STR, WallOfShame

TOP_COUNT = 10
POSITION_RADIUS = 5
DM_FAILURE_DELETE_AFTER = 15
NOT_IN_GUILD_MESSAGE = "Leaderboards only work in a server."

LEADERBOARD_HELP = "Show the top 10 of the OnPhone leaderboard."
WOS_LEADERBOARD_HELP = "Show the top 10 of the wall of shame leaderboard."
POSITION_HELP = "Privately show your place on the OnPhone leaderboard."
WOS_POSITION_HELP = "Privately show your place on the wall of shame leaderboard."


@dataclass(frozen=True)
class BoardInfo:
    key: str  # The ``storage_key`` of the board that scores it.
    title: str
    emoji: str
    board_label: str
    position_command: str
    color: discord.Color


ONPHONE_BOARD = BoardInfo(
    key=Starboard.storage_key,
    title="OnPhone Leaderboard",
    emoji=STARBOARD_ONPHONE_EMOJI_STR,
    board_label="starboard",
    position_command="position",
    color=discord.Color.dark_green(),
)
WALL_OF_SHAME_BOARD = BoardInfo(
    key=WallOfShame.storage_key,
    title="Wall of Shame Leaderboard",
    emoji=WALL_OF_SHAME_BAN_EMOJI_STR,
    board_label="wall of shame",
    position_command="wosposition",
    color=discord.Color.dark_red(),
)


@dataclass(frozen=True)
class RankedEntry:
    rank: int
    user_id: int
    score: int


def rank_scores(
    scores: Iterable[Tuple[int, int]], include_user_id: Optional[int] = None
) -> List[RankedEntry]:
    # Orders ``(user_id, score)`` pairs from highest to lowest score. Tied users
    # share a rank (1, 2, 2, 4). ``include_user_id`` is added with a score of 0
    # if they have not scored yet, since everyone starts at 0.
    scores = list(scores)
    if include_user_id is not None and all(
        user_id != include_user_id for user_id, _ in scores
    ):
        scores.append((include_user_id, 0))

    ranked: List[RankedEntry] = []
    for index, (user_id, score) in enumerate(
        sorted(scores, key=lambda entry: (-entry[1], entry[0]))
    ):
        rank = ranked[-1].rank if ranked and ranked[-1].score == score else index + 1
        ranked.append(RankedEntry(rank=rank, user_id=user_id, score=score))
    return ranked


def position_window(
    ranked: List[RankedEntry], user_id: int, radius: int = POSITION_RADIUS
) -> List[RankedEntry]:
    # Returns the user's entry with up to ``radius`` entries above and below it.
    index = next(i for i, entry in enumerate(ranked) if entry.user_id == user_id)
    return ranked[max(0, index - radius) : index + radius + 1]


def format_points(score: int) -> str:
    return f"{score:,} pt" if abs(score) == 1 else f"{score:,} pts"


class Leaderboard(commands.Cog):
    def __init__(self, bot, store: BoardStore):
        self.bot = bot
        self.store = store

    async def cog_load(self):
        await super().cog_load()
        print("Leaderboard Cog loaded.")

    def _display_name(self, guild: discord.Guild, user_id: int) -> str:
        user = guild.get_member(user_id) or self.bot.get_user(user_id)
        if user is None:
            return f"<@{user_id}>"
        return discord.utils.escape_markdown(user.display_name)

    def _format_entry(
        self, guild: discord.Guild, entry: RankedEntry, highlight: bool = False
    ) -> str:
        name = self._display_name(guild, entry.user_id)
        points = format_points(entry.score)
        if highlight:
            return f"**{entry.rank}. {name} — {points}** ← you"
        return f"**{entry.rank}.** {name} — {points}"

    def _get_footer(self, board: BoardInfo) -> str:
        return (
            f"Reacting costs {REACTION_COST} · reactors get +{BACKER_REWARD} if "
            f"the message makes the {board.board_label} · its author gets 1 per "
            "reaction"
        )

    def build_top_embed(self, guild: discord.Guild, board: BoardInfo) -> discord.Embed:
        ranked = rank_scores(self.store.get_scores(board.key, guild.id))
        lines = [self._format_entry(guild, entry) for entry in ranked[:TOP_COUNT]]
        body = "\n".join(lines) if lines else "No scores yet."
        return discord.Embed(
            title=board.title,
            description=f"{board.emoji} **Top {TOP_COUNT}**\n\n{body}",
            color=board.color,
        ).set_footer(text=self._get_footer(board))

    def build_position_embed(
        self, guild: discord.Guild, user_id: int, board: BoardInfo
    ) -> discord.Embed:
        ranked = rank_scores(
            self.store.get_scores(board.key, guild.id), include_user_id=user_id
        )
        window = position_window(ranked, user_id)
        me = next(entry for entry in window if entry.user_id == user_id)
        lines = [
            self._format_entry(guild, entry, highlight=entry.user_id == user_id)
            for entry in window
        ]
        header = (
            f"{board.emoji} You are **#{me.rank}** of {len(ranked)} with "
            f"**{format_points(me.score)}**."
        )
        return discord.Embed(
            title=f"Your {board.title} Position",
            description=header + "\n\n" + "\n".join(lines),
            color=board.color,
        ).set_footer(text=self._get_footer(board))

    async def _send_top(self, ctx: commands.Context, board: BoardInfo) -> None:
        if ctx.guild is None:
            await ctx.send(NOT_IN_GUILD_MESSAGE)
            return

        await ctx.send(embed=self.build_top_embed(ctx.guild, board))

    async def _send_position(self, ctx: commands.Context, board: BoardInfo) -> None:
        if ctx.guild is None:
            await ctx.send(NOT_IN_GUILD_MESSAGE)
            return

        # Prefix commands can't reply privately in a channel, so DM instead.
        embed = self.build_position_embed(ctx.guild, ctx.author.id, board)
        try:
            await ctx.author.send(embed=embed)
        except discord.Forbidden:
            await ctx.reply(
                "I couldn't DM you. Use "
                f"`/{board.position_command}` to see your position privately.",
                mention_author=False,
                delete_after=DM_FAILURE_DELETE_AFTER,
            )

    async def _respond_top(self, intr: discord.Interaction, board: BoardInfo) -> None:
        if intr.guild is None:
            await intr.response.send_message(NOT_IN_GUILD_MESSAGE, ephemeral=True)
            return

        await intr.response.send_message(embed=self.build_top_embed(intr.guild, board))

    async def _respond_position(
        self, intr: discord.Interaction, board: BoardInfo
    ) -> None:
        if intr.guild is None:
            await intr.response.send_message(NOT_IN_GUILD_MESSAGE, ephemeral=True)
            return

        await intr.response.send_message(
            embed=self.build_position_embed(intr.guild, intr.user.id, board),
            ephemeral=True,
        )

    # Each command exists as a slash command and a ``?`` prefix command. They
    # aren't hybrid commands because those are registered globally, while the
    # bot only syncs slash commands for its configured guilds.

    @app_commands.command(name="leaderboard", description=LEADERBOARD_HELP)
    async def leaderboard_slash(self, intr: discord.Interaction):
        await self._respond_top(intr, ONPHONE_BOARD)

    @commands.command(name="leaderboard", help=LEADERBOARD_HELP)
    async def leaderboard_prefix(self, ctx: commands.Context):
        await self._send_top(ctx, ONPHONE_BOARD)

    @app_commands.command(name="wosleaderboard", description=WOS_LEADERBOARD_HELP)
    async def wosleaderboard_slash(self, intr: discord.Interaction):
        await self._respond_top(intr, WALL_OF_SHAME_BOARD)

    @commands.command(name="wosleaderboard", help=WOS_LEADERBOARD_HELP)
    async def wosleaderboard_prefix(self, ctx: commands.Context):
        await self._send_top(ctx, WALL_OF_SHAME_BOARD)

    @app_commands.command(name="position", description=POSITION_HELP)
    async def position_slash(self, intr: discord.Interaction):
        await self._respond_position(intr, ONPHONE_BOARD)

    @commands.command(name="position", help=POSITION_HELP)
    async def position_prefix(self, ctx: commands.Context):
        await self._send_position(ctx, ONPHONE_BOARD)

    @app_commands.command(name="wosposition", description=WOS_POSITION_HELP)
    async def wosposition_slash(self, intr: discord.Interaction):
        await self._respond_position(intr, WALL_OF_SHAME_BOARD)

    @commands.command(name="wosposition", help=WOS_POSITION_HELP)
    async def wosposition_prefix(self, ctx: commands.Context):
        await self._send_position(ctx, WALL_OF_SHAME_BOARD)


async def setup_leaderboard(bot, guilds, store: Optional[BoardStore]):
    if store is None:
        raise RuntimeError("Leaderboards need the board database, which failed to open")
    await bot.add_cog(Leaderboard(bot, store), guilds=guilds)
