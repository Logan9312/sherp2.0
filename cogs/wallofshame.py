from typing import Optional

import discord

from helper import get_config

from .helpers.board_store import BoardStore
from .message_board import MessageBoard

__DEFAULT_CHANNEL_ID = 1519463378903629824
__DEFAULT_BAN_EMOJI_STR = "<:ban:939740396286791741>"
__DEFAULT_BAN_THRESHOLD = 3

__cfg = get_config().get("wallofshame", None)
WALL_OF_SHAME_CHANNEL_ID = (
    __cfg.get("channel", __DEFAULT_CHANNEL_ID) if __cfg else __DEFAULT_CHANNEL_ID
)
WALL_OF_SHAME_BAN_EMOJI_STR = (
    __cfg.get("ban_emoji", __cfg.get("emoji", __DEFAULT_BAN_EMOJI_STR))
    if __cfg
    else __DEFAULT_BAN_EMOJI_STR
)
WALL_OF_SHAME_BAN_THRESHOLD = (
    __cfg.get("ban_threshold", __DEFAULT_BAN_THRESHOLD)
    if __cfg
    else __DEFAULT_BAN_THRESHOLD
)


class WallOfShame(MessageBoard):
    board_name = "wall-of-shame"
    storage_key = "ban"

    def __init__(self, bot: discord.Client, store: Optional[BoardStore] = None):
        super().__init__(
            bot,
            channel_id=WALL_OF_SHAME_CHANNEL_ID,
            primary_emoji_str=WALL_OF_SHAME_BAN_EMOJI_STR,
            primary_threshold=WALL_OF_SHAME_BAN_THRESHOLD,
            embed_color=discord.Color.dark_red(),
            store=store,
        )


async def setup_wall_of_shame(bot, guilds, store=None):
    await bot.add_cog(WallOfShame(bot, store), guilds=guilds)
