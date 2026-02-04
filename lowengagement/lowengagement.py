import discord
import re
import datetime
import logging
from typing import Optional, Union, List

from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, humanize_timedelta

log = logging.getLogger("red.NoiselessVolatileLobster.lowengagement")

class LowEngagement(commands.Cog):
    """
    Detect and warn users with low engagement (emoji-only spam).
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987123654, force_registration=True)

        default_guild = {
            "enabled": False,
            "emoji_streak_limit": 5,
            "time_window_days": 1,
            "max_level_ignored": 10,  # Users above this level are safe
            "warn_msg_lvl1": "Please avoid sending multiple messages containing only emojis. Contribute to the conversation with text.",
            "warn_link": "https://discord.com/guidelines",
            "warn_msg_lvl3": "Repeated low engagement behavior (emoji spam) after a warning.",
            "ignored_channels": [],
            "ignored_roles": []
        }

        default_member = {
            "streak": 0,
            "last_emoji_ts": 0.0,
            "is_flagged": False
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        # Regex to match custom emojis <a:name:id> or <:name:id>
        self.custom_emoji_regex = re.compile(r'<a?:\w+:\d+>')

        # Broad regex for unicode emojis/symbols
        # This includes standard emoji ranges, dingbats, geometric shapes, etc.
        self.unicode_emoji_regex = re.compile(
            r'['
            r'\U0001f000-\U0001faff'  # Supplemental Symbols and Pictographs
            r'\U00002000-\U00002bff'  # General Punctuation/Symbols (inc. some math/arrows often used as emojis)
            r'\U00002600-\U000027ff'  # Misc Symbols / Dingbats
            r'\U0000fe00-\U0000fe0f'  # Variation Selectors
            r'\U0001f900-\U0001f9ff'  # Supplemental Symbols and Pictographs
            r']+', flags=re.UNICODE
        )

    def is_emoji_only(self, content: str) -> bool:
        """
        Determines if a string is composed 'only' of emojis (custom or unicode) and whitespace.
        """
        if not content:
            return False

        # 1. Remove Custom Emojis
        temp = self.custom_emoji_regex.sub('', content)

        # 2. Remove Unicode Emojis (Broad sweep)
        temp = self.unicode_emoji_regex.sub('', temp)

        # 3. Strip whitespace
        temp = temp.strip()

        # 4. If nothing is left, it was all emojis/whitespace
        return len(temp) == 0

    async def get_user_level(self, member: discord.Member) -> int:
        """Get LevelUp level for a member, defaulting to 0 if not found."""
        levelup = self.bot.get_cog("LevelUp")
        if not levelup:
            return 0
        try:
            # Check if get_level exists and is callable
            if hasattr(levelup, "get_level"):
                # Potential return types depending on version: int or object with .level
                lvl = levelup.get_level(member)
                if isinstance(lvl, int):
                    return lvl
                # Handle object return if needed (hypothetical, based on common patterns)
                return getattr(lvl, "level", 0)
        except Exception as e:
            log.debug(f"Failed to get level for {member.id}: {e}")
        return 0

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if not await self.config.guild(message.guild).enabled():
            return

        # Hibernate Integration
        hibernate = self.bot.get_cog("Hibernate")
        if hibernate:
            try:
                is_hibernating = await hibernate.is_hibernating(message.author)
                if is_hibernating:
                    return
            except Exception:
                pass # Hibernate method might differ or fail, safe ignore

        # Ignore if user has ignored role
        ignored_roles = await self.config.guild(message.guild).ignored_roles()
        if any(r.id in ignored_roles for r in message.author.roles):
            return

        # Ignore if channel is ignored
        ignored_channels = await self.config.guild(message.guild).ignored_channels()
        if message.channel.id in ignored_channels:
            return

        is_emoji = self.is_emoji_only(message.content)
        member_conf = self.config.member(message.author)
        guild_conf = self.config.guild(message.guild)

        if not is_emoji:
            # Reset streak on valid engagement
            await member_conf.streak.set(0)
            return

        # It IS an emoji-only message
        # Check Level
        user_level = await self.get_user_level(message.author)
        max_level = await guild_conf.max_level_ignored()

        # If user is a high level veteran, ignore them (unless max_level is -1 for everyone)
        if user_level > max_level and max_level != -1:
            return

        # Check time window
        last_ts = await member_conf.last_emoji_ts()
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        
        window_days = await guild_conf.time_window_days()
        window_seconds = window_days * 86400

        current_streak = await member_conf.streak()

        if (now_ts - last_ts) > window_seconds:
            # Time window expired, reset streak to 1
            current_streak = 1
        else:
            current_streak += 1

        # Update data
        await member_conf.last_emoji_ts.set(now_ts)
        await member_conf.streak.set(current_streak)

        streak_limit = await guild_conf.emoji_streak_limit()

        if current_streak >= streak_limit:
            # Reset streak so we don't spam warn on every single subsequent emoji
            await member_conf.streak.set(0)
            await self.trigger_warning(message.guild, message.author)

    async def trigger_warning(self, guild: discord.Guild, member: discord.Member, manual: bool = False):
        """
        Executes the warning logic using WarnSystem.
        """
        warnsystem = self.bot.get_cog("WarnSystem")
        if not warnsystem:
            log.warning("WarnSystem cog not loaded. Cannot warn user.")
            return

        # Get API
        api = getattr(warnsystem, "api", None)
        if not api:
            log.warning("WarnSystem API not found.")
            return

        member_conf = self.config.member(member)
        is_flagged = await member_conf.is_flagged()
        guild_conf = self.config.guild(guild)

        if not is_flagged and not manual:
            # Level 1 Warn
            reason_text = await guild_conf.warn_msg_lvl1()
            link = await guild_conf.warn_msg_lvl1()
            link_text = await guild_conf.warn_link()
            
            full_reason = f"{reason_text}\nRead more: {link_text}"
            
            try:
                # Warning Level 1
                await api.warn(
                    guild=guild,
                    members=[member],
                    author=guild.me, # Bot is the author
                    level=1,
                    reason=full_reason
                )
                await member_conf.is_flagged.set(True)
                log.info(f"LowEngagement: Issued Level 1 warn to {member.id} in {guild.name}")
            except Exception as e:
                log.error(f"Failed to issue Level 1 warn: {e}")

        else:
            # Already flagged OR Manual trigger -> Treat as repeat offense -> Level 3 Warn
            # Note: Manual trigger puts them here immediately? 
            # The prompt says: "Manual... mark a user as low engagement... They will receive a Level 1 WarnSystem warning, and be flagged... so next time... Level 3"
            
            if manual:
                # Special logic for manual: Give Level 1, set Flag.
                reason_text = await guild_conf.warn_msg_lvl1()
                link_text = await guild_conf.warn_link()
                full_reason = f"[Manual Mark] {reason_text}\nRead more: {link_text}"

                try:
                    await api.warn(
                        guild=guild,
                        members=[member],
                        author=guild.me,
                        level=1,
                        reason=full_reason
                    )
                    await member_conf.is_flagged.set(True)
                    log.info(f"LowEngagement: Manually marked {member.id} in {guild.name}")
                except Exception as e:
                    log.error(f"Failed to issue Manual Level 1 warn: {e}")

            else:
                # Is Flagged (Repeat offense)
                reason_text = await guild_conf.warn_msg_lvl3()
                
                try:
                    # Warning Level 3 (Kick)
                    await api.warn(
                        guild=guild,
                        members=[member],
                        author=guild.me,
                        level=3,
                        reason=reason_text
                    )
                    # We do not reset the flag. They remain low engagement until manually cleared or logic changes.
                    log.info(f"LowEngagement: Issued Level 3 warn to {member.id} in {guild.name}")
                except Exception as e:
                    log.error(f"Failed to issue Level 3 warn: {e}")


    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def lowengagementset(self, ctx):
        """Configuration for Low Engagement detection."""
        pass

    @lowengagementset.command(name="enable")
    async def set_enable(self, ctx, toggle: bool):
        """Enable or disable the cog for this server."""
        await self.config.guild(ctx.guild).enabled.set(toggle)
        await ctx.send(f"LowEngagement enabled: {toggle}")

    @lowengagementset.command(name="limit")
    async def set_limit(self, ctx, count: int):
        """Set the number of consecutive emoji messages required to trigger."""
        if count < 1:
            return await ctx.send("Limit must be at least 1.")
        await self.config.guild(ctx.guild).emoji_streak_limit.set(count)
        await ctx.send(f"Streak limit set to {count}.")

    @lowengagementset.command(name="days")
    async def set_days(self, ctx, days: int):
        """Set the time window in days for the streak to be valid."""
        if days < 1:
            return await ctx.send("Days must be at least 1.")
        await self.config.guild(ctx.guild).time_window_days.set(days)
        await ctx.send(f"Time window set to {days} days.")

    @lowengagementset.command(name="level")
    async def set_level(self, ctx, level: int):
        """Set the max LevelUp level. Users above this are ignored. Set -1 to track everyone."""
        await self.config.guild(ctx.guild).max_level_ignored.set(level)
        await ctx.send(f"Max level set to {level}.")

    @lowengagementset.command(name="reason1")
    async def set_reason1(self, ctx, *, text: str):
        """Set the warning text for the first offense (Level 1)."""
        await self.config.guild(ctx.guild).warn_msg_lvl1.set(text)
        await ctx.send("Level 1 warning text updated.")

    @lowengagementset.command(name="link")
    async def set_link(self, ctx, link: str):
        """Set the link appended to the Level 1 warning."""
        await self.config.guild(ctx.guild).warn_link.set(link)
        await ctx.send("Warning link updated.")

    @lowengagementset.command(name="reason3")
    async def set_reason3(self, ctx, *, text: str):
        """Set the warning text for the second offense (Level 3)."""
        await self.config.guild(ctx.guild).warn_msg_lvl3.set(text)
        await ctx.send("Level 3 warning text updated.")

    @lowengagementset.command(name="ignorechannel")
    async def ignore_channel(self, ctx, channel: discord.TextChannel):
        """Toggle ignoring a specific channel."""
        async with self.config.guild(ctx.guild).ignored_channels() as ignored:
            if channel.id in ignored:
                ignored.remove(channel.id)
                await ctx.send(f"{channel.mention} is no longer ignored.")
            else:
                ignored.append(channel.id)
                await ctx.send(f"{channel.mention} is now ignored.")

    @lowengagementset.command(name="ignorerole")
    async def ignore_role(self, ctx, role: discord.Role):
        """Toggle ignoring a specific role."""
        async with self.config.guild(ctx.guild).ignored_roles() as ignored:
            if role.id in ignored:
                ignored.remove(role.id)
                await ctx.send(f"`{role.name}` is no longer ignored.")
            else:
                ignored.append(role.id)
                await ctx.send(f"`{role.name}` is now ignored.")

    @lowengagementset.command(name="view")
    async def view_settings(self, ctx):
        """View current LowEngagement settings."""
        settings = await self.config.guild(ctx.guild).all()
        
        warn_system_status = "Loaded" if self.bot.get_cog("WarnSystem") else "Not Loaded (Required)"
        level_up_status = "Loaded" if self.bot.get_cog("LevelUp") else "Not Loaded"
        hibernate_status = "Loaded" if self.bot.get_cog("Hibernate") else "Not Loaded"

        msg = (
            f"**Enabled**: {settings['enabled']}\n"
            f"**Streak Limit**: {settings['emoji_streak_limit']} messages\n"
            f"**Time Window**: {settings['time_window_days']} days\n"
            f"**Max Level Ignored**: {settings['max_level_ignored']}\n"
            f"**Ignored Channels**: {len(settings['ignored_channels'])}\n"
            f"**Ignored Roles**: {len(settings['ignored_roles'])}\n\n"
            f"**Lvl 1 Reason**: {settings['warn_msg_lvl1']}\n"
            f"**Link**: {settings['warn_link']}\n"
            f"**Lvl 3 Reason**: {settings['warn_msg_lvl3']}\n\n"
            f"__Integrations__\n"
            f"WarnSystem: {warn_system_status}\n"
            f"LevelUp: {level_up_status}\n"
            f"Hibernate: {hibernate_status}"
        )

        embed = discord.Embed(title="Low Engagement Settings", description=msg, color=await ctx.embed_color())
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def marklowengagement(self, ctx, member: discord.Member):
        """
        Manually mark a user as Low Engagement.
        
        This will issue a Level 1 warning immediately and flag them for Level 3 on next offense.
        """
        await ctx.send(f"Processing manual low engagement mark for {member.mention}...")
        
        # We pass manual=True to trigger the specific logic requested:
        # "They will receive a Level 1 WarnSystem warning, and be flagged... so next time... Level 3"
        await self.trigger_warning(ctx.guild, member, manual=True)
        
        await ctx.tick()