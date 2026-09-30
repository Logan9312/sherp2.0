import asyncio
import logging
import sqlite3
from typing import Any, Callable, List, Optional, cast
import discord
from discord.ext import commands

from .helpers.board_store import BoardStore

logger = logging.getLogger(__name__)

PROPAGANDA_GIF_URL = "https://media1.tenor.com/m/zdkG7NnnREoAAAAd/propaganda.gif"


class MessageBoard(commands.Cog):
    # To implement a new board, subclass this and pass the board's channel, primary emoji and threshold to ``__init__``.
    # Override ``_get_threshold`` to change which emojis qualify and at what counts, return the threshold for a qualifying emoji.
    # Use ``None`` for an emoji that can never repost a message to the board.
    # Add the primary emoji of new board as a default excluded emoji into a list in starboard.py somewhere at the beginning.
    # Add the new board to the bot in bot.py, and add a config section for it in bot_config.toml.
    # To persist the board's posts across restarts and score its primary emoji on a leaderboard,
    # set ``storage_key`` to a stable key and pass a ``BoardStore`` as ``store`` to ``__init__``.
    # Only a board with a store handles messages that are not in discord.py's message cache.

    board_name = "message board"
    storage_key: Optional[str] = None

    def __init__(
        self,
        bot: discord.Client,
        *,
        channel_id: int,
        primary_emoji_str: str,
        primary_threshold: int,
        embed_color: discord.Color = discord.Color.dark_green(),
        store: Optional[BoardStore] = None,
    ):
        self.bot = bot
        self.primary_emoji_str = primary_emoji_str
        self.primary_threshold = primary_threshold
        self.board_channel_id = channel_id
        self.embed_color = embed_color
        self.board_msgs = dict()
        self.board_channel = bot.get_channel(self.board_channel_id)
        self.store = store
        # Serializes every board and score update, so events that need to fetch
        # a message are still applied in the order Discord sent them.
        self._lock = asyncio.Lock()

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

    def _get_store(self) -> Optional[tuple[BoardStore, str]]:
        if self.store is None or self.storage_key is None:
            return None
        return self.store, self.storage_key

    def _store_call(self, method: Callable[..., Any], *args: Any) -> Any:
        # Database failures are logged but must never stop the board itself.
        try:
            return method(*args)
        except sqlite3.Error:
            logger.exception(
                "Failed to update %s store via %s args=%s",
                self.board_name,
                getattr(method, "__name__", method),
                args,
            )
            return None

    def _is_scored_emoji(self, emoji: object) -> bool:
        return self._get_store() is not None and str(emoji) == self.primary_emoji_str

    def _get_scored_author_id(self, msg: discord.Message) -> Optional[int]:
        # Bots never earn author points on the leaderboard.
        author = msg.author
        return None if getattr(author, "bot", False) else author.id

    def _record_reaction_add(self, react: discord.Reaction, user) -> None:
        stored = self._get_store()
        if stored is None or str(react.emoji) != self.primary_emoji_str:
            return
        if getattr(user, "bot", False):
            return

        msg = react.message
        guild = getattr(msg, "guild", None)
        if guild is None or user.id == msg.author.id:
            return

        store, key = stored
        self._store_call(
            store.add_reaction,
            key,
            guild.id,
            msg.id,
            self._get_scored_author_id(msg),
            user.id,
        )

    def _record_board_post(self, msg: discord.Message) -> None:
        stored = self._get_store()
        guild = getattr(msg, "guild", None)
        if stored is None or guild is None:
            return

        store, key = stored
        self._store_call(
            store.mark_boarded, key, guild.id, msg.id, self._get_scored_author_id(msg)
        )

    def _get_board_record(self, message_id: int) -> Optional[dict]:
        # Board posts are cached in ``board_msgs`` and, with a store, persisted
        # so they are still known after a restart.
        record = self.board_msgs.get(message_id)
        stored = self._get_store()
        if record is not None or stored is None:
            return record

        store, key = stored
        post = self._store_call(store.get_board_post, key, message_id)
        if post is None:
            return None

        post_id, channel_id, emoji = post
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = self.bot.get_partial_messageable(channel_id)
        record = {"post_id": post_id, "emoji": emoji, "channel": channel}
        self.board_msgs[message_id] = record
        return record

    def _save_board_record(
        self, message_id: int, post: discord.Message, channel: object, emoji: str
    ) -> None:
        self.board_msgs[message_id] = {
            "post_id": post.id,
            "emoji": emoji,
            "channel": channel,
        }
        stored = self._get_store()
        channel_id = self._get_channel_id(channel)
        if stored is None or channel_id is None:
            return

        store, key = stored
        self._store_call(
            store.save_board_post, key, message_id, post.id, channel_id, emoji
        )

    def _is_cached(self, message_id: int) -> bool:
        cached = getattr(self.bot, "cached_messages", None) or ()
        return any(msg.id == message_id for msg in cached)

    def _needs_uncached_handling(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> bool:
        # discord.py only fires on_reaction_add/remove for messages in its message
        # cache, which starts empty after a restart and only holds recent
        # messages. Reactions on any other message are handled from the raw event
        # by fetching the message. That needs a store, otherwise the board can't
        # tell whether it already posted the message before the restart.
        if self._get_store() is None or payload.guild_id is None:
            return False
        if payload.channel_id == self.board_channel_id:
            return False
        emoji = str(payload.emoji)
        if emoji != self.primary_emoji_str and self._get_threshold(emoji) is None:
            return False
        return not self._is_cached(payload.message_id)

    async def _fetch_message(
        self, channel_id: int, message_id: int
    ) -> Optional[discord.Message]:
        try:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
            fetch_message = getattr(channel, "fetch_message", None)
            if fetch_message is None:
                return None
            return await fetch_message(message_id)
        except discord.HTTPException:
            logger.warning(
                "Failed to fetch %s source message source_message_id=%s "
                "source_channel_id=%s",
                self.board_name,
                message_id,
                channel_id,
                exc_info=True,
            )
            return None

    def _find_reaction(
        self, msg: discord.Message, emoji: object
    ) -> Optional[discord.Reaction]:
        for reaction in msg.reactions:
            if str(reaction.emoji) == str(emoji):
                return reaction
        return None

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

    def _get_message_title(
        self, msg: discord.Message, fallback_react: Optional[discord.Reaction] = None
    ) -> Optional[str]:
        reactions = self._get_qualifying_reactions(msg, fallback_react)
        if not reactions:
            return None

        counts = " ".join(
            f"{reaction.emoji} x **{reaction.count}**" for reaction in reactions
        )
        return f"{counts} |{msg.channel.mention}"

    def _get_title(self, react: discord.Reaction) -> Optional[str]:
        return self._get_message_title(react.message, react)

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
        await self._refresh_board_post(react.message, react)

    async def _refresh_board_post(
        self, source: discord.Message, fallback_react: Optional[discord.Reaction]
    ) -> None:
        title = self._get_message_title(source, fallback_react)
        if title is None:
            await self.delete_board_post(source.id)
            return

        record = self._get_board_record(source.id)
        if record is None:
            return
        msg_id = record["post_id"]
        channel = record.get("channel", self.board_channel)
        msg: discord.Message = await channel.fetch_message(msg_id)
        await msg.edit(content=title)

    async def delete_board_post(self, message_id: int) -> None:
        record = self._get_board_record(message_id)
        if not record:
            return

        self.board_msgs.pop(message_id, None)
        stored = self._get_store()
        if stored is not None:
            store, key = stored
            self._store_call(store.delete_board_post, key, message_id)

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
        ).set_author(
            name=msg.author.display_name, icon_url=msg.author.display_avatar.url
        )

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
        self._save_board_record(react.message.id, msg, channel, str(react.emoji))
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

    async def _handle_reaction_add(
        self, react: discord.Reaction, user: discord.User | discord.Member
    ) -> None:
        if not self._is_eligible_channel(react.message.channel):
            return

        self._record_reaction_add(react, user)

        emoji = str(react.emoji)
        threshold = self._get_threshold(emoji)
        if threshold is None or react.count < threshold:
            return

        if self._get_board_record(react.message.id) is not None:
            await self.update_reaction_count(react)
        else:
            await self.create_board_post(react)

    @commands.Cog.listener()
    async def on_reaction_add(
        self, react: discord.Reaction, user: discord.User | discord.Member
    ):
        async with self._lock:
            await self._handle_reaction_add(react, user)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.member is None or not self._needs_uncached_handling(payload):
            return

        async with self._lock:
            msg = await self._fetch_message(payload.channel_id, payload.message_id)
            if msg is None:
                return
            react = self._find_reaction(msg, payload.emoji)
            if react is None:
                return  # The reaction was removed again before the fetch.
            await self._handle_reaction_add(react, payload.member)

    @commands.Cog.listener()
    async def on_reaction_remove(
        self, react: discord.Reaction, _: discord.User | discord.Member
    ):
        async with self._lock:
            if self._get_board_record(react.message.id) is None:
                return
            await self.update_reaction_count(react)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        async with self._lock:
            # Scores only need the IDs, so removals are recorded here for cached
            # and uncached messages alike.
            stored = self._get_store()
            if stored is not None and self._is_scored_emoji(payload.emoji):
                store, key = stored
                self._store_call(
                    store.remove_reaction, key, payload.message_id, payload.user_id
                )

            if not self._needs_uncached_handling(payload):
                return
            if self._get_board_record(payload.message_id) is None:
                return
            msg = await self._fetch_message(payload.channel_id, payload.message_id)
            if msg is not None:
                await self._refresh_board_post(msg, None)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent):
        async with self._lock:
            stored = self._get_store()
            if stored is not None:
                store, key = stored
                self._store_call(store.clear_reactions, key, payload.message_id)
            await self.delete_board_post(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(
        self, payload: discord.RawReactionClearEmojiEvent
    ):
        async with self._lock:
            stored = self._get_store()
            if stored is not None and self._is_scored_emoji(payload.emoji):
                store, key = stored
                self._store_call(store.clear_reactions, key, payload.message_id)

            if self._get_threshold(str(payload.emoji)) is None:
                return
            if self._get_board_record(payload.message_id) is None:
                return
            msg = await self._fetch_message(payload.channel_id, payload.message_id)
            if msg is not None:
                await self._refresh_board_post(msg, None)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        async with self._lock:
            stored = self._get_store()
            if stored is not None:
                store, key = stored
                self._store_call(store.forget_pending_message, key, payload.message_id)
            await self.delete_board_post(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(
        self, payload: discord.RawBulkMessageDeleteEvent
    ):
        async with self._lock:
            for message_id in payload.message_ids:
                stored = self._get_store()
                if stored is not None:
                    store, key = stored
                    self._store_call(store.forget_pending_message, key, message_id)
                await self.delete_board_post(message_id)
