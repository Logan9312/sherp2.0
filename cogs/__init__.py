from .schedubuddy import setup_schedule_buddy
from .kattis import setup_kattis
from .misc import setup_misc_cog
from .snipe import setup_snipe
from .course_info import setup_course_info
from .starboard import setup_starboard
from .wallofshame import setup_wall_of_shame
from .faq import setup_faq
from .sherpmail import setup_SherpMailbox_cog
from .ship import setup_ship
from .leaderboard import open_leaderboard_store, setup_leaderboard


import asyncio
from aiohttp import ClientSession


async def setup_all_cogs(bot, guilds, client=None):
    if not client:
        client = ClientSession()
    leaderboard = open_leaderboard_store()
    results = await asyncio.gather(
        setup_schedule_buddy(bot, guilds, client),
        setup_kattis(bot, guilds),
        setup_misc_cog(bot, guilds),
        setup_snipe(bot, guilds, client),
        setup_course_info(bot, guilds),
        setup_starboard(bot, guilds, leaderboard),
        setup_wall_of_shame(bot, guilds, leaderboard),
        setup_leaderboard(bot, guilds, leaderboard),
        setup_faq(bot, guilds),
        setup_SherpMailbox_cog(bot, guilds),
        setup_ship(bot, guilds),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            print(f"Error loading cog: {result}")
