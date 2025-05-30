import discord
from discord.ext import commands
from discord import app_commands
from typing import Dict, List, Tuple, Optional
import asyncio
from datetime import datetime, timedelta, timezone


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
    
    async def get_user_message_count(self, guild: discord.Guild, user: discord.Member, progress_callback=None) -> int:
        """Get total message count for a user in a guild using filtered history"""
        count = 0
        # Get all channels that can contain messages
        channels = [
            channel for channel in guild.channels 
            if isinstance(channel, (discord.TextChannel, discord.Thread))
        ]
        
        # Get forum channels separately to handle their threads
        forum_channels = [
            channel for channel in guild.channels 
            if isinstance(channel, discord.ForumChannel)
        ]
        
        # Count accessible channels first (only check bot permissions)
        accessible_channels = []
        for channel in channels:
            if channel.permissions_for(guild.me).read_messages:
                accessible_channels.append(channel)
        
        accessible_forums = []
        for forum in forum_channels:
            if forum.permissions_for(guild.me).read_messages:
                accessible_forums.append(forum)
        
        total_accessible = len(accessible_channels) + len(accessible_forums)
        channels_processed = 0
        
        # Debug info
        print(f"\nCounting messages for {user.name} in {guild.name}")
        print(f"Total channels to check: {len(channels)} text channels/threads, {len(forum_channels)} forums")
        print(f"Accessible channels: {len(accessible_channels)} text channels/threads, {len(accessible_forums)} forums")
        
        # Search through all accessible channels
        for channel in accessible_channels:
            try:
                channel_count = 0
                # Use history and filter messages manually
                async for message in channel.history(
                    limit=None
                    # Removed time limit to count all messages
                ):
                    if message.author.id == user.id and not message.author.bot:
                        channel_count += 1
                
                if channel_count > 0:
                    print(f"Channel {channel.name}: {channel_count} messages")
                    count += channel_count
                    
            except discord.Forbidden:
                print(f"No access to channel: {channel.name}")
                continue  # Skip channels we don't have access to
            except discord.HTTPException as e:
                print(f"HTTP error in {channel.name}: {str(e)}")
                continue  # Skip if we hit rate limits
            
            # Update channel progress
            channels_processed += 1
            if progress_callback and total_accessible > 0:
                await progress_callback(channels_processed, total_accessible)
        
        # Handle accessible forum channels
        for forum in accessible_forums:
            try:
                # Get all threads (both active and archived)
                all_threads = []
                
                # Get active threads
                for thread in forum.threads:
                    all_threads.append(thread)
                
                # Get archived threads
                async for thread in forum.archived_threads(limit=None):
                    all_threads.append(thread)
                
                # Process all threads
                for thread in all_threads:
                    try:
                        thread_count = 0
                        async for message in thread.history(
                            limit=None
                            # Removed time limit to count all messages
                        ):
                            if message.author.id == user.id and not message.author.bot:
                                thread_count += 1
                        
                        if thread_count > 0:
                            print(f"Thread {thread.name} in {forum.name}: {thread_count} messages")
                            count += thread_count
                            
                    except discord.Forbidden:
                        print(f"No access to thread: {thread.name}")
                        continue
                    except discord.HTTPException as e:
                        print(f"HTTP error in thread {thread.name}: {str(e)}")
                        continue
                            
            except discord.Forbidden:
                print(f"No access to forum: {forum.name}")
                continue
            except discord.HTTPException as e:
                print(f"HTTP error in forum {forum.name}: {str(e)}")
                continue
            
            # Update channel progress for forum
            channels_processed += 1
            if progress_callback and total_accessible > 0:
                await progress_callback(channels_processed, total_accessible)
        
        print(f"Total count for {user.name}: {count} messages\n")
        return count
    
    async def get_leaderboard_data(self, guild: discord.Guild, channel: discord.TextChannel) -> List[Tuple[str, int]]:
        """Get sorted leaderboard data for a guild by fetching message counts"""
        # Get all members in the guild
        members = [m for m in guild.members if not m.bot]
        total_users = len(members)
        
        # Create a loading embed
        loading_embed = discord.Embed(
            title="📊 Message Leaderboard",
            description="Fetching message counts for all users... This may take a while.",
            color=discord.Color.gold()
        )
        loading_embed.add_field(name="Users Progress", value="0%", inline=True)
        loading_embed.add_field(name="Current User", value="Starting...", inline=True)
        loading_embed.add_field(name="Channel Progress", value="0%", inline=True)
        
        # Send loading message to the command channel
        loading_msg = await channel.send(embed=loading_embed)
        
        # Track progress
        last_user_percent = 0
        last_channel_percent = 0
        current_channel_progress = (0, 0)
        
        async def update_channel_progress(channels_done, total_channels):
            nonlocal last_channel_percent, current_channel_progress
            current_channel_progress = (channels_done, total_channels)
            
            if total_channels > 0:
                channel_percent = int((channels_done / total_channels) * 100)
                # Update only on 10% increments
                if channel_percent >= last_channel_percent + 10:
                    last_channel_percent = (channel_percent // 10) * 10
                    await update_embed()
        
        async def update_embed():
            loading_embed.set_field_at(0, name="Users Progress", 
                                     value=f"{last_user_percent}% ({users_processed}/{total_users})", 
                                     inline=True)
            loading_embed.set_field_at(1, name="Current User", 
                                     value=current_user_name, 
                                     inline=True)
            if current_channel_progress[1] > 0:
                channel_pct = int((current_channel_progress[0] / current_channel_progress[1]) * 100)
                loading_embed.set_field_at(2, name="Channel Progress", 
                                         value=f"{channel_pct}% ({current_channel_progress[0]}/{current_channel_progress[1]})", 
                                         inline=True)
            try:
                await loading_msg.edit(embed=loading_embed)
            except:
                pass
        
        # Get message counts for all members
        message_counts = []
        users_processed = 0
        current_user_name = "Starting..."
        
        for i, member in enumerate(members):
            current_user_name = member.display_name
            users_processed = i + 1
            
            # Update user progress every 10%
            if total_users > 0:
                user_percent = int((users_processed / total_users) * 100)
                if user_percent >= last_user_percent + 10:
                    last_user_percent = (user_percent // 10) * 10
                    await update_embed()
            
            # Reset channel progress for new user
            current_channel_progress = (0, 0)
            
            count = await self.get_user_message_count(guild, member, update_channel_progress)
            if count > 0:  # Only include users with messages
                message_counts.append((str(member.id), count))
        
        # Update to show we're finalizing
        try:
            loading_embed.clear_fields()
            loading_embed.description = "All users processed! Preparing final leaderboard..."
            loading_embed.add_field(name="Status", value="✅ Sorting results...", inline=False)
            loading_embed.add_field(name="Users Processed", value=f"{total_users} users", inline=True)
            loading_embed.add_field(name="Users with Messages", value=f"{len(message_counts)} users", inline=True)
            await loading_msg.edit(embed=loading_embed)
        except:
            pass
        
        # Sort by message count
        sorted_results = sorted(message_counts, key=lambda x: x[1], reverse=True)
        
        # Delete loading message
        try:
            await loading_msg.delete()
        except:
            pass
        
        return sorted_results
    
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
        count = await self.get_user_message_count(guild, member, None)
        
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