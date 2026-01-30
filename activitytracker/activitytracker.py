import discord
import logging
import re
import time
import asyncio
from datetime import datetime, timedelta
from typing import Literal, Optional, Union, Dict, List

from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, paginator, humanize_list

log = logging.getLogger("red.activitytracker")

class ActivityTracker(commands.Cog):
    """
    Track user message and voice activity with automated policing.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8273918273, force_registration=True)

        default_guild = {
            "msg_threshold": 12,
            "msg_window_hours": 72,
            "min_chars": 1,
            "ignore_commands": True,
            "ignored_channels": [],
            "voice_min_minutes": 20,
            "voice_min_users": 2,
            "inactivity_days": 30,
            "preview_mode": True,
            "policing_rules": [], # List of dicts: {"level": int, "days": int, "action": str}
            "report_channel": None,
            "has_run_normal": False
        }

        default_member = {
            "last_active": None, # Timestamp
            "message_history": [], # List of timestamps
            "voice_start": None, # Temp storage for VC session
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        
        self.policing_task = self.bot.loop.create_task(self.initialize_loop())

    async def initialize_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.run_policing()
            except Exception as e:
                log.error(f"Error in policing loop: {e}")
            await asyncio.sleep(3600) # Run every hour

    async def run_policing(self, manual_report_ctx=None):
        """
        Main logic for checking inactivity and performing actions.
        If manual_report_ctx is provided, it's the first run after preview mode.
        """
        for guild in self.bot.guilds:
            conf = await self.config.guild(guild).all()
            if conf["preview_mode"] and not manual_report_ctx:
                continue

            report_entries = []
            rules = sorted(conf["policing_rules"], key=lambda x: x['days'], reverse=True)
            
            for member in guild.members:
                if member.bot:
                    continue
                
                # Check Hibernate
                hibernate_cog = self.bot.get_cog("Hibernate")
                if hibernate_cog:
                    try:
                        if await hibernate_cog.is_hibernating(member):
                            continue
                    except:
                        pass

                mem_data = await self.config.member(member).all()
                last_active = mem_data["last_active"]
                
                if not last_active:
                    # If never active, use join date as baseline
                    last_active = member.joined_at.timestamp()

                days_inactive = (datetime.utcnow().timestamp() - last_active) / 86400

                # Find applicable rule
                applicable_rule = None
                user_level = await self._get_level(member)
                
                for rule in rules:
                    if user_level >= rule["level"] and days_inactive >= rule["days"]:
                        applicable_rule = rule
                        break
                
                if applicable_rule:
                    if manual_report_ctx:
                        report_entries.append(f"{member.display_name} (Level {user_level}): {applicable_rule['action']} due to {int(days_inactive)} days inactivity.")
                    elif not conf["preview_mode"]:
                        await self._execute_policing_action(member, applicable_rule)

            if manual_report_ctx and report_entries:
                msg = "**ActivityTracker: Normal Mode Activated**\nThe following users are targets for policing actions:\n"
                msg += "\n".join(report_entries)
                for page in paginator(msg, prefix="", suffix=""):
                    await manual_report_ctx.send(page)

    async def _get_level(self, member: discord.Member) -> int:
        lvl_cog = self.bot.get_cog("LevelUp")
        if lvl_cog:
            try:
                # LevelUp typically stores levels in its own config
                data = await lvl_cog.config.member(member).all()
                return data.get("level", 0)
            except:
                return 0
        return 0

    async def _execute_policing_action(self, member: discord.Member, rule: dict):
        action = rule["action"].lower()
        reason = f"ActivityTracker: Inactive for {rule['days']}+ days at Level {rule['level']}."
        
        if action == "kick":
            try:
                await member.kick(reason=reason)
            except discord.Forbidden:
                log.warning(f"Failed to kick {member} in {member.guild.name}: Missing Permissions")
        
        elif action == "warn":
            ws_cog = self.bot.get_cog("WarnSystem")
            if ws_cog:
                try:
                    # Based on WarnSystem API docs
                    await ws_cog.api.warn_user(
                        member=member,
                        warner=member.guild.me,
                        reason=reason,
                        points=1 # Default 1 point
                    )
                except Exception as e:
                    log.error(f"WarnSystem error: {e}")
        
        elif action == "mention":
            conf = await self.config.guild(member.guild).all()
            channel = member.guild.get_channel(conf["report_channel"])
            if channel:
                await channel.send(f"{member.mention}, you have been marked as inactive. Please chat to stay in the server!")

    def is_emoji_only(self, content: str) -> bool:
        # Simple regex for custom and unicode emojis
        custom_emoji_re = re.compile(r"<a?:\w+:\d+>")
        unicode_emoji_re = re.compile(r"[\U00010000-\U0010ffff]", flags=re.UNICODE)
        
        stripped = custom_emoji_re.sub("", content)
        stripped = unicode_emoji_re.sub("", stripped).strip()
        return len(stripped) == 0

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        conf = await self.config.guild(message.guild).all()
        
        if message.channel.id in conf["ignored_channels"]:
            return
        
        if conf["ignore_commands"]:
            ctx = await self.bot.get_context(message)
            if ctx.valid:
                return

        if len(message.content) < conf["min_chars"]:
            return

        if self.is_emoji_only(message.content):
            return

        # Record activity
        now = datetime.utcnow().timestamp()
        async with self.config.member(message.author).message_history() as history:
            history.append(now)
            # Prune old
            cutoff = now - (conf["msg_window_hours"] * 3600)
            history[:] = [t for t in history if t > cutoff]
            
            if len(history) >= conf["msg_threshold"]:
                await self.config.member(message.author).last_active.set(now)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        # User joined or switched
        if after.channel and not before.channel:
            await self.config.member(member).voice_start.set(datetime.utcnow().timestamp())
        
        # User left or switched
        elif before.channel and not after.channel:
            start_ts = await self.config.member(member).voice_start()
            if not start_ts:
                return
            
            duration_mins = (datetime.utcnow().timestamp() - start_ts) / 60
            conf = await self.config.guild(member.guild).all()
            
            # Check conditions
            if duration_mins >= conf["voice_min_minutes"]:
                # Check user count in channel (during the session is hard, so we check at leave time as a proxy)
                # In a more perfect world, we'd track count throughout.
                if len(before.channel.members) >= conf["voice_min_users"]:
                    await self.config.member(member).last_active.set(datetime.utcnow().timestamp())
            
            await self.config.member(member).voice_start.clear()

    async def is_active(self, member: discord.Member):
        """Public API for other cogs"""
        data = await self.config.member(member).all()
        last_active = data["last_active"]
        conf = await self.config.guild(member.guild).all()
        
        if not last_active:
            return False, None
            
        days_diff = (datetime.utcnow().timestamp() - last_active) / 86400
        is_active = days_diff < conf["inactivity_days"]
        return is_active, last_active

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def activitytrackerset(self, ctx):
        """Settings for ActivityTracker"""
        pass

    @activitytrackerset.command(name="view")
    async def setter_view(self, ctx):
        """View all current settings"""
        conf = await self.config.guild(ctx.guild).all()
        rules_str = "\n".join([f"Lvl {r['level']}: {r['action']} after {r['days']} days" for r in conf["policing_rules"]]) or "None"
        channels = [ctx.guild.get_channel(c).name for c in conf["ignored_channels"] if ctx.guild.get_channel(c)]
        
        msg = (
            f"**ActivityTracker Settings for {ctx.guild.name}**\n"
            f"Preview Mode: {'ON' if conf['preview_mode'] else 'OFF'}\n"
            f"Message Threshold: {conf['msg_threshold']} msgs / {conf['msg_window_hours']}h\n"
            f"Min Characters: {conf['min_chars']}\n"
            f"Ignore Commands: {conf['ignore_commands']}\n"
            f"Voice Quota: {conf['voice_min_minutes']}m with {conf['voice_min_users']} users\n"
            f"Inactivity Mark: {conf['inactivity_days']} days\n"
            f"Ignored Channels: {humanize_list(channels) if channels else 'None'}\n"
            f"Report Channel: {getattr(ctx.guild.get_channel(conf['report_channel']), 'mention', 'Not Set')}\n"
            f"**Policing Rules:**\n{rules_str}"
        )
        await ctx.send(msg)

    @activitytrackerset.command()
    async def preview(self, ctx, toggle: bool):
        """Toggle preview mode (no actions taken when ON)"""
        was_on = await self.config.guild(ctx.guild).preview_mode()
        await self.config.guild(ctx.guild).preview_mode.set(toggle)
        
        if was_on and not toggle:
            await ctx.send("Preview mode disabled. Calculating initial report...")
            await self.run_policing(manual_report_ctx=ctx)
        else:
            await ctx.send(f"Preview mode set to {toggle}")

    @activitytrackerset.command()
    async def msgthreshold(self, ctx, messages: int, hours: int):
        """Set message activity threshold (e.g. 12 72)"""
        await self.config.guild(ctx.guild).msg_threshold.set(messages)
        await self.config.guild(ctx.guild).msg_window_hours.set(hours)
        await ctx.tick()

    @activitytrackerset.command()
    async def voicequota(self, ctx, minutes: int, min_users: int):
        """Set voice activity threshold (e.g. 20 2)"""
        await self.config.guild(ctx.guild).voice_min_minutes.set(minutes)
        await self.config.guild(ctx.guild).voice_min_users.set(min_users)
        await ctx.tick()

    @activitytrackerset.command()
    async def inactivitydays(self, ctx, days: int):
        """How many days until a user is considered inactive since last activity"""
        await self.config.guild(ctx.guild).inactivity_days.set(days)
        await ctx.tick()

    @activitytrackerset.command()
    async def minchars(self, ctx, chars: int):
        """Minimum characters for a message to count"""
        await self.config.guild(ctx.guild).min_chars.set(chars)
        await ctx.tick()

    @activitytrackerset.command()
    async def ignorecommands(self, ctx, toggle: bool):
        """Whether bot commands count towards activity"""
        await self.config.guild(ctx.guild).ignore_commands.set(toggle)
        await ctx.tick()

    @activitytrackerset.command()
    async def reportchannel(self, ctx, channel: discord.TextChannel):
        """Where to post mentions or initial reports"""
        await self.config.guild(ctx.guild).report_channel.set(channel.id)
        await ctx.tick()

    @activitytrackerset.group(name="rule")
    async def rules(self, ctx):
        """Manage policing rules"""
        pass

    @rules.command(name="add")
    async def rule_add(self, ctx, level: int, days: int, action: Literal["kick", "warn", "mention"]):
        """Add a policing rule (e.g. 5 45 warn)"""
        async with self.config.guild(ctx.guild).policing_rules() as r:
            r.append({"level": level, "days": days, "action": action})
        await ctx.tick()

    @rules.command(name="remove")
    async def rule_remove(self, ctx, index: int):
        """Remove a rule by its index in [p]activitytrackerset view"""
        async with self.config.guild(ctx.guild).policing_rules() as r:
            try:
                r.pop(index)
                await ctx.tick()
            except IndexError:
                await ctx.send("Invalid index.")

    @activitytrackerset.command()
    async def ignorechannel(self, ctx, channel: discord.TextChannel):
        """Toggle channel exclusion from activity tracking"""
        async with self.config.guild(ctx.guild).ignored_channels() as c:
            if channel.id in c:
                c.remove(channel.id)
                await ctx.send(f"Now tracking activity in {channel.mention}")
            else:
                c.append(channel.id)
                await ctx.send(f"Excluding {channel.mention} from activity tracking")

    @commands.command()
    @checks.mod_or_permissions(manage_nicknames=True)
    async def markactive(self, ctx, member: discord.Member):
        """Manually mark a user as active as of right now"""
        await self.config.member(member).last_active.set(datetime.utcnow().timestamp())
        await ctx.send(f"Marked {member.display_name} as active.")

    @commands.command()
    @checks.mod_or_permissions(manage_nicknames=True)
    async def markinactive(self, ctx, member: discord.Member):
        """Manually mark a user as inactive (wipes activity history)"""
        await self.config.member(member).last_active.clear()
        await self.config.member(member).message_history.set([])
        await ctx.send(f"Marked {member.display_name} as inactive.")

    @commands.command()
    async def activity(self, ctx, member: Optional[discord.Member] = None):
        """Check your own or another user's activity status"""
        target = member or ctx.author
        is_act, last_ts = await self.is_active(target)
        
        if not last_ts:
            await ctx.send(f"{target.display_name} has no recorded activity.")
            return
            
        dt = datetime.fromtimestamp(last_ts)
        status = "Active" if is_act else "Inactive"
        await ctx.send(f"**{target.display_name}**\nStatus: {status}\nLast Active: {dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")