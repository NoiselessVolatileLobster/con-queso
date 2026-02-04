import discord
from discord.ext import commands
from redbot.core import checks, Config, commands
from redbot.core.utils.chat_formatting import box, humanize_list
import random
import asyncio
import logging
import time
from collections import defaultdict

log = logging.getLogger("red.NoiselessVolatileLobster.sortinghat")

class SortingHat(commands.Cog):
    """
    Sorts users into houses when they reach a specific level.
    Requires Vrt's LevelUp cog.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "enabled": False,
            "greeting_channel": None,
            "house_roles": [],  # List of Role IDs
            "sort_level": 2,
            "greeting_message": "Welcome to {house}, {member}! You have been sorted!"
        }
        
        self.config.register_guild(**default_guild)
        
        # Queue system: {guild_id: [member_id, ...]}
        self.guild_queues = defaultdict(list)
        # Active tasks: {guild_id: Task}
        self.active_tasks = {}
        # Last sort time: {guild_id: timestamp}
        self.last_sort_times = defaultdict(float)

    # Helper: Get Level
    async def get_member_level(self, member: discord.Member) -> int:
        levelup = self.bot.get_cog("LevelUp")
        if not levelup:
            return 0
        
        try:
            # Check if the method is async or sync to support different versions
            potential_level = levelup.get_level(member)
            if asyncio.iscoroutine(potential_level):
                return await potential_level
            return potential_level
        except AttributeError:
            # Fallback if API changes
            return 0
        except Exception as e:
            log.error(f"Error getting level for {member}: {e}")
            return 0

    # Helper: Check if user has a house
    async def get_assigned_house(self, guild: discord.Guild, member: discord.Member) -> discord.Role:
        house_ids = await self.config.guild(guild).house_roles()
        for role in member.roles:
            if role.id in house_ids:
                return role
        return None

    # Helper: Add to Queue
    def enqueue_member(self, guild: discord.Guild, member: discord.Member):
        if member.id not in self.guild_queues[guild.id]:
            self.guild_queues[guild.id].append(member.id)
            self._ensure_processor_running(guild)

    def _ensure_processor_running(self, guild: discord.Guild):
        if guild.id not in self.active_tasks or self.active_tasks[guild.id].done():
            self.active_tasks[guild.id] = self.bot.loop.create_task(self._process_queue(guild))

    async def _process_queue(self, guild: discord.Guild):
        log.info(f"Starting sort queue processor for guild {guild.name} ({guild.id})")
        
        while self.guild_queues[guild.id]:
            # Rate limit check
            last_time = self.last_sort_times[guild.id]
            now = time.time()
            # 1 hour = 3600 seconds
            elapsed = now - last_time
            if elapsed < 3600:
                wait_time = 3600 - elapsed
                log.info(f"SortingHat: Waiting {wait_time:.1f}s before next sort in {guild.name}")
                await asyncio.sleep(wait_time)

            # Get next member
            if not self.guild_queues[guild.id]:
                break
                
            member_id = self.guild_queues[guild.id].pop(0)
            member = guild.get_member(member_id)

            if member:
                # Double check eligibility (in case they got sorted while waiting)
                existing_house = await self.get_assigned_house(guild, member)
                if not existing_house:
                    await self.sort_member(guild, member)
                    # Update time ONLY if we actually tried to sort
                    self.last_sort_times[guild.id] = time.time()
                else:
                    log.info(f"Skipping {member} (already has house)")
            
            # If there are more items, we loop back. 
            # The rate limit check at the top handles the sleep.

        log.info(f"Finished sort queue for guild {guild.name}")
        if guild.id in self.active_tasks:
            del self.active_tasks[guild.id]

    # Helper: Sort Logic
    async def sort_member(self, guild: discord.Guild, member: discord.Member):
        house_ids = await self.config.guild(guild).house_roles()
        
        if not house_ids:
            return None

        # verify roles exist
        valid_roles = []
        clean_config = False
        for rid in house_ids:
            role = guild.get_role(rid)
            if role:
                valid_roles.append(role)
            else:
                clean_config = True
        
        if clean_config:
            await self.config.guild(guild).house_roles.set([r.id for r in valid_roles])

        if not valid_roles:
            return None

        # Pick random house
        chosen_house = random.choice(valid_roles)
        
        try:
            await member.add_roles(chosen_house, reason="SortingHat: Level reached")
        except discord.Forbidden:
            log.warning(f"Failed to sort {member} in {guild}: Missing Permissions")
            return None
        except discord.HTTPException:
            return None

        # Send greeting
        greet_channel_id = await self.config.guild(guild).greeting_channel()
        if greet_channel_id:
            channel = guild.get_channel(greet_channel_id)
            if channel and channel.permissions_for(guild.me).send_messages:
                msg_template = await self.config.guild(guild).greeting_message()
                
                # Replace placeholders
                message = msg_template.replace("{house}", chosen_house.mention)
                message = message.replace("{member}", member.mention)
                message = message.replace("{mention}", member.mention) # Added alias
                
                try:
                    await channel.send(message)
                except discord.HTTPException:
                    pass
        
        return chosen_house

    @commands.Cog.listener()
    async def on_member_levelup(self, guild: discord.Guild, member: discord.Member, message, channel, new_level: int):
        """
        Listener for LevelUp cog.
        """
        if member.bot:
            return

        if not await self.config.guild(guild).enabled():
            return

        target_level = await self.config.guild(guild).sort_level()

        # We trigger if they just hit the specific level
        if new_level == target_level:
            # Check if they already have a house
            existing_house = await self.get_assigned_house(guild, member)
            if not existing_house:
                # Add to queue instead of sorting immediately
                self.enqueue_member(guild, member)

    @commands.group(name="sortinghatset", aliases=["shset"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def sortinghatset(self, ctx):
        """Configuration settings for SortingHat."""
        pass

    @sortinghatset.command(name="toggle")
    async def sh_toggle(self, ctx):
        """Enable or disable the SortingHat system."""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        state = "enabled" if not current else "disabled"
        await ctx.send(f"SortingHat is now **{state}**.")

    @sortinghatset.command(name="addhouse")
    async def sh_addhouse(self, ctx, role: discord.Role):
        """Add a role to be used as a House."""
        async with self.config.guild(ctx.guild).house_roles() as houses:
            if role.id in houses:
                return await ctx.send(f"{role.name} is already a house.")
            houses.append(role.id)
        await ctx.send(f"Added {role.name} to the list of houses.")

    @sortinghatset.command(name="delhouse")
    async def sh_delhouse(self, ctx, role: discord.Role):
        """Remove a role from the house list."""
        async with self.config.guild(ctx.guild).house_roles() as houses:
            if role.id not in houses:
                return await ctx.send("That role is not a configured house.")
            houses.remove(role.id)
        await ctx.send(f"Removed {role.name} from the list of houses.")

    @sortinghatset.command(name="channel")
    async def sh_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for greeting sorted users. Leave empty to disable."""
        if channel:
            await self.config.guild(ctx.guild).greeting_channel.set(channel.id)
            await ctx.send(f"Greetings will now be sent in {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).greeting_channel.set(None)
            await ctx.send("Greetings disabled.")

    @sortinghatset.command(name="level")
    async def sh_level(self, ctx, level: int):
        """Set the level at which users get sorted (Default: 2)."""
        if level < 1:
            return await ctx.send("Level must be 1 or higher.")
        await self.config.guild(ctx.guild).sort_level.set(level)
        await ctx.send(f"Users will now be sorted when they reach level {level}.")

    @sortinghatset.command(name="message")
    async def sh_message(self, ctx, *, message: str):
        """
        Set the greeting message.
        
        Available Variables:
        {member}  - Mentions the user (e.g. @User)
        {mention} - Alias for {member}
        {house}   - Mentions the house role (e.g. @Gryffindor)
        
        Example:
        [p]shset message {member} has been sorted into {house}!
        """
        await self.config.guild(ctx.guild).greeting_message.set(message)
        await ctx.send("Greeting message updated.")

    @sortinghatset.command(name="sortunsorted")
    async def sh_sortunsorted(self, ctx):
        """
        Queue all users who meet the level requirement but have no house to be sorted.
        (Processes 1 user per hour to prevent spam).
        """
        if not self.bot.get_cog("LevelUp"):
            return await ctx.send("The 'LevelUp' cog is not loaded. I cannot determine user levels.")

        target_level = await self.config.guild(ctx.guild).sort_level()
        house_ids = await self.config.guild(ctx.guild).house_roles()
        
        if not house_ids:
            return await ctx.send("No houses configured! Use `[p]shset addhouse` first.")

        msg = await ctx.send("Scanning members... this might take a moment.")
        
        added_count = 0
        skipped_low_level = 0
        skipped_already_sorted = 0

        async with ctx.typing():
            for member in ctx.guild.members:
                if member.bot:
                    continue

                # 1. Check existing house
                has_house = False
                for role in member.roles:
                    if role.id in house_ids:
                        has_house = True
                        break
                
                if has_house:
                    skipped_already_sorted += 1
                    continue

                # 2. Check Level
                lvl = await self.get_member_level(member)
                if lvl < target_level:
                    skipped_low_level += 1
                    continue

                # 3. Add to Queue
                if member.id not in self.guild_queues[ctx.guild.id]:
                    self.enqueue_member(ctx.guild, member)
                    added_count += 1
        
        current_queue_size = len(self.guild_queues[ctx.guild.id])
        
        summary = (
            f"**Scan Complete**\n"
            f"Added to Queue: {added_count}\n"
            f"Current Queue Size: {current_queue_size}\n"
            f"Skipped (Already sorted): {skipped_already_sorted}\n"
            f"Skipped (Level < {target_level}): {skipped_low_level}\n\n"
            f"**Note:** Users are processed 1 per hour to prevent channel spam."
        )
        await msg.edit(content=summary)

    @sortinghatset.command(name="view")
    async def sh_view(self, ctx):
        """View current settings."""
        conf = await self.config.guild(ctx.guild).all()
        
        houses = []
        for rid in conf['house_roles']:
            role = ctx.guild.get_role(rid)
            if role:
                houses.append(role.mention)
            else:
                houses.append(f"Deleted Role ({rid})")
        
        houses_str = "\n".join(houses) if houses else "None configured"
        channel = ctx.guild.get_channel(conf['greeting_channel'])
        channel_str = channel.mention if channel else "None"
        
        queue_len = len(self.guild_queues[ctx.guild.id])
        
        desc = (
            f"**Enabled:** {conf['enabled']}\n"
            f"**Sort Level:** {conf['sort_level']}\n"
            f"**Greeting Channel:** {channel_str}\n"
            f"**Houses:**\n{houses_str}\n\n"
            f"**Queue Size:** {queue_len} users waiting."
        )
        
        embed = discord.Embed(title="SortingHat Settings", description=desc, color=discord.Color.purple())
        await ctx.send(embed=embed)