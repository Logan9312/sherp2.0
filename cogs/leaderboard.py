import discord
from discord.ext import commands
from discord import app_commands
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
        
    async def cog_load(self):
        """Load when cog loads"""
        await super().cog_load()
        print("Leaderboard Cog loaded.")
    
    async def get_user_message_count(self, guild: discord.Guild, user: discord.Member) -> int:
        """Get total message count for a user in a guild"""
        count = 0
        # Search through all channels the user can see
        for channel in guild.text_channels:
            if channel.permissions_for(user).read_messages:
                try:
                    # Use search to get message count
                    async for message in channel.history(limit=None):
                        if message.author.id == user.id and not message.author.bot:
                            count += 1
                except discord.Forbidden:
                    continue  # Skip channels we don't have access to
                except discord.HTTPException:
                    continue  # Skip if we hit rate limits
        return count
    
    async def get_leaderboard_data(self, guild: discord.Guild, channel: discord.TextChannel) -> List[Tuple[str, int]]:
        """Get sorted leaderboard data for a guild by fetching message counts"""
        # Get all members in the guild
        members = [m for m in guild.members if not m.bot]
        
        # Create a loading embed
        loading_embed = discord.Embed(
            title="📊 Message Leaderboard",
            description="Fetching message counts for all users... This may take a while.",
            color=discord.Color.gold()
        )
        
        # Send loading message to the command channel
        loading_msg = await channel.send(embed=loading_embed)
        
        # Get message counts for all members
        message_counts = []
        for member in members:
            count = await self.get_user_message_count(guild, member)
            if count > 0:  # Only include users with messages
                message_counts.append((str(member.id), count))
        
        # Delete loading message
        try:
            await loading_msg.delete()
        except:
            pass
        
        # Sort by message count
        return sorted(message_counts, key=lambda x: x[1], reverse=True)
    
    # Prefix command for leaderboard
    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard_prefix(self, ctx: commands.Context):
        """Display the message count leaderboard for this server"""
        await self.show_leaderboard(ctx, ctx.guild, ctx.channel)
    
    # Slash command for leaderboard
    @app_commands.command(
        name="leaderboard",
        description="Display the message count leaderboard for this server"
    )
    async def leaderboard_slash(self, interaction: discord.Interaction):
        """Slash command to display the message count leaderboard"""
        await self.show_leaderboard(interaction, interaction.guild, interaction.channel)
    
    # Common method for both prefix and slash commands
    async def show_leaderboard(self, ctx_or_interaction, guild: discord.Guild, channel: discord.TextChannel):
        """Display the leaderboard for a guild"""
        # Get message counts for this guild
        sorted_users = await self.get_leaderboard_data(guild, channel)
        
        if not sorted_users:
            embed = discord.Embed(
                title="📊 Message Leaderboard",
                description="No message data available.",
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
        
        await self.show_message_count(ctx, ctx.guild, member)
    
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
        
        await self.show_message_count(interaction, interaction.guild, member)
    
    # Common method for both prefix and slash commands
    async def show_message_count(self, ctx_or_interaction, guild: discord.Guild, member: discord.Member):
        """Display message count for a member"""
        # Send loading message
        loading_embed = discord.Embed(
            title="📈 Message Count",
            description=f"Fetching message count for {member.display_name}...",
            color=member.color
        )
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message(embed=loading_embed)
        else:
            loading_msg = await ctx_or_interaction.send(embed=loading_embed)
        
        # Get message count
        count = await self.get_user_message_count(guild, member)
        
        # Get rank by fetching all counts
        all_counts = await self.get_leaderboard_data(guild, ctx_or_interaction.channel)
        rank = 0
        for i, (uid, _) in enumerate(all_counts, 1):
            if uid == str(member.id):
                rank = i
                break
        
        # Create final embed
        embed = discord.Embed(
            title="📈 Message Count",
            color=member.color
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Messages", value=f"**{count:,}**", inline=True)
        if rank > 0:
            embed.add_field(name="Rank", value=f"**#{rank}**", inline=True)
        
        # Update or send final message
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.edit_original_response(embed=embed)
        else:
            await loading_msg.edit(embed=embed)


async def setup_leaderboard(bot, guilds):
    cog = Leaderboard(bot)
    await bot.add_cog(cog, guilds=guilds) 