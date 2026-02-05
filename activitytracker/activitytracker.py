import discord
import logging
import re
import asyncio
import time
from datetime import datetime, timedelta
from typing import Literal, Optional, Union, Dict, List, Any

from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify, humanize_list, bold

log = logging.getLogger("red.activitytracker")

class RunPolicingView(discord.ui.View):
    """View to confirm running a policing check immediately."""
    def __init__(self, cog, ctx):
        super().__init__(timeout=60)
        self.cog = cog
        self.ctx = ctx
        self.message: Optional[discord.Message] = None

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(view=self)

    @discord.ui.button(label="Run Policing Now", style=discord.ButtonStyle.primary, emoji="🚨")
    async def run_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return

        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        await self.message.edit(view=self)
        
        await interaction.followup.send("Initiating manual policing run...", ephemeral=True)
        await self.cog.run_policing(manual_report_ctx=self.ctx)

class ActivityTracker(commands.Cog):
    """
    Track user message and voice activity with automated policing.
    Integrates with LevelUp, WarnSystem, and Hibernate.
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
        }

        default_member = {
            "last_active": None, # Timestamp
            "message_history": [], # List of timestamps
            "voice_start": None, # Temp storage for VC session
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        
        self.policing_task = self.bot.loop.create_task(self.initialize_loop())

    def cog_unload(self):
        if self.policing_task:
            self.policing_task.cancel()

    async def initialize_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.run_policing()
            except Exception as e:
                log.error(f"Error in policing loop: {e}", exc_info=True)
            await asyncio.sleep(3600) # Run every hour

    # --------------------------------------------------------------------------------
    # Integration Helpers
    # --------------------------------------------------------------------------------

    async def _get_level(self, member: discord.Member) -> int:
        """Get member level via LevelUp cog if available."""
        cog = self.bot.get_cog("LevelUp")
        if cog:
            try:
                # Check for get_level method
                if hasattr(cog, "get_level"):
                    result = cog.get_level(member)
                    # Check if it's a coroutine (async) and await it if so
                    if asyncio.iscoroutine(result):
                        return await result
                    # Otherwise return directly
                    return result
            except Exception as e:
                log.debug(f"Failed to fetch level for {member}: {e}")
        return 0

    async def _is_hibernating(self, member: discord.Member) -> bool:
        """Check if member is hibernating via Hibernate cog."""
        cog = self.bot.get_cog("Hibernate")
        if cog:
            try:
                return await cog.is_hibernating(member)
            except Exception:
                return False
        return False

    async def _warn_user(self, guild: discord.Guild, member: discord.Member, reason: str) -> bool:
        """Warn user via WarnSystem cog if available."""
        cog = self.bot.get_cog("WarnSystem")
        if cog and hasattr(cog, "api"):
            try:
                # Based on WarnSystem API: warn(guild, members, author, level, reason...)
                # members is Iterable
                await cog.api.warn(
                    guild=guild,
                    members=[member],
                    author=guild.me,
                    level=1, # Simple warning
                    reason=reason,
                    take_action=True
                )
                return True
            except Exception as e:
                log.error(f"WarnSystem integration failed: {e}")
        return False

    # --------------------------------------------------------------------------------
    # Core Logic
    # --------------------------------------------------------------------------------

    async def run_policing(self, manual_report_ctx=None):
        """
        Main logic for checking inactivity and performing actions.
        """
        for guild in self.bot.guilds:
            if manual_report_ctx and manual_report_ctx.guild != guild:
                continue

            conf = await self.config.guild(guild).all()
            
            # If in preview mode and not manually running a report, skip automated actions
            if conf["preview_mode"] and not manual_report_ctx:
                continue

            report_entries = []
            rules = sorted(conf["policing_rules"], key=lambda x: x['days'], reverse=True)
            report_channel = guild.get_channel(conf["report_channel"])

            # If not manual, and no report channel, and preview mode is on, we can't really report anywhere.
            if not manual_report_ctx and conf["preview_mode"] and not report_channel:
                continue

            for member in guild.members:
                if member.bot:
                    continue
                
                if await self._is_hibernating(member):
                    continue

                mem_data = await self.config.member(member).all()
                last_active = mem_data["last_active"]
                
                # If never active, use join date
                if not last_active:
                    last_active = member.joined_at.timestamp()

                days_inactive = (datetime.utcnow().timestamp() - last_active) / 86400

                # Determine Rule
                applicable_rule = None
                user_level = await self._get_level(member)
                
                # Verify user_level is an int before comparison, just in case
                if not isinstance(user_level, (int, float)):
                    user_level = 0

                for rule in rules:
                    if user_level >= rule["level"] and days_inactive >= rule["days"]:
                        applicable_rule = rule
                        break
                
                if applicable_rule:
                    action_str = f"Action: {applicable_rule['action'].upper()} (Rule: Lvl {applicable_rule['level']} / {applicable_rule['days']} days)"
                    entry = f"• {member.display_name} (Lvl {user_level}): Inactive {int(days_inactive)} days. {action_str}"
                    
                    if manual_report_ctx:
                        report_entries.append(entry)
                    elif conf["preview_mode"]:
                        report_entries.append(entry)
                    else:
                        # REAL ACTION
                        await self._execute_policing_action(member, applicable_rule, int(days_inactive))
                        # Still add to report for log
                        report_entries.append(f"{entry} [EXECUTED]")

            # Send Reports
            if report_entries:
                header = f"**ActivityTracker Report** | Guild: {guild.name}\n"
                if conf["preview_mode"]:
                    header += "**[PREVIEW MODE]** No actions were taken. The following would have triggered:\n"
                else:
                    header += "**[LIVE MODE]** The following actions were processed:\n"

                full_msg = header + "\n".join(report_entries)
                
                # If manual context, send there
                if manual_report_ctx:
                    for page in pagify(full_msg):
                        await manual_report_ctx.send(page)
                
                # Otherwise send to report channel if configured
                elif report_channel:
                    try:
                        for page in pagify(full_msg):
                            await report_channel.send(page)
                    except discord.Forbidden:
                        log.warning(f"Missing permissions to send report to {report_channel.name} in {guild.name}")

    async def _execute_policing_action(self, member: discord.Member, rule: dict, days: int):
        action = rule["action"].lower()
        reason = f"ActivityTracker: Inactive for {days} days (Rule: >{rule['days']} days, Lvl {rule['level']})."
        
        try:
            if action == "kick":
                if member.top_role >= member.guild.me.top_role:
                    log.warning(f"Cannot kick {member} in {member.guild.name}: Role hierarchy.")
                    return
                await member.kick(reason=reason)
            
            elif action == "warn":
                # Try WarnSystem first
                success = await self._warn_user(member.guild, member, reason)
                if not success:
                    # Fallback to simple DM if warning system fails
                    try:
                        await member.send(f"**Inactivity Warning** in {member.guild.name}: {reason}")
                    except discord.Forbidden:
                        pass
            
            elif action == "mention":
                conf = await self.config.guild(member.guild).all()
                channel = member.guild.get_channel(conf["report_channel"])
                if channel:
                    try:
                        await channel.send(f"{member.mention}, you have been marked as inactive ({days} days). Please chat to stay in the server!")
                    except discord.Forbidden:
                        pass
        except discord.Forbidden:
            log.warning(f"Forbidden error executing {action} on {member} in {member.guild.name}")
        except Exception as e:
            log.error(f"Error executing {action} on {member}: {e}")

    # --------------------------------------------------------------------------------
    # Events
    # --------------------------------------------------------------------------------

    def is_emoji_only(self, content: str) -> bool:
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
            if message.content.startswith(tuple(await self.bot.get_prefix(message))):
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

        now = datetime.utcnow().timestamp()

        # 1. Activity END (Left or Moved)
        if before.channel:
            if not after.channel or (after.channel and after.channel.id != before.channel.id):
                start_ts = await self.config.member(member).voice_start()
                if start_ts:
                    duration_mins = (now - start_ts) / 60
                    conf = await self.config.guild(member.guild).all()
                    
                    # Logic: Check user count in the channel they LEFT
                    # This implies they were part of a valid conversation
                    user_count = len(before.channel.members) 
                    # Note: They are already gone from 'before.channel.members' in most cache impls, 
                    # but 'before' snapshot might count them? 
                    # Safest is to just check config thresholds against duration.
                    
                    if duration_mins >= conf["voice_min_minutes"]:
                        # We only count if the channel had enough people. 
                        # Since user just left, we check current count + 1 roughly, or just ignore exact user count for simplicity
                        # strictly adhering to user request "voice_min_users"
                        # We'll check the current count of the channel they left.
                        if len(before.channel.members) >= (conf["voice_min_users"] - 1): 
                            await self.config.member(member).last_active.set(now)
                    
                    await self.config.member(member).voice_start.clear()

        # 2. Activity START (Joined or Moved To)
        if after.channel:
            if not before.channel or (before.channel and before.channel.id != after.channel.id):
                await self.config.member(member).voice_start.set(now)

    # --------------------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------------------

    async def is_active(self, member: discord.Member):
        data = await self.config.member(member).all()
        last_active = data["last_active"]
        conf = await self.config.guild(member.guild).all()
        
        if not last_active:
            # If no data, use join date
            last_active = member.joined_at.timestamp()
            
        days_diff = (datetime.utcnow().timestamp() - last_active) / 86400
        is_active = days_diff < conf["inactivity_days"]
        return is_active, last_active

    # --------------------------------------------------------------------------------
    # Commands
    # --------------------------------------------------------------------------------

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def activitytrackerset(self, ctx):
        """Settings for ActivityTracker"""
        pass

    @activitytrackerset.command(name="view")
    async def setter_view(self, ctx):
        """View all current settings"""
        conf = await self.config.guild(ctx.guild).all()
        
        # Format Rules Table
        rules_data = []
        if conf["policing_rules"]:
            rules = sorted(conf["policing_rules"], key=lambda x: x['level'])
            rules_data.append(["Lvl", "Days", "Action"])
            rules_data.append(["---", "----", "------"])
            for r in rules:
                rules_data.append([str(r['level']), str(r['days']), r['action']])
        
        # Simple column alignment
        rules_str = "No rules configured."
        if rules_data:
            col_widths = [max(len(row[i]) for row in rules_data) for i in range(len(rules_data[0]))]
            lines = []
            for row in rules_data:
                line = "  ".join(word.ljust(width) for word, width in zip(row, col_widths))
                lines.append(line)
            rules_str = "\n".join(lines)

        channels = [ctx.guild.get_channel(c).name for c in conf["ignored_channels"] if ctx.guild.get_channel(c)]
        report_chan = ctx.guild.get_channel(conf["report_channel"])
        report_chan_name = report_chan.mention if report_chan else "Not Set (WARNING)"

        settings_info = (
            f"[General Settings]\n"
            f"Preview Mode:      {conf['preview_mode']}\n"
            f"Inactivity Days:   {conf['inactivity_days']} days\n"
            f"Report Channel:    {report_chan_name}\n"
            f"\n[Message Activity]\n"
            f"Threshold:         {conf['msg_threshold']} msgs in {conf['msg_window_hours']} hours\n"
            f"Min Characters:    {conf['min_chars']}\n"
            f"Ignore Commands:   {conf['ignore_commands']}\n"
            f"\n[Voice Activity]\n"
            f"Quota:             {conf['voice_min_minutes']} mins (Min {conf['voice_min_users']} users)\n"
            f"\n[Ignored Channels]\n"
            f"{humanize_list(channels) if channels else 'None'}\n"
        )
        
        await ctx.send(box(settings_info, lang="ini"))
        if conf["policing_rules"]:
            await ctx.send(box(rules_str, lang="css", title="Policing Rules"))

    @activitytrackerset.command(name="listusers")
    async def list_users(self, ctx, status_filter: Literal["active", "inactive", "hibernating"] = None):
        """
        List users with their activity status, ID, and last active date.
        
        Optional: Filter by 'active', 'inactive', or 'hibernating'.
        """
        if not ctx.guild:
            return
        
        await ctx.typing()

        conf = await self.config.guild(ctx.guild).all()
        inactivity_days = conf["inactivity_days"]
        # optimization: fetch all member data at once
        all_member_data = await self.config.all_members(ctx.guild)
        
        # Determine title based on filter
        filter_str = status_filter.title() if status_filter else "All"
        lines = [f"--- {filter_str} Users ---"]
        lines.append(f"{'User':<20} {'ID':<20} {'Last Active':<12} {'Status'}")
        lines.append("-" * 65)

        for member in ctx.guild.members:
            if member.bot:
                continue

            status = "Inactive"
            is_hibernating = await self._is_hibernating(member)
            if is_hibernating:
                status = "Hibernating"

            # Check Activity
            mem_data = all_member_data.get(member.id, {})
            last_active = mem_data.get("last_active")
            
            last_active_str = "Never"
            if last_active:
                last_active_str = datetime.fromtimestamp(last_active).strftime("%Y-%m-%d")
                
                # If they are hibernating, status is already "Hibernating"
                if not is_hibernating:
                    days_diff = (datetime.utcnow().timestamp() - last_active) / 86400
                    if days_diff < inactivity_days:
                        status = "Active"
            
            # Filter logic
            if status_filter:
                if status.lower() != status_filter.lower():
                    continue

            # Format line
            name = member.name
            if len(name) > 19:
                name = name[:18] + "…"
            
            lines.append(f"{name:<20} {str(member.id):<20} {last_active_str:<12} {status}")

        text = "\n".join(lines)
        
        if len(lines) <= 3:
            await ctx.send(f"No users found matching filter: {status_filter or 'All'}")
            return

        for page in pagify(text, page_length=1900):
            await ctx.send(box(page, lang="text"))

    @activitytrackerset.command(name="markallactive")
    async def mark_all_active(self, ctx):
        """
        Mark ALL non-bot users in the server as active right now.
        Useful for initializing the cog on a server.
        """
        await ctx.send("Marking all users as active. This may take a moment for large servers...")
        
        async with ctx.typing():
            now = datetime.utcnow().timestamp()
            count = 0
            for member in ctx.guild.members:
                if not member.bot:
                    await self.config.member(member).last_active.set(now)
                    count += 1
        
        await ctx.send(f"Done. Successfully marked {count} users as Active.")

    @activitytrackerset.command(name="preview")
    async def preview_mode(self, ctx, toggle: bool):
        """
        Toggle preview mode.
        
        If ON: Policing reports are generated but no actions are taken.
        If OFF: Policing actions (kick, warn) are executed.
        """
        conf = await self.config.guild(ctx.guild).all()
        report_channel_id = conf["report_channel"]
        report_channel = ctx.guild.get_channel(report_channel_id) if report_channel_id else None

        if not report_channel:
            await ctx.send(
                "⚠️ **Warning:** No report channel is configured. "
                "If you enable Preview Mode, reports will have nowhere to go unless run manually. "
                "If you disable Preview Mode, 'Mention' actions will fail."
                "\nPlease set one via `[p]activitytrackerset reportchannel`."
            )

        await self.config.guild(ctx.guild).preview_mode.set(toggle)
        
        status_str = "ENABLED" if toggle else "DISABLED"
        msg_text = f"Preview mode is now **{status_str}**."
        if toggle:
            msg_text += "\nNo actions will be taken against users. Reports will be sent to the report channel."
        else:
            msg_text += "\n**WARNING:** The bot will now actively policing users (Kick/Warn) based on your rules."

        # Prompt to run immediately
        view = RunPolicingView(self, ctx)
        view.message = await ctx.send(msg_text, view=view)

    @activitytrackerset.command(name="run")
    async def manual_run(self, ctx):
        """Manually trigger a policing run and output the report here."""
        await ctx.send("Running policing check...")
        await self.run_policing(manual_report_ctx=ctx)

    @activitytrackerset.group(name="rule")
    async def rules(self, ctx):
        """Manage policing rules"""
        pass

    @rules.command(name="add")
    async def rule_add(self, ctx, level: int, days: int, action: Literal["kick", "warn", "mention"]):
        """
        Add a policing rule.
        
        Example: `[p]atset rule add 5 45 warn`
        (Users level 5+ inactive for 45+ days will be warned)
        """
        async with self.config.guild(ctx.guild).policing_rules() as r:
            # Check for duplicates
            for rule in r:
                if rule['level'] == level and rule['days'] == days:
                    await ctx.send("A rule with this level and day threshold already exists.")
                    return
            r.append({"level": level, "days": days, "action": action})
        await ctx.tick()
        await ctx.send(f"Rule added: Level {level}+ inactive for {days}+ days -> {action}")

    @rules.command(name="remove")
    async def rule_remove(self, ctx, level: int, days: int):
        """
        Remove a rule by level and days.
        """
        async with self.config.guild(ctx.guild).policing_rules() as r:
            original_len = len(r)
            r[:] = [rule for rule in r if not (rule['level'] == level and rule['days'] == days)]
            
            if len(r) < original_len:
                await ctx.send("Rule removed.")
            else:
                await ctx.send("No matching rule found.")

    @rules.command(name="clear")
    async def rule_clear(self, ctx):
        """Clear all policing rules."""
        await self.config.guild(ctx.guild).policing_rules.set([])
        await ctx.tick()

    @activitytrackerset.command()
    async def reportchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for policing reports and mentions."""
        await self.config.guild(ctx.guild).report_channel.set(channel.id)
        await ctx.send(f"Report channel set to {channel.mention}")

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

    @activitytrackerset.command()
    async def msgthreshold(self, ctx, messages: int, hours: int):
        """Set message activity threshold (e.g. 12 72)"""
        await self.config.guild(ctx.guild).msg_threshold.set(messages)
        await self.config.guild(ctx.guild).msg_window_hours.set(hours)
        await ctx.send(f"Threshold set: {messages} messages within {hours} hours required to update 'Last Active'.")

    @activitytrackerset.command()
    async def voicequota(self, ctx, minutes: int, min_users: int):
        """Set voice activity threshold (e.g. 20 2)"""
        await self.config.guild(ctx.guild).voice_min_minutes.set(minutes)
        await self.config.guild(ctx.guild).voice_min_users.set(min_users)
        await ctx.send(f"Voice Quota set: {minutes} mins in a channel with at least {min_users} users.")

    @activitytrackerset.command()
    async def inactivitydays(self, ctx, days: int):
        """Set base inactivity definition in days."""
        await self.config.guild(ctx.guild).inactivity_days.set(days)
        await ctx.send(f"Users are considered inactive after {days} days.")

    @commands.command()
    @checks.mod_or_permissions(manage_nicknames=True)
    async def markactive(self, ctx, member: discord.Member):
        """Manually mark a user as active as of right now"""
        await self.config.member(member).last_active.set(datetime.utcnow().timestamp())
        await ctx.send(f"Marked {member.display_name} as active.")

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
        color = discord.Color.green() if is_act else discord.Color.red()
        
        embed = discord.Embed(title=f"Activity Status: {target.display_name}", color=color)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Last Active", value=dt.strftime('%Y-%m-%d %H:%M UTC'), inline=True)
        
        # Add Level info if LevelUp is loaded
        lvl = await self._get_level(target)
        if lvl > 0:
             embed.add_field(name="Level", value=str(lvl), inline=True)

        await ctx.send(embed=embed)