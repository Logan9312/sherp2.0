import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from typing import Dict, List, Tuple, Optional
import asyncio


class LeaderboardView(discord.ui.View):
    """View for paginated leaderboard display"""
    
    def __init__(self, entries: List[Tuple[str, int]], per_page: int = 10):
        super().__init__(timeout=60)
        self.entries = entries
        self.per_page = per_page
        self.current_page = 0
        self.max_page = (len(entries) - 1) // per_page
        
    def get_embed(self) -> discord.Embed:
        """Generate the embed for the current page"""
        start = self.current_page * self.per_page
        end = min(start + self.per_page, len(self.entries))
        
        embed = discord.Embed(
            title="📊 Message Leaderboard",
            description=f"Showing users {start + 1}-{end} of {len(self.entries)}",
            color=discord.Color.gold()
        )
        
        leaderboard_text = []
        for i, (user_id, count) in enumerate(self.entries[start:end], start=start + 1):
            # Get medal emoji for top 3
            medal = ""
            if i == 1:
                medal = "🥇 "
            elif i == 2:
                medal = "🥈 "
            elif i == 3:
                medal = "🥉 "
            
            leaderboard_text.append(f"{medal}**{i}.** <@{user_id}> - **{count:,}** messages")
        
        embed.add_field(
            name="Rankings",
            value="\n".join(leaderboard_text) if leaderboard_text else "No data available",
            inline=False
        )
        
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_page + 1}")
        return embed
    
    def update_buttons(self):
        """Update button states based on current page"""
        self.first_page.disabled = self.current_page == 0
        self.prev_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == self.max_page
        self.last_page.disabled = self.current_page == self.max_page
    
    @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    
    @discord.ui.button(label="<", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    
    @discord.ui.button(label=">", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(self.max_page, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    
    @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.max_page
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
    
    async def on_timeout(self):
        """Disable all buttons when the view times out"""
        for item in self.children:
            item.disabled = True


class Leaderboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.message_counts_file = "data/message_counts.json"
        self.message_counts: Dict[str, Dict[str, int]] = {}
        self.save_lock = asyncio.Lock()
        
    async def cog_load(self):
        """Load message counts from file when cog loads"""
        await super().cog_load()
        await self.load_message_counts()
        print("Leaderboard Cog loaded.")
        
    async def load_message_counts(self):
        """Load message counts from JSON file"""
        if os.path.exists(self.message_counts_file):
            try:
                with open(self.message_counts_file, 'r') as f:
                    self.message_counts = json.load(f)
            except:
                self.message_counts = {}
        else:
            self.message_counts = {}
    
    async def save_message_counts(self):
        """Save message counts to JSON file"""
        async with self.save_lock:
            with open(self.message_counts_file, 'w') as f:
                json.dump(self.message_counts, f, indent=2)
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Track message counts for users"""
        # Ignore bot messages and DMs
        if message.author.bot or not message.guild:
            return
        
        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        
        # Initialize guild data if not exists
        if guild_id not in self.message_counts:
            self.message_counts[guild_id] = {}
        
        # Increment message count
        if user_id not in self.message_counts[guild_id]:
            self.message_counts[guild_id][user_id] = 0
        
        self.message_counts[guild_id][user_id] += 1
        
        # Save every 10 messages to reduce I/O
        total_messages = sum(self.message_counts[guild_id].values())
        if total_messages % 10 == 0:
            await self.save_message_counts()
    
    # Helper method to get leaderboard data
    async def get_leaderboard_data(self, guild_id: str) -> Optional[List[Tuple[str, int]]]:
        """Get sorted leaderboard data for a guild"""
        if guild_id not in self.message_counts or not self.message_counts[guild_id]:
            return None
            
        return sorted(
            self.message_counts[guild_id].items(),
            key=lambda x: x[1],
            reverse=True
        )
    
    # Helper method to get user message count and rank
    async def get_user_stats(self, guild_id: str, user_id: str) -> Tuple[int, int]:
        """Get message count and rank for a user"""
        count = 0
        rank = 0
        
        if guild_id in self.message_counts and user_id in self.message_counts[guild_id]:
            count = self.message_counts[guild_id][user_id]
            
            # Calculate rank
            sorted_users = sorted(
                self.message_counts[guild_id].items(),
                key=lambda x: x[1],
                reverse=True
            )
            
            for i, (uid, _) in enumerate(sorted_users, 1):
                if uid == user_id:
                    rank = i
                    break
                    
        return count, rank
    
    # Prefix command for leaderboard
    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard_prefix(self, ctx: commands.Context):
        """Display the message count leaderboard for this server"""
        await self.show_leaderboard(ctx, ctx.guild.id)
    
    # Slash command for leaderboard
    @app_commands.command(
        name="leaderboard",
        description="Display the message count leaderboard for this server"
    )
    async def leaderboard_slash(self, interaction: discord.Interaction):
        """Slash command to display the message count leaderboard"""
        await self.show_leaderboard(interaction, interaction.guild_id)
    
    # Common method for both prefix and slash commands
    async def show_leaderboard(self, ctx_or_interaction, guild_id: int):
        """Display the leaderboard for a guild"""
        guild_id_str = str(guild_id)
        
        # Get message counts for this guild
        sorted_users = await self.get_leaderboard_data(guild_id_str)
        
        if not sorted_users:
            embed = discord.Embed(
                title="📊 Message Leaderboard",
                description="No message data available yet. Start chatting!",
                color=discord.Color.gold()
            )
            if isinstance(ctx_or_interaction, discord.Interaction):
                await ctx_or_interaction.response.send_message(embed=embed)
            else:
                await ctx_or_interaction.send(embed=embed)
            return
        
        # Create paginated view
        view = LeaderboardView(sorted_users)
        view.update_buttons()
        
        # Send initial embed with view
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message(embed=view.get_embed(), view=view)
        else:
            await ctx_or_interaction.send(embed=view.get_embed(), view=view)
    
    # Prefix command for message count
    @commands.command(name="messagecount", aliases=["mc", "mycount"])
    async def message_count_prefix(self, ctx: commands.Context, member: discord.Member = None):
        """Check message count for yourself or another member"""
        if member is None:
            member = ctx.author
        
        await self.show_message_count(ctx, ctx.guild.id, member)
    
    # Slash command for message count
    @app_commands.command(
        name="messagecount",
        description="Check message count for yourself or another member"
    )
    @app_commands.describe(
        member="The member to check message count for (defaults to yourself)"
    )
    async def message_count_slash(
        self, 
        interaction: discord.Interaction, 
        member: Optional[discord.Member] = None
    ):
        """Slash command to check message count"""
        if member is None:
            member = interaction.user
        
        await self.show_message_count(interaction, interaction.guild_id, member)
    
    # Common method for both prefix and slash commands
    async def show_message_count(self, ctx_or_interaction, guild_id: int, member: discord.Member):
        """Display message count for a member"""
        guild_id_str = str(guild_id)
        user_id_str = str(member.id)
        
        # Get message count and rank
        count, rank = await self.get_user_stats(guild_id_str, user_id_str)
        
        embed = discord.Embed(
            title="📈 Message Count",
            color=member.color
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Messages", value=f"**{count:,}**", inline=True)
        if rank > 0:
            embed.add_field(name="Rank", value=f"**#{rank}**", inline=True)
        
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message(embed=embed)
        else:
            await ctx_or_interaction.send(embed=embed)
    
    # Prefix command for reset leaderboard
    @commands.command(name="resetleaderboard", hidden=True)
    @commands.has_permissions(administrator=True)
    async def reset_leaderboard_prefix(self, ctx: commands.Context):
        """Reset the leaderboard for this server (Admin only)"""
        await self.reset_leaderboard(ctx, ctx.guild.id)
    
    # Slash command for reset leaderboard
    @app_commands.command(
        name="resetleaderboard",
        description="Reset the message leaderboard for this server (Admin only)"
    )
    @app_commands.default_permissions(administrator=True)
    async def reset_leaderboard_slash(self, interaction: discord.Interaction):
        """Slash command to reset the leaderboard"""
        await self.reset_leaderboard(interaction, interaction.guild_id)
    
    # Common method for both prefix and slash commands
    async def reset_leaderboard(self, ctx_or_interaction, guild_id: int):
        """Reset the leaderboard for a guild"""
        guild_id_str = str(guild_id)
        
        if guild_id_str in self.message_counts:
            self.message_counts[guild_id_str] = {}
            await self.save_message_counts()
            
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message("✅ Leaderboard has been reset for this server.")
        else:
            await ctx_or_interaction.send("✅ Leaderboard has been reset for this server.")


async def setup_leaderboard(bot, guilds):
    cog = Leaderboard(bot)
    await bot.add_cog(cog, guilds=guilds) 