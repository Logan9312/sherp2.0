from typing import Optional
import discord

from helper import get_config

from .helpers.leaderboard_store import LeaderboardStore
from .message_board import MessageBoard

__DEFAULT_CHANNEL_ID = 1133260871049691257
__DEFAULT_ONPHONE_EMOJI_STR = "<:OnPhone:1062142401973588039>"
__DEFAULT_ONPHONE_THRESHOLD = 3
# This is for starboard only, 5 of any emoji other than OnPhone will also work.
# In a way, this is why the starboard is a main boss board, since it can be triggered by any emoji.
__DEFAULT_OTHER_THRESHOLD = 5
# Add primary emojis of other boards to this list so they don't go on to starboard.
__DEFAULT_EXCLUDED_EMOJIS = ["<:ban:939740396286791741>"]

__cfg = get_config().get("starboard", None)
STARBOARD_CHANNEL_ID = (
    __cfg.get("channel", __DEFAULT_CHANNEL_ID) if __cfg else __DEFAULT_CHANNEL_ID
)
STARBOARD_ONPHONE_EMOJI_STR = (
    __cfg.get("onphone_emoji", __cfg.get("emoji", __DEFAULT_ONPHONE_EMOJI_STR))
    if __cfg
    else __DEFAULT_ONPHONE_EMOJI_STR
)

STARBOARD_ONPHONE_THRESHOLD = (
    __cfg.get("onphone_threshold", __DEFAULT_ONPHONE_THRESHOLD)
    if __cfg
    else __DEFAULT_ONPHONE_THRESHOLD
)

STARBOARD_OTHER_THRESHOLD = (
    __cfg.get("other_threshold", __DEFAULT_OTHER_THRESHOLD)
    if __cfg
    else __DEFAULT_OTHER_THRESHOLD
)

STARBOARD_EXCLUDED_EMOJIS = (
    __cfg.get("excluded_emojis", __DEFAULT_EXCLUDED_EMOJIS)
    if __cfg
    else __DEFAULT_EXCLUDED_EMOJIS
)


class Starboard(MessageBoard):
    board_name = "starboard"
    leaderboard_key = "onphone"

    def __init__(
        self, bot: discord.Client, leaderboard: Optional[LeaderboardStore] = None
    ):
        super().__init__(
            bot,
            channel_id=STARBOARD_CHANNEL_ID,
            primary_emoji_str=STARBOARD_ONPHONE_EMOJI_STR,
            primary_threshold=STARBOARD_ONPHONE_THRESHOLD,
            leaderboard=leaderboard,
        )
        self.other_threshold = STARBOARD_OTHER_THRESHOLD
        self.excluded_emojis = STARBOARD_EXCLUDED_EMOJIS

    def _can_render_emoji(self, emoji: str) -> bool:
        emoji_id = discord.PartialEmoji.from_str(emoji).id
        if emoji_id is None:
            return True
        resolved = self.bot.get_emoji(emoji_id)
        return resolved is not None and resolved.is_usable()

    def _get_threshold(self, emoji: str) -> Optional[int]:
        if emoji in self.excluded_emojis:
            return None
        if emoji == self.primary_emoji_str:
            return self.primary_threshold
        if not self._can_render_emoji(emoji):
            return None
        return self.other_threshold


async def setup_starboard(bot, guilds, leaderboard=None):
    await bot.add_cog(Starboard(bot, leaderboard), guilds=guilds)
