import logging
import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.NoiselessVolatileLobster.SpammerRole")

class SpammerRole(commands.Cog):
    """Automatically assigns a role to users flagged by Discord as spammers."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8472948291, force_registration=True)
        
        default_guild = {
            "enabled": False,
            "spammer_role_id": None
        }
        self.config.register_guild(**default_guild)

    # -------------------------------------------------------------------------
    # LISTENER LOGIC
    # -------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        log.debug(f"[SpammerRole] Checking newly joined member {member.name} (ID: {member.id}) in guild {member.guild.name}.")
        await self._process_spammer_check(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return

        # Only process if the flag actually changed to True
        if not before.public_flags.spammer and after.public_flags.spammer:
            log.debug(f"[SpammerRole] Spammer flag detected on update for {after.name} (ID: {after.id}) in guild {after.guild.name}.")
            await self._process_spammer_check(after)

    async def _process_spammer_check(self, member: discord.Member):
        """Core logic to check the flag and assign the role if applicable."""
        guild = member.guild
        data = await self.config.guild(guild).all()

        if not data["enabled"]:
            log.debug(f"[SpammerRole] Cog is disabled in guild {guild.name}. Skipping.")
            return

        role_id = data["spammer_role_id"]
        if not role_id:
            log.debug(f"[SpammerRole] No spammer role configured in guild {guild.name}. Skipping.")
            return

        # Check the public API flag for spammer
        if member.public_flags.spammer:
            role = guild.get_role(role_id)
            if role:
                try:
                    # Avoid assigning if they already have it
                    if role not in member.roles:
                        await member.add_roles(role, reason="Auto-assigned: User flagged as spammer by Discord API.")
                        log.debug(f"[SpammerRole] Successfully assigned role '{role.name}' to {member.name}.")
                except discord.Forbidden:
                    log.debug(f"[SpammerRole] Missing permissions to assign role to {member.name} in guild {guild.name}.")
                except discord.HTTPException as e:
                    log.debug(f"[SpammerRole] HTTP Exception while assigning role to {member.name}: {e}")
            else:
                log.debug(f"[SpammerRole] Configured role ID {role_id} not found in guild {guild.name}.")

    # -------------------------------------------------------------------------
    # ADMIN COMMANDS
    # -------------------------------------------------------------------------

    @commands.group(name="spammerroleset", aliases=["sroleset"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def spammerroleset(self, ctx: commands.Context):
        """Configuration commands for the SpammerRole cog."""
        pass

    @spammerroleset.command(name="toggle")
    async def spammerroleset_toggle(self, ctx: commands.Context):
        """Toggle the auto-assignment of the spammer role on or off."""
        current = await self.config.guild(ctx.guild).enabled()
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        
        status = "enabled" if new_state else "disabled"
        log.debug(f"[SpammerRole] Auto-assignment {status} in guild {ctx.guild.name} by {ctx.author.name}.")
        await ctx.send(f"Spammer role auto-assignment is now **{status}**.")

    @spammerroleset.command(name="role")
    async def spammerroleset_role(self, ctx: commands.Context, *, role: discord.Role = None):
        """Set the role to be assigned to likely spammers. Leave blank to clear."""
        if role is None:
            await self.config.guild(ctx.guild).spammer_role_id.set(None)
            log.debug(f"[SpammerRole] Spammer role cleared in guild {ctx.guild.name} by {ctx.author.name}.")
            await ctx.send("The spammer role has been cleared. The cog will no longer assign a role.")
            return

        # Safety check: Ensure the bot can actually manage this role
        if role.position >= ctx.guild.me.top_role.position:
            await ctx.send("⚠️ I cannot assign that role because it is higher than or equal to my top role in the hierarchy.")
            return

        await self.config.guild(ctx.guild).spammer_role_id.set(role.id)
        log.debug(f"[SpammerRole] Spammer role set to '{role.name}' (ID: {role.id}) in guild {ctx.guild.name} by {ctx.author.name}.")
        await ctx.send(f"Users flagged as spammers will now be given the **{role.name}** role.")

    @spammerroleset.command(name="view")
    async def spammerroleset_view(self, ctx: commands.Context):
        """View the current configuration for SpammerRole."""
        data = await self.config.guild(ctx.guild).all()
        
        enabled_status = "Enabled" if data["enabled"] else "Disabled"
        
        role_id = data["spammer_role_id"]
        role_obj = ctx.guild.get_role(role_id) if role_id else None
        role_display = f"@{role_obj.name} ({role_obj.id})" if role_obj else "None Set"

        table = (
            "```\n"
            "+----------------+---------------------------------+\n"
            "| Setting        | Value                           |\n"
            "+----------------+---------------------------------+\n"
            f"| Module Status  | {enabled_status:<31} |\n"
            f"| Spammer Role   | {role_display:<31} |\n"
            "+----------------+---------------------------------+\n"
            "```"
        )

        log.debug(f"[SpammerRole] Config view requested by {ctx.author.name} in guild {ctx.guild.name}.")
        await ctx.send(f"**SpammerRole Configuration for {ctx.guild.name}**\n{table}")