import logging
import sqlite3
from typing import Any, Callable, List, Optional, cast
import discord
from discord.ext import commands

from .helpers.leaderboard_store import LeaderboardStore

logger = logging.getLogger(__name__)

PROPAGANDA_GIF_URL = "https://media1.tenor.com/m/zdkG7NnnREoAAAAd/propaganda.gif"


class MessageBoard(commands.Cog):
    # To implement a new board, subclass this and pass the board's channel, primary emoji and threshold to ``__init__``.
    # Override ``_get_threshold`` to change which emojis qualify and at what counts, return the threshold for a qualifying emoji.
    # Use ``None`` for an emoji that can never repost a message to the board.
    # Add the primary emoji of new board as a default excluded emoji into a list in starboard.py somewhere at the beginning.
    # Add the new board to the bot in bot.py, and add a config section for it in bot_config.toml.
    # To score the primary emoji on a leaderboard, set ``leaderboard_key`` to a stable storage key
    # and pass a ``LeaderboardStore`` as ``leaderboard`` to ``__init__``.

    board_name = "message board"
    leaderboard_key: Optional[str] = None

    def __init__(
        self,
        bot: discord.Client,
        *,
        channel_id: int,
        primary_emoji_str: str,
        primary_threshold: int,
        embed_color: discord.Color = discord.Color.dark_green(),
        leaderboard: Optional[LeaderboardStore] = None,
    ):
        self.bot = bot
        self.primary_emoji_str = primary_emoji_str
        self.primary_threshold = primary_threshold
        self.board_channel_id = channel_id
        self.embed_color = embed_color
        self.board_msgs = dict()
        self.board_channel = bot.get_channel(self.board_channel_id)
        self.leaderboard = leaderboard

    async def cog_load(self):
        await super().cog_load()
        print(f"{type(self).__name__} Cog loaded.")

    def _get_threshold(self, emoji: str) -> Optional[int]:
        if emoji == self.primary_emoji_str:
            return self.primary_threshold
        return None

    def _get_channel_id(self, channel: object) -> Optional[int]:
        return getattr(channel, "id", None)

    def _is_eligible_channel(self, channel) -> bool:
        if channel.is_nsfw():
            return False
        return getattr(channel, "id", None) != self.board_channel_id

    def _leaderboard_call(self, method: Callable[..., Any], *args: Any) -> None:
        # Leaderboard failures are logged but must never stop the board itself.
        try:
            method(*args)
        except sqlite3.Error:
            logger.exception(
                "Failed to update %s leaderboard via %s args=%s",
                self.board_name,
                getattr(method, "__name__", method),
                args,
            )

    def _is_scored_emoji(self, emoji: object) -> bool:
        return (
            self.leaderboard is not None
            and self.leaderboard_key is not None
            and str(emoji) == self.primary_emoji_str
        )

    def _get_scored_author_id(self, msg: discord.Message) -> Optional[int]:
        # Bots never earn author points on the leaderboard.
        author = msg.author
        return None if getattr(author, "bot", False) else author.id

    def _record_reaction_add(self, react: discord.Reaction, user) -> None:
        if not self._is_scored_emoji(react.emoji) or getattr(user, "bot", False):
            return

        msg = react.message
        guild = getattr(msg, "guild", None)
        if guild is None or user.id == msg.author.id:
            return

        store = cast(LeaderboardStore, self.leaderboard)
        self._leaderboard_call(
            store.add_reaction,
            self.leaderboard_key,
            guild.id,
            msg.id,
            self._get_scored_author_id(msg),
            user.id,
        )

    def _record_reaction_remove(self, react: discord.Reaction, user) -> None:
        if not self._is_scored_emoji(react.emoji) or getattr(user, "bot", False):
            return

        store = cast(LeaderboardStore, self.leaderboard)
        self._leaderboard_call(
            store.remove_reaction, self.leaderboard_key, react.message.id, user.id
        )

    def _record_reactions_cleared(self, message_id: int) -> None:
        if self.leaderboard is None or self.leaderboard_key is None:
            return

        self._leaderboard_call(
            self.leaderboard.clear_reactions, self.leaderboard_key, message_id
        )

    def _record_board_post(self, msg: discord.Message) -> None:
        if self.leaderboard is None or self.leaderboard_key is None:
            return

        guild = getattr(msg, "guild", None)
        if guild is None:
            return

        self._leaderboard_call(
            self.leaderboard.mark_boarded,
            self.leaderboard_key,
            guild.id,
            msg.id,
            self._get_scored_author_id(msg),
        )

    def _record_message_deleted(self, message_id: int) -> None:
        if self.leaderboard is None or self.leaderboard_key is None:
            return

        self._leaderboard_call(
            self.leaderboard.forget_pending_message, self.leaderboard_key, message_id
        )

    def _get_qualifying_reactions(
        self, msg: discord.Message, fallback_react: Optional[discord.Reaction] = None
    ) -> List[discord.Reaction]:
        reactions = list(getattr(msg, "reactions", None) or [])
        if fallback_react and all(
            str(reaction.emoji) != str(fallback_react.emoji) for reaction in reactions
        ):
            reactions.append(fallback_react)

        qualifying = []
        for reaction in reactions:
            threshold = self._get_threshold(str(reaction.emoji))
            if threshold is not None and reaction.count >= threshold:
                qualifying.append(reaction)
        return qualifying

    def _get_title(self, react: discord.Reaction) -> Optional[str]:
        reactions = self._get_qualifying_reactions(react.message, react)
        if not reactions:
            return None

        counts = " ".join(
            f"{reaction.emoji} x **{reaction.count}**" for reaction in reactions
        )
        return f"{counts} |{react.message.channel.mention}"

    async def _get_open_msg_view(self, msg: discord.Message) -> discord.ui.View:
        btn = discord.ui.Button(label="Jump", url=msg.jump_url)
        v = discord.ui.View().add_item(btn)

        if msg.type != discord.MessageType.reply:
            return v

        reply = msg.reference.cached_message or await msg.channel.fetch_message(
            msg.reference.message_id
        )
        return v.add_item(discord.ui.Button(label="Context", url=reply.jump_url))

    async def update_reaction_count(self, react: discord.Reaction) -> None:
        title = self._get_title(react)
        if title is None:
            await self.delete_board_post(react.message.id)
            return

        record = self.board_msgs[react.message.id]
        msg_id = record["post_id"]
        channel = record.get("channel", self.board_channel)
        msg: discord.Message = await channel.fetch_message(msg_id)
        await msg.edit(content=title)

    async def delete_board_post(self, message_id: int) -> None:
        record = self.board_msgs.pop(message_id, None)
        if not record:
            return

        channel = record.get("channel", self.board_channel)
        try:
            msg = await channel.fetch_message(record["post_id"])
            await msg.delete()
        except discord.HTTPException:
            logger.warning(
                "Failed to delete %s post source_message_id=%s "
                "board_post_id=%s board_channel_id=%s",
                self.board_name,
                message_id,
                record["post_id"],
                self._get_channel_id(channel),
                exc_info=True,
            )

    def _get_first_viable_attachment_url(
        self, atmnts: List[discord.Attachment]
    ) -> Optional[str]:
        for a in atmnts:
            if a.url.split("?")[0].endswith(("png", "jpeg", "jpg", "gif", "webp")):
                return a.url
        return None

    def _get_board_embed(self, msg: discord.Message) -> discord.Embed:
        embed = discord.Embed(
            description=msg.content or msg.system_content,
            color=self.embed_color,
        ).set_author(name=msg.author.display_name, icon_url=msg.author.avatar.url)

        if u := self._get_first_viable_attachment_url(msg.attachments):
            embed.set_image(url=u)

        return embed

    async def _build_embeds(self, msg: discord.Message) -> List[discord.Embed]:
        main_embed = self._get_board_embed(msg)

        if msg.type != discord.MessageType.reply:
            return [main_embed]

        reply_to = msg.reference.cached_message or await msg.channel.fetch_message(
            msg.reference.message_id
        )
        atcmnt_url = self._get_first_viable_attachment_url(reply_to.attachments)
        if not atcmnt_url:
            main_embed.add_field(
                name="Reply to the message:",
                value=reply_to.content or reply_to.system_content,
            )
            return [main_embed]

        reply_embed = discord.Embed(
            title="Reply to this message:",
            description=reply_to.content or reply_to.system_content,
            color=self.embed_color,
        ).set_image(url=atcmnt_url)

        return [main_embed, reply_embed]

    def _get_no_board_access_embed(self) -> discord.Embed:
        return discord.Embed(
            description=(
                f"I would love to {self.board_name} your message but I don't "
                f"have access to that channel. Please {self.primary_emoji_str} "
                "my petition"
            ),
            color=discord.Color.gold(),
        ).set_image(url=PROPAGANDA_GIF_URL)

    async def _try_add_board_reaction(
        self, msg: discord.Message, emoji: discord.PartialEmoji | discord.Emoji | str
    ) -> None:
        try:
            await msg.add_reaction(emoji)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Failed to add reaction to %s post board_post_id=%s "
                "board_channel_id=%s emoji=%s",
                self.board_name,
                getattr(msg, "id", None),
                self._get_channel_id(getattr(msg, "channel", None)),
                emoji,
                exc_info=True,
            )

    async def _send_board_copy(
        self,
        channel: discord.abc.Messageable,
        react: discord.Reaction,
        embeds: List[discord.Embed],
        open_msg_view: discord.ui.View,
    ) -> None:
        title = self._get_title(react)
        if title is None:
            return

        msg: discord.Message = await channel.send(
            title, embeds=embeds, view=open_msg_view
        )
        self.board_msgs[react.message.id] = {
            "post_id": msg.id,
            "emoji": str(react.emoji),
            "channel": channel,
        }
        self._record_board_post(react.message)
        await self._try_add_board_reaction(msg, react.emoji)

    async def _handle_board_send_failure(
        self,
        react: discord.Reaction,
        embeds: List[discord.Embed],
        open_msg_view: discord.ui.View,
    ) -> None:
        fallback_embeds = embeds + [self._get_no_board_access_embed()]
        try:
            await self._send_board_copy(
                react.message.channel, react, fallback_embeds, open_msg_view
            )
        except discord.HTTPException:
            logger.exception(
                "Failed to send %s fallback source_message_id=%s "
                "source_channel_id=%s board_channel_id=%s emoji=%s "
                "reaction_count=%s",
                self.board_name,
                react.message.id,
                self._get_channel_id(react.message.channel),
                self.board_channel_id,
                react.emoji,
                react.count,
            )
            raise

    async def create_board_post(self, react: discord.Reaction):
        embeds = await self._build_embeds(react.message)
        open_msg_view = await self._get_open_msg_view(react.message)

        if self.board_channel is None:
            logger.warning(
                "%s channel unavailable; sending fallback "
                "source_message_id=%s source_channel_id=%s "
                "board_channel_id=%s emoji=%s reaction_count=%s",
                self.board_name,
                react.message.id,
                self._get_channel_id(react.message.channel),
                self.board_channel_id,
                react.emoji,
                react.count,
            )
            await self._handle_board_send_failure(react, embeds, open_msg_view)
            return

        board_channel = cast(discord.abc.Messageable, self.board_channel)
        try:
            await self._send_board_copy(board_channel, react, embeds, open_msg_view)
        except discord.HTTPException:
            logger.exception(
                "Failed to send %s post to configured channel "
                "source_message_id=%s source_channel_id=%s "
                "board_channel_id=%s emoji=%s reaction_count=%s",
                self.board_name,
                react.message.id,
                self._get_channel_id(react.message.channel),
                self.board_channel_id,
                react.emoji,
                react.count,
            )
            await self._handle_board_send_failure(react, embeds, open_msg_view)

    @commands.Cog.listener()
    async def on_reaction_add(
        self, react: discord.Reaction, user: discord.User | discord.Member
    ):
        if not self._is_eligible_channel(react.message.channel):
            return

        self._record_reaction_add(react, user)

        emoji = str(react.emoji)
        threshold = self._get_threshold(emoji)
        if threshold is None or react.count < threshold:
            return

        if react.message.id in self.board_msgs:
            await self.update_reaction_count(react)
        else:
            await self.create_board_post(react)

    @commands.Cog.listener()
    async def on_reaction_remove(
        self, react: discord.Reaction, user: discord.User | discord.Member
    ):
        self._record_reaction_remove(react, user)

        if react.message.id not in self.board_msgs:
            return

        await self.update_reaction_count(react)

    @commands.Cog.listener()
    async def on_reaction_clear(
        self, msg: discord.Message, _: List[discord.Reaction]
    ) -> None:
        self._record_reactions_cleared(msg.id)

    @commands.Cog.listener()
    async def on_reaction_clear_emoji(self, react: discord.Reaction) -> None:
        if self._is_scored_emoji(react.emoji):
            self._record_reactions_cleared(react.message.id)

    @commands.Cog.listener()
    async def on_message_delete(self, msg: discord.Message):
        self._record_message_deleted(msg.id)
        await self.delete_board_post(msg.id)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, msgs: List[discord.Message]) -> None:
        for msg in msgs:
            self._record_message_deleted(msg.id)
