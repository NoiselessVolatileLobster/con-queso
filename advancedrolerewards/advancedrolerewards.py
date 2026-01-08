import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify, humanize_list
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
import asyncio
import datetime
import logging
import json
import io

log = logging.getLogger("red.advancedrolerewards")

class AdvancedRoleRewards(commands.Cog):
    """
    Grant role rewards based on level and tenure.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)

        # Default Settings
        default_guild = {
            "level_rewards": [],    # [{"level": int, "role_id": int}]
            "days_rewards": [],     # [{"days": int, "role_id": int}]
            "advanced_rewards": [], # [{"days": int, "level": int, "role_id": int}]
            "secret_rewards": [],   # [{"days": int, "level": int, "role_id": int}]
            "multistep_rewards": {}, # {"name": [{"days": int, "level": int, "role_id": int}]}
            "optin_rewards": [],    # [{"base_role_id": int, "days": int, "level": int, "role_id": int}]
        }
        
        default_user = {
            "start_date": None # Timestamp
        }

        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)

        self.bg_loop = self.bot.loop.create_task(self.check_rewards_loop())

    def cog_unload(self):
        if self.bg_loop:
            self.bg_loop.cancel()

    # =========================================================================
    # LOGIC & HELPERS
    # =========================================================================

    async def get_member_level(self, member: discord.Member) -> int:
        """
        Attempts to retrieve level from LevelUp cog. 
        Tries multiple common attribute patterns for compatibility.
        """
        levelup = self.bot.get_cog("LevelUp")
        if not levelup:
            return 0
        
        # Method 1: cached_users (Vertyco LevelUp common pattern)
        if hasattr(levelup, "data") and hasattr(levelup.data, "get_user"):
             # Some versions use a generic data manager
             pass 

        # Method 2: Async method to get profile
        if hasattr(levelup, "get_user_profile"):
            try:
                profile = await levelup.get_user_profile(member.id, member.guild.id)
                return profile.level
            except:
                pass

        # Method 3: Direct DB access (Common in some forks)
        if hasattr(levelup, "db"):
            try:
                # This depends heavily on the specific database driver wrapper
                # Simplified guess:
                data = await levelup.db.users.find_one({"user_id": member.id, "guild_id": member.guild.id})
                if data:
                    return data.get("level", 0)
            except:
                pass
        
        # Method 4: Cache dict
        if hasattr(levelup, "cache"):
             key = f"{member.guild.id}-{member.id}"
             if key in levelup.cache:
                 return levelup.cache[key].get("level", 0)

        # Fallback: Check if there is a 'get_level' public method
        if hasattr(levelup, "get_level"):
            try:
                lvl = await levelup.get_level(member.id, member.guild.id)
                return int(lvl)
            except:
                pass
        
        return 0

    async def get_tenure_days(self, member: discord.Member) -> int:
        """
        Calculates tenure based on Config start_date or joined_at.
        """
        start_ts = await self.config.user(member).start_date()
        
        if start_ts:
            start_dt = datetime.datetime.fromtimestamp(start_ts, tz=datetime.timezone.utc)
        else:
            start_dt = member.joined_at

        if not start_dt:
            # Fallback for edge cases where joined_at is None (rare API quirk)
            return 0

        # Ensure start_dt is aware (discord.py 2.x standard)
        now = discord.utils.utcnow()
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)

        delta = now - start_dt
        return max(0, delta.days)

    async def check_rewards_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    settings = await self.config.guild(guild).all()
                    
                    # Note: Requires Privileged Intents (Members) to iterate guild.members
                    for member in guild.members:
                        if member.bot:
                            continue
                        await self.process_member_rewards(member, settings)
                        await asyncio.sleep(0.01) # Yield to prevent blocking
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in reward loop: {e}")
            
            await asyncio.sleep(300) # Check every 5 minutes

    async def process_member_rewards(self, member: discord.Member, settings: dict, level_override: int = None) -> tuple:
        level = level_override if level_override is not None else await self.get_member_level(member)
        days = await self.get_tenure_days(member)
        
        to_add = []
        to_remove = []

        # 1. Level Rewards
        for reward in settings["level_rewards"]:
            role = member.guild.get_role(reward["role_id"])
            if role:
                if level >= reward["level"]:
                    if role not in member.roles:
                        to_add.append(role)

        # 2. Days Rewards
        for reward in settings["days_rewards"]:
            role = member.guild.get_role(reward["role_id"])
            if role:
                if days >= reward["days"]:
                    if role not in member.roles:
                        to_add.append(role)

        # 3. Advanced Rewards
        for reward in settings["advanced_rewards"]:
            role = member.guild.get_role(reward["role_id"])
            if role:
                if level >= reward["level"] and days >= reward["days"]:
                    if role not in member.roles:
                        to_add.append(role)

        # 4. Secret Rewards
        for reward in settings["secret_rewards"]:
            role = member.guild.get_role(reward["role_id"])
            if role:
                if level >= reward["level"] and days >= reward["days"]:
                    if role not in member.roles:
                        to_add.append(role)

        # 5. Opt-in Rewards
        for reward in settings["optin_rewards"]:
            target_role = member.guild.get_role(reward["role_id"])
            base_role = member.guild.get_role(reward["base_role_id"])
            
            if target_role and base_role:
                if base_role in member.roles:
                    if level >= reward["level"] and days >= reward["days"]:
                        if target_role not in member.roles:
                            to_add.append(target_role)

        # 6. Multistep Rewards
        for name, steps in settings["multistep_rewards"].items():
            highest_step_index = -1
            
            for idx, step in enumerate(steps):
                if level >= step["level"] and days >= step["days"]:
                    highest_step_index = idx
                else:
                    break
            
            if highest_step_index != -1:
                target_step = steps[highest_step_index]
                target_role = member.guild.get_role(target_step["role_id"])
                
                if target_role and target_role not in member.roles:
                    to_add.append(target_role)
                
                for idx, step in enumerate(steps):
                    if idx != highest_step_index:
                        r = member.guild.get_role(step["role_id"])
                        if r and r in member.roles:
                            to_remove.append(r)

        # Apply Changes
        applied_adds = []
        applied_removes = []

        if to_add:
            try:
                await member.add_roles(*to_add, reason="AdvancedRoleRewards: Criteria met")
                applied_adds = to_add
            except discord.Forbidden:
                pass
        
        if to_remove:
            try:
                await member.remove_roles(*to_remove, reason="AdvancedRoleRewards: Criteria updated")
                applied_removes = to_remove
            except discord.Forbidden:
                pass

        return applied_adds, applied_removes

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def get_reward_status(self, member: discord.Member) -> list:
        """
        Public API for other cogs.
        """
        return asyncio.create_task(self._calculate_reward_status(member))

    async def _calculate_reward_status(self, member: discord.Member):
        settings = await self.config.guild(member.guild).all()
        level = await self.get_member_level(member)
        days = await self.get_tenure_days(member)
        
        results = []

        def get_status_str(req_level, req_days, is_done):
            if is_done:
                return "Completed"
            
            level_diff = max(0, req_level - level)
            days_diff = max(0, req_days - days)
            
            if level_diff > 0 and days_diff > 0:
                return f"Pending {days_diff} days and {level_diff} levels"
            elif level_diff > 0:
                return f"Pending {level_diff} levels"
            elif days_diff > 0:
                return f"Pending {days_diff} days"
            return "Pending processing"

        # Level
        for r in settings["level_rewards"]:
            role = member.guild.get_role(r["role_id"])
            if not role: continue
            is_done = role in member.roles
            results.append({
                "role": role,
                "status": get_status_str(r["level"], 0, is_done),
                "type": "Level"
            })

        # Days
        for r in settings["days_rewards"]:
            role = member.guild.get_role(r["role_id"])
            if not role: continue
            is_done = role in member.roles
            results.append({
                "role": role,
                "status": get_status_str(0, r["days"], is_done),
                "type": "Days"
            })

        # Advanced
        for r in settings["advanced_rewards"]:
            role = member.guild.get_role(r["role_id"])
            if not role: continue
            is_done = role in member.roles
            results.append({
                "role": role,
                "status": get_status_str(r["level"], r["days"], is_done),
                "type": "Advanced"
            })
        
        # Opt-in
        for r in settings["optin_rewards"]:
            target_role = member.guild.get_role(r["role_id"])
            base_role = member.guild.get_role(r["base_role_id"])
            if not target_role: continue
            
            if base_role and base_role not in member.roles:
                status = "Not Eligible (Missing Base Role)"
            else:
                is_done = target_role in member.roles
                status = get_status_str(r["level"], r["days"], is_done)
            
            results.append({
                "role": target_role,
                "status": status,
                "type": "Opt-in"
            })

        # Multistep
        for name, steps in settings["multistep_rewards"].items():
            found_next = False
            for step in steps:
                role = member.guild.get_role(step["role_id"])
                if not role: continue
                
                req_met = level >= step["level"] and days >= step["days"]
                
                if not req_met:
                    results.append({
                        "role": role,
                        "status": get_status_str(step["level"], step["days"], False),
                        "type": f"Multistep ({name})"
                    })
                    found_next = True
                    break
            
            if not found_next:
                last_step = steps[-1]
                role = member.guild.get_role(last_step["role_id"])
                if role:
                    results.append({
                        "role": role,
                        "status": "Completed",
                        "type": f"Multistep ({name})"
                    })

        return results

    # =========================================================================
    # EVENTS
    # =========================================================================

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot: return
        await self.config.user(member).start_date.set(discord.utils.utcnow().timestamp())
        
        settings = await self.config.guild(member.guild).all()
        await self.process_member_rewards(member, settings)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self.config.user(member).clear()

    @commands.Cog.listener()
    async def on_member_levelup(self, guild: discord.Guild, member: discord.Member, message: discord.Message, channel: discord.abc.GuildChannel, new_level: int):
        """
        Listener for LevelUp cog events.
        """
        if member.bot:
            return
            
        settings = await self.config.guild(guild).all()
        # Pass new_level directly to avoid race conditions with DB updates
        await self.process_member_rewards(member, settings, level_override=new_level)

    # =========================================================================
    # COMMANDS
    # =========================================================================

    @commands.group(name="rolerewardset")
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def rolerewardset(self, ctx):
        """Configure Advanced Role Rewards."""
        pass

    # --- LEVEL ---
    @rolerewardset.group(name="level")
    async def rrs_level(self, ctx):
        """Manage Level-based rewards."""
        pass

    @rrs_level.command(name="add")
    async def rrs_level_add(self, ctx, level: int, role: discord.Role):
        """Add a level reward."""
        if level < 1:
            return await ctx.send("Level must be greater than 0.")
        
        async with self.config.guild(ctx.guild).level_rewards() as rewards:
            for r in rewards:
                if r["level"] == level and r["role_id"] == role.id:
                    return await ctx.send("This reward already exists.")
            
            rewards.append({"level": level, "role_id": role.id})
            rewards.sort(key=lambda x: x["level"])
        
        await ctx.send(f"Added reward: Level {level} -> {role.mention}")

    @rrs_level.command(name="remove")
    async def rrs_level_remove(self, ctx, level: int, role: discord.Role):
        """Remove a level reward."""
        async with self.config.guild(ctx.guild).level_rewards() as rewards:
            original_len = len(rewards)
            rewards[:] = [r for r in rewards if not (r["level"] == level and r["role_id"] == role.id)]
            
            if len(rewards) == original_len:
                return await ctx.send("Reward not found.")
        
        await ctx.send("Reward removed.")

    @rrs_level.command(name="list")
    async def rrs_level_list(self, ctx):
        """List level rewards."""
        rewards = await self.config.guild(ctx.guild).level_rewards()
        if not rewards:
            return await ctx.send("No level rewards configured.")
        
        text = ""
        for r in rewards:
            role = ctx.guild.get_role(r["role_id"])
            role_name = role.mention if role else "[Deleted Role]"
            text += f"Level {r['level']}: {role_name}\n"
        
        await self._send_paginated(ctx, text, "Level Rewards")

    # --- DAYS ---
    @rolerewardset.group(name="days")
    async def rrs_days(self, ctx):
        """Manage Days-based rewards."""
        pass

    @rrs_days.command(name="add")
    async def rrs_days_add(self, ctx, days: int, role: discord.Role):
        """Add a days reward."""
        if days < 1:
            return await ctx.send("Days must be greater than 0.")
            
        async with self.config.guild(ctx.guild).days_rewards() as rewards:
            rewards.append({"days": days, "role_id": role.id})
            rewards.sort(key=lambda x: x["days"])
        
        await ctx.send(f"Added reward: {days} Days -> {role.mention}")

    @rrs_days.command(name="remove")
    async def rrs_days_remove(self, ctx, days: int, role: discord.Role):
        """Remove a days reward."""
        async with self.config.guild(ctx.guild).days_rewards() as rewards:
            rewards[:] = [r for r in rewards if not (r["days"] == days and r["role_id"] == role.id)]
        await ctx.send("Reward removed if it existed.")

    @rrs_days.command(name="list")
    async def rrs_days_list(self, ctx):
        """List days rewards."""
        rewards = await self.config.guild(ctx.guild).days_rewards()
        if not rewards:
            return await ctx.send("No days rewards configured.")
        
        text = ""
        for r in rewards:
            role = ctx.guild.get_role(r["role_id"])
            role_name = role.mention if role else "[Deleted Role]"
            text += f"{r['days']} Days: {role_name}\n"
        
        await self._send_paginated(ctx, text, "Days Rewards")

    # --- ADVANCED ---
    @rolerewardset.group(name="advanced")
    async def rrs_adv(self, ctx):
        """Manage Advanced (Level + Days) rewards."""
        pass

    @rrs_adv.command(name="add")
    async def rrs_adv_add(self, ctx, days: int, level: int, role: discord.Role):
        """Add an advanced reward (Days AND Level)."""
        async with self.config.guild(ctx.guild).advanced_rewards() as rewards:
            rewards.append({"days": days, "level": level, "role_id": role.id})
        await ctx.send(f"Added Advanced reward: {days} Days AND Level {level} -> {role.mention}")

    @rrs_adv.command(name="remove")
    async def rrs_adv_remove(self, ctx, days: int, level: int, role: discord.Role):
        """Remove an advanced reward."""
        async with self.config.guild(ctx.guild).advanced_rewards() as rewards:
            rewards[:] = [r for r in rewards if not (r["days"] == days and r["level"] == level and r["role_id"] == role.id)]
        await ctx.send("Reward removed if it existed.")

    @rrs_adv.command(name="list")
    async def rrs_adv_list(self, ctx):
        """List advanced rewards."""
        rewards = await self.config.guild(ctx.guild).advanced_rewards()
        if not rewards:
            return await ctx.send("No advanced rewards configured.")
        
        text = ""
        for r in rewards:
            role = ctx.guild.get_role(r["role_id"])
            role_name = role.mention if role else "[Deleted Role]"
            text += f"{r['days']} Days + Level {r['level']}: {role_name}\n"
        
        await self._send_paginated(ctx, text, "Advanced Rewards")

    # --- SECRET ---
    @rolerewardset.group(name="secret")
    async def rrs_secret(self, ctx):
        """Manage Secret rewards (No notification/status)."""
        pass

    @rrs_secret.command(name="add")
    async def rrs_secret_add(self, ctx, days: int, level: int, role: discord.Role):
        """Add a secret reward."""
        async with self.config.guild(ctx.guild).secret_rewards() as rewards:
            rewards.append({"days": days, "level": level, "role_id": role.id})
        await ctx.send(f"Added Secret reward: {days} Days AND Level {level} -> {role.mention}")

    @rrs_secret.command(name="remove")
    async def rrs_secret_remove(self, ctx, days: int, level: int, role: discord.Role):
        """Remove a secret reward."""
        async with self.config.guild(ctx.guild).secret_rewards() as rewards:
            rewards[:] = [r for r in rewards if not (r["days"] == days and r["level"] == level and r["role_id"] == role.id)]
        await ctx.send("Reward removed if it existed.")

    @rrs_secret.command(name="list")
    async def rrs_secret_list(self, ctx):
        """List secret rewards."""
        rewards = await self.config.guild(ctx.guild).secret_rewards()
        if not rewards:
            return await ctx.send("No secret rewards configured.")
        
        text = ""
        for r in rewards:
            role = ctx.guild.get_role(r["role_id"])
            role_name = role.mention if role else "[Deleted Role]"
            text += f"{r['days']} Days + Level {r['level']}: {role_name}\n"
        
        await self._send_paginated(ctx, text, "Secret Rewards")

    # --- OPT-IN ---
    @rolerewardset.group(name="optin")
    async def rrs_optin(self, ctx):
        """Manage Opt-in rewards (Requires base role)."""
        pass

    @rrs_optin.command(name="add")
    async def rrs_optin_add(self, ctx, base_role: discord.Role, days: int, level: int, target_role: discord.Role):
        """Add an opt-in reward."""
        async with self.config.guild(ctx.guild).optin_rewards() as rewards:
            rewards.append({
                "base_role_id": base_role.id,
                "days": days,
                "level": level,
                "role_id": target_role.id
            })
        await ctx.send(f"Added Opt-in: Requires {base_role.name} + {days} Days + Level {level} -> {target_role.mention}")

    @rrs_optin.command(name="remove")
    async def rrs_optin_remove(self, ctx, target_role: discord.Role):
        """Remove an opt-in reward by target role."""
        async with self.config.guild(ctx.guild).optin_rewards() as rewards:
            rewards[:] = [r for r in rewards if r["role_id"] != target_role.id]
        await ctx.send("Reward removed if it existed.")

    @rrs_optin.command(name="list")
    async def rrs_optin_list(self, ctx):
        """List opt-in rewards."""
        rewards = await self.config.guild(ctx.guild).optin_rewards()
        if not rewards:
            return await ctx.send("No opt-in rewards configured.")
        
        text = ""
        for r in rewards:
            base = ctx.guild.get_role(r["base_role_id"])
            target = ctx.guild.get_role(r["role_id"])
            base_name = base.name if base else "[Deleted]"
            target_name = target.mention if target else "[Deleted]"
            text += f"Base: {base_name} | {r['days']} Days + Level {r['level']} -> {target_name}\n"
        
        await self._send_paginated(ctx, text, "Opt-in Rewards")

    # --- MULTISTEP ---
    @rolerewardset.group(name="multistep")
    async def rrs_multi(self, ctx):
        """Manage Multistep rewards."""
        pass

    @rrs_multi.command(name="add")
    async def rrs_multi_add(self, ctx, name: str, days: int, level: int, role: discord.Role):
        """Add a step to a named multistep chain."""
        async with self.config.guild(ctx.guild).multistep_rewards() as rewards:
            if name not in rewards:
                rewards[name] = []
            
            rewards[name].append({"days": days, "level": level, "role_id": role.id})
        
        await ctx.send(f"Added step to chain '{name}': {days} Days + Level {level} -> {role.mention}")

    @rrs_multi.command(name="remove")
    async def rrs_multi_remove(self, ctx, name: str, index: int):
        """Remove a step from a chain by index (start at 1)."""
        async with self.config.guild(ctx.guild).multistep_rewards() as rewards:
            if name not in rewards:
                return await ctx.send("Chain not found.")
            
            try:
                removed = rewards[name].pop(index - 1)
                await ctx.send(f"Removed step {index} from '{name}'.")
                if not rewards[name]:
                    del rewards[name]
            except IndexError:
                await ctx.send("Invalid index.")

    @rrs_multi.command(name="list")
    async def rrs_multi_list(self, ctx):
        """List multistep rewards."""
        rewards = await self.config.guild(ctx.guild).multistep_rewards()
        if not rewards:
            return await ctx.send("No multistep rewards configured.")
        
        text = ""
        for name, steps in rewards.items():
            text += f"**Chain: {name}**\n"
            for idx, s in enumerate(steps, 1):
                role = ctx.guild.get_role(s["role_id"])
                role_name = role.mention if role else "[Deleted]"
                text += f"  Step {idx}: {s['days']} Days + Level {s['level']} -> {role_name}\n"
        
        await self._send_paginated(ctx, text, "Multistep Rewards")

    # --- START DATE ---
    @rolerewardset.group(name="startdate")
    async def rrs_startdate(self, ctx):
        """Manage User Start Dates."""
        pass

    @rrs_startdate.command(name="set")
    async def rrs_sd_set(self, ctx, user: discord.Member, date_str: str):
        """Set a user's start date (Format: YYYY-MM-DD)."""
        try:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            dt = dt.replace(tzinfo=datetime.timezone.utc)
            await self.config.user(user).start_date.set(dt.timestamp())
            await ctx.send(f"Start date for {user.display_name} set to {date_str}.")
            settings = await self.config.guild(ctx.guild).all()
            await self.process_member_rewards(user, settings)
        except ValueError:
            await ctx.send("Invalid format. Please use YYYY-MM-DD.")

    @rrs_startdate.command(name="view")
    async def rrs_sd_view(self, ctx, user: discord.Member):
        """View a user's configured start date."""
        ts = await self.config.user(user).start_date()
        if ts:
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            await ctx.send(f"{user.display_name}'s Start Date: {dt.strftime('%Y-%m-%d')}")
        else:
            await ctx.send(f"{user.display_name} uses their server join date: {user.joined_at.strftime('%Y-%m-%d') if user.joined_at else 'Unknown'}")

    # --- DEBUG ---
    @rolerewardset.command(name="debug")
    async def rrs_debug(self, ctx, member: discord.Member):
        """Debug a user's reward status."""
        level = await self.get_member_level(member)
        days = await self.get_tenure_days(member)
        
        status_list = await self._calculate_reward_status(member)
        
        embed = discord.Embed(title=f"Debug: {member.display_name}", color=discord.Color.blue())
        embed.add_field(name="Stats", value=f"Level: {level}\nTenure: {days} days", inline=False)
        
        if status_list:
            desc = ""
            for item in status_list:
                role_name = item['role'].name if item['role'] else "Deleted Role"
                desc += f"**{item['type']}**: {role_name} - {item['status']}\n"
            
            for page in pagify(desc):
                embed.description = page
                await ctx.send(embed=embed)
                embed = discord.Embed(color=discord.Color.blue())
        else:
            embed.description = "No relevant rewards found."
            await ctx.send(embed=embed)
    
    @rolerewardset.command(name="check")
    async def rrs_check(self, ctx, member: discord.Member):
        """
        Manually check and apply rewards for a specific member.
        Returns a list of roles added or removed.
        """
        async with ctx.typing():
            settings = await self.config.guild(ctx.guild).all()
            added, removed = await self.process_member_rewards(member, settings)
        
        if not added and not removed:
            return await ctx.send(f"Rewards check completed for {member.display_name}. No changes were made.")
            
        msg = f"**Rewards check completed for {member.display_name}:**\n"
        if added:
            msg += f"**Roles Added:** {humanize_list([r.name for r in added])}\n"
        if removed:
            msg += f"**Roles Removed:** {humanize_list([r.name for r in removed])}\n"
            
        await ctx.send(msg)

    # --- EXPORT/IMPORT ---
    @rolerewardset.command(name="export")
    async def rrs_export(self, ctx):
        """Export settings and user stats to JSON."""
        data = await self.config.get_raw_guild_data(ctx.guild.id)
        user_data = await self.config.all_users()
        
        export_bundle = {
            "settings": data,
            "users": user_data
        }
        
        file_obj = io.BytesIO(json.dumps(export_bundle, indent=4).encode('utf-8'))
        await ctx.send("Here is the configuration export:", file=discord.File(file_obj, filename="advanced_role_rewards_export.json"))

    @rolerewardset.command(name="import")
    async def rrs_import(self, ctx):
        """Import settings from an attached JSON file."""
        if not ctx.message.attachments:
            return await ctx.send("Please attach a JSON file.")
        
        file = ctx.message.attachments[0]
        content = await file.read()
        
        try:
            data = json.loads(content)
            if "settings" in data:
                await self.config.guild(ctx.guild).set(data["settings"])
            
            if "users" in data:
                for user_id, u_data in data["users"].items():
                    await self.config.user_from_id(int(user_id)).set(u_data)
            
            await ctx.send("Configuration imported successfully.")
        except json.JSONDecodeError:
            await ctx.send("Invalid JSON.")
        except Exception as e:
            await ctx.send(f"Error importing: {e}")

    # --- VIEW SETTINGS ---
    @rolerewardset.command(name="view")
    async def rrs_view(self, ctx):
        """View all current settings."""
        settings = await self.config.guild(ctx.guild).all()
        
        text = "## Advanced Role Rewards Configuration\n\n"
        
        text += "**Level Rewards**\n"
        if settings["level_rewards"]:
            for r in settings["level_rewards"]:
                role = ctx.guild.get_role(r["role_id"])
                text += f"- Level {r['level']} -> {role.name if role else 'Deleted'}\n"
        else:
            text += "- None\n"
            
        text += "\n**Days Rewards**\n"
        if settings["days_rewards"]:
            for r in settings["days_rewards"]:
                role = ctx.guild.get_role(r["role_id"])
                text += f"- {r['days']} Days -> {role.name if role else 'Deleted'}\n"
        else:
            text += "- None\n"
            
        text += "\n**Advanced Rewards**\n"
        if settings["advanced_rewards"]:
            for r in settings["advanced_rewards"]:
                role = ctx.guild.get_role(r["role_id"])
                text += f"- {r['days']} Days + Lv {r['level']} -> {role.name if role else 'Deleted'}\n"
        else:
            text += "- None\n"
            
        text += "\n**Secret Rewards**\n"
        if settings["secret_rewards"]:
            for r in settings["secret_rewards"]:
                role = ctx.guild.get_role(r["role_id"])
                text += f"- {r['days']} Days + Lv {r['level']} -> {role.name if role else 'Deleted'}\n"
        else:
            text += "- None\n"
            
        text += "\n**Opt-in Rewards**\n"
        if settings["optin_rewards"]:
            for r in settings["optin_rewards"]:
                base = ctx.guild.get_role(r["base_role_id"])
                target = ctx.guild.get_role(r["role_id"])
                text += f"- Base: {base.name if base else 'Deleted'} + {r['days']} Days + Lv {r['level']} -> {target.name if target else 'Deleted'}\n"
        else:
            text += "- None\n"
            
        text += "\n**Multistep Chains**\n"
        if settings["multistep_rewards"]:
            for name, steps in settings["multistep_rewards"].items():
                text += f"- {name}: {len(steps)} steps\n"
        else:
            text += "- None\n"

        await self._send_paginated(ctx, text, "Full Configuration")

    async def _send_paginated(self, ctx, text, title):
        pages = list(pagify(text))
        if len(pages) == 1:
            embed = discord.Embed(title=title, description=pages[0], color=discord.Color.green())
            await ctx.send(embed=embed)
        else:
            embeds = []
            for i, page in enumerate(pages):
                e = discord.Embed(title=f"{title} ({i+1}/{len(pages)})", description=page, color=discord.Color.green())
                embeds.append(e)
            await menu(ctx, embeds, DEFAULT_CONTROLS)