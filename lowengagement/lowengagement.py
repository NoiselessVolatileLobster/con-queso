import discord
import re
import datetime
import logging
from typing import Optional, List

from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.NoiselessVolatileLobster.LowEngagement")

class LowEngagement(commands.Cog):
    """
    Detects and penalizes low engagement users who spam emojis.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=981237498127, force_registration=True)

        default_guild = {
            "enabled": False,
            "emoji_limit": 3,           # How many messages in a row
            "time_window_days": 1,      # Time window to count those messages
            "level_threshold": 5,       # Level <= this gets checked
            "warn_text_1": "Low Engagement: Please use words, not just emojis.",
            "warn_link_1": "https://discord.com/guidelines",
            "warn_text_3": "Repeated Low Engagement: Continued emoji spam.",
            "flagged_users": []         # List of user IDs who have hit strike 1
        }

        self.config.register_guild(**default_guild)

        # Regex to match custom emojis <a:name:id> or <:name:id>
        self.custom_emoji_regex = re.compile(r'<a?:\w+:\d+>')

    async def get_user_level(self, member: discord.Member) -> int:
        """
        Integrates with VRT LevelUp to get a user's level.
        Returns 0 if cog not found or user has no data.
        """
        levelup = self.bot.get_cog("LevelUp")
        if not levelup:
            return 0
        
        # Based on VRT LevelUp API
        try:
            # Some versions use get_level, others might need profile access
            if hasattr(levelup, "get_level"):
                return await levelup.get_level(member)
            # Fallback for different versions/structures of LevelUp
            data = await levelup.db.users.find_one({"user_id": str(member.id), "guild_id": str(member.guild.id)})
            if data:
                return data.get("level", 0)
        except Exception as e:
            log.debug(f"Failed to fetch level for {member.id}: {e}")
        
        return 0

    async def issue_warning(self, member: discord.Member, level: int, reason: str) -> bool:
        """
        Integrates with Laggron's WarnSystem.
        Returns True if successful, False otherwise.
        """
        warnsystem = self.bot.get_cog("WarnSystem")
        if not warnsystem:
            log.warning("WarnSystem cog not loaded. Cannot issue warning.")
            return False

        guild = member.guild
        # We use the bot itself as the warner
        author = guild.me

        try:
            # Using the API as described in standard WarnSystem integrations
            # Attempting to locate the API wrapper usually found on the cog
            api = getattr(warnsystem, "api", None)
            
            if api:
                # Correct API Signature: warn(guild, member, author, level, reason)
                # We use keyword arguments to be safe and explicit, matching the API docs.
                await api.warn(
                    guild=guild,
                    member=member,
                    author=author,
                    level=level,
                    reason=reason
                )
            else:
                # Fallback: direct function call if API wrapper isn't structured typically
                # Note: API implementations vary, this targets the standard Laggron structure
                await warnsystem.warn(guild=guild, member=member, author=author, reason=reason, level=level)
            
            return True
                
        except Exception as e:
            err_msg = str(e)
            if "No modlog found" in err_msg:
                log.warning(f"WarnSystem failed to warn {member.id} (Guild: {guild.id}) because no modlog channel is configured in WarnSystem. "
                            f"Please ensure you have configured a log channel using `[p]warnset channel`.")
            else:
                log.error(f"Failed to issue WarnSystem warning to {member.id}: {e}")
            return False

    def is_emoji_only(self, content: str) -> bool:
        """
        Determines if a message is purely emojis (custom or unicode).
        """
        if not content:
            return False # Attachments only, etc.

        # 1. Remove custom emojis
        content = self.custom_emoji_regex.sub('', content)

        # 2. Remove whitespace
        content = content.strip()

        # 3. Simple heuristic for unicode emojis:
        # If the string is now empty, it was likely just custom emojis + whitespace.
        if len(content) == 0:
            return True

        # 4. Unicode Emoji Check
        # It is difficult to regex ALL unicode emojis perfectly without external libs.
        # We check if the remaining characters are "symbol-like" or common emoji ranges.
        # This is a basic filter. If it contains alphanumeric chars, it's not emoji only.
        if re.search(r'[a-zA-Z0-9]', content):
            return False
            
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # Check if enabled
        conf = await self.config.guild(message.guild).all()
        if not conf["enabled"]:
            return

        # 1. Check content of CURRENT message first to save resources
        if not self.is_emoji_only(message.content):
            return

        # 2. Level Check
        user_level = await self.get_user_level(message.author)
        if user_level > conf["level_threshold"]:
            return

        # 3. History Check (The expensive part)
        # We need to find the last X messages FROM THIS USER
        emoji_limit = conf["emoji_limit"]
        days_limit = conf["time_window_days"]
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_limit)

        user_messages = []
        
        # We fetch a buffer. If limit is 3, fetching 50 history is usually safe to find 3 user msgs.
        try:
            async for msg in message.channel.history(limit=50):
                if msg.author.id == message.author.id:
                    # Check time window
                    if msg.created_at < cutoff:
                        break # Too old, stop searching
                    
                    user_messages.append(msg)
                    
                    if len(user_messages) >= emoji_limit:
                        break
        except Exception:
            # Perm errors or other issues
            return

        # If we didn't find enough messages within the time window
        if len(user_messages) < emoji_limit:
            return

        # Verify ALL found messages are emoji only
        for msg in user_messages:
            if not self.is_emoji_only(msg.content):
                return

        # 4. Trigger Logic
        flagged_users = conf["flagged_users"]
        uid = message.author.id

        if uid in flagged_users:
            # Repeat Offender -> Level 3 Warning
            reason = conf["warn_text_3"]
            await self.issue_warning(message.author, 3, reason)
            # We do not remove them from flagged list; they remain flagged until admin reset
        else:
            # First Offense -> Level 1 Warning
            reason = f"{conf['warn_text_1']} (Read: {conf['warn_link_1']})"
            await self.issue_warning(message.author, 1, reason)
            
            # Add to flags
            async with self.config.guild(message.guild).flagged_users() as flags:
                if uid not in flags:
                    flags.append(uid)

    @commands.group(name="lowengagementset", aliases=["leset"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def lowengagementset(self, ctx):
        """Configuration settings for Low Engagement cog."""
        pass

    @lowengagementset.command(name="view")
    async def view_settings(self, ctx):
        """View the current configuration settings."""
        conf = await self.config.guild(ctx.guild).all()
        
        # Using a code block table as requested
        table_data = [
            f"{'Setting':<20} | {'Value'}",
            f"{'-'*21}|{'-'*30}",
            f"{'Enabled':<20} | {str(conf['enabled'])}",
            f"{'Emoji Limit':<20} | {conf['emoji_limit']} msgs in a row",
            f"{'Time Window':<20} | {conf['time_window_days']} days",
            f"{'Level Threshold':<20} | Level {conf['level_threshold']}",
            f"{'Flagged Users':<20} | {len(conf['flagged_users'])} users",
            f"{'Warn Text (Lvl 1)':<20} | {conf['warn_text_1'][:25]}...",
            f"{'Warn Text (Lvl 3)':<20} | {conf['warn_text_3'][:25]}...",
        ]
        
        table_str = "\n".join(table_data)
        await ctx.send(box(table_str, lang="prolog"))

    @lowengagementset.command(name="toggle")
    async def toggle_cog(self, ctx):
        """Toggle the cog on or off for this guild."""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        await ctx.send(f"LowEngagement is now **{'Enabled' if not current else 'Disabled'}**.")

    @lowengagementset.command(name="limit")
    async def set_limit(self, ctx, count: int):
        """Set the number of emoji-only messages in a row to trigger."""
        if count < 1:
            return await ctx.send("Limit must be at least 1.")
        await self.config.guild(ctx.guild).emoji_limit.set(count)
        await ctx.send(f"Emoji limit set to {count} messages.")

    @lowengagementset.command(name="days")
    async def set_days(self, ctx, days: int):
        """Set the time window in days for the message count."""
        if days < 1:
            return await ctx.send("Days must be at least 1.")
        await self.config.guild(ctx.guild).time_window_days.set(days)
        await ctx.send(f"Time window set to {days} days.")

    @lowengagementset.command(name="level")
    async def set_level(self, ctx, level: int):
        """Set the LevelUp threshold (Users above this level are ignored)."""
        await self.config.guild(ctx.guild).level_threshold.set(level)
        await ctx.send(f"Level threshold set to {level}. Users above this level are immune.")

    @lowengagementset.command(name="text1")
    async def set_text1(self, ctx, *, text: str):
        """Set the warning text for the first offense (Level 1)."""
        await self.config.guild(ctx.guild).warn_text_1.set(text)
        await ctx.send("Level 1 warning text updated.")

    @lowengagementset.command(name="link1")
    async def set_link1(self, ctx, link: str):
        """Set the link included in the first offense warning."""
        await self.config.guild(ctx.guild).warn_link_1.set(link)
        await ctx.send("Level 1 warning link updated.")

    @lowengagementset.command(name="text3")
    async def set_text3(self, ctx, *, text: str):
        """Set the warning text for repeated offenses (Level 3)."""
        await self.config.guild(ctx.guild).warn_text_3.set(text)
        await ctx.send("Level 3 warning text updated.")

    @lowengagementset.command(name="resetflags")
    async def reset_flags(self, ctx):
        """Clear the list of flagged users (Resets everyone to Strike 1 status)."""
        await self.config.guild(ctx.guild).flagged_users.set([])
        await ctx.send("All flagged users have been reset. Next offense will be treated as a first offense.")

    @lowengagementset.command(name="unflag")
    async def unflag_user(self, ctx, member: discord.Member):
        """Unflag a specific user."""
        async with self.config.guild(ctx.guild).flagged_users() as flags:
            if member.id in flags:
                flags.remove(member.id)
                await ctx.send(f"{member.display_name} has been unflagged.")
            else:
                await ctx.send(f"{member.display_name} was not flagged.")

    @lowengagementset.command(name="manual", aliases=["mark"])
    async def manual_flag(self, ctx, member: discord.Member):
        """Manually mark a user as Low Engagement (Issues Lvl 1 Warn + Flags)."""
        conf = await self.config.guild(ctx.guild).all()
        
        # 1. Issue Level 1 Warning
        reason = f"{conf['warn_text_1']} (Read: {conf['warn_link_1']})"
        success = await self.issue_warning(member, 1, reason)
        
        if success:
            # 2. Add to flags
            async with self.config.guild(ctx.guild).flagged_users() as flags:
                if member.id not in flags:
                    flags.append(member.id)
                    await ctx.send(f"{member.mention} has been manually marked as Low Engagement. Level 1 warning issued and user is now flagged.")
                else:
                    await ctx.send(f"{member.mention} was already flagged, but I issued another Level 1 warning as requested.")
        else:
            await ctx.send(f"❌ Failed to warn {member.mention}. WarnSystem could not issue the warning. Please check your **WarnSystem** modlog configuration (`[p]warnset channel`).")