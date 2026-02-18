import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import box
from tabulate import tabulate

class PermissionsCheck(commands.Cog):
    """Check a user's permissions in a specific channel."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98347598234, force_registration=True)
        default_guild = {
            "use_embeds": True
        }
        self.config.register_guild(**default_guild)

    @commands.group(name="permissionscheckset")
    @commands.admin_or_permissions(administrator=True)
    async def permissionscheckset(self, ctx):
        """Configuration settings for PermissionsCheck."""
        pass

    @permissionscheckset.command(name="view")
    async def permissionscheckset_view(self, ctx):
        """View the current configurations for PermissionsCheck."""
        use_embeds = await self.config.guild(ctx.guild).use_embeds()
        
        # Presenting administrative data in a scannable, code-block table
        data = [
            ["Setting", "Current Value"],
            ["Use Embeds", str(use_embeds)]
        ]
        
        table = tabulate(data, headers="firstrow", tablefmt="fancy_grid")
        await ctx.send(f"**PermissionsCheck Settings for {ctx.guild.name}**\n{box(table)}")

    @permissionscheckset.command(name="embeds")
    async def permissionscheckset_embeds(self, ctx, toggle: bool):
        """Toggle whether permission checks are output as Discord Embeds."""
        await self.config.guild(ctx.guild).use_embeds.set(toggle)
        await ctx.send(f"Permission check embed output has been set to: `{toggle}`")

    @commands.command(name="checkperms")
    @commands.admin_or_permissions(administrator=True)
    async def checkperms(self, ctx, channel: discord.abc.GuildChannel, member: discord.Member):
        """
        Check a member's permissions in a specified channel.
        
        **Example:**
        `[p]checkperms #general @User`
        """
        perms = channel.permissions_for(member)
        
        # Mapping to the specific permission flags requested
        can_see = perms.view_channel
        can_post = perms.send_messages
        can_history = perms.read_message_history
        can_manage = perms.manage_channels or perms.manage_roles or perms.manage_permissions
        can_pin = perms.manage_messages  # Manage messages governs message pinning in standard channels

        def format_perm(has_perm: bool) -> str:
            return "✅ Yes" if has_perm else "❌ No"

        use_embeds = await self.config.guild(ctx.guild).use_embeds()

        if use_embeds:
            embed = discord.Embed(
                title=f"Permissions for {member.display_name}",
                description=f"**Channel:** {channel.mention}",
                color=member.color if member.color != discord.Color.default() else discord.Color.blue()
            )
            
            # Grabbing avatar in a dpy 2.x compliant manner
            avatar_url = member.avatar.url if member.avatar else member.display_avatar.url
            embed.set_thumbnail(url=avatar_url)
            
            embed.add_field(name="See Channel", value=format_perm(can_see), inline=False)
            embed.add_field(name="Post Messages", value=format_perm(can_post), inline=False)
            embed.add_field(name="See Message History", value=format_perm(can_history), inline=False)
            embed.add_field(name="Manage Permissions", value=format_perm(can_manage), inline=False)
            embed.add_field(name="Message Pin Permissions", value=format_perm(can_pin), inline=False)
            
            await ctx.send(embed=embed)
        else:
            # Fallback for when embeds are disabled, still utilizing a clean table layout
            data = [
                ["Permission", "Status"],
                ["See Channel", format_perm(can_see)],
                ["Post Messages", format_perm(can_post)],
                ["See Message History", format_perm(can_history)],
                ["Manage Permissions", format_perm(can_manage)],
                ["Message Pin Permissions", format_perm(can_pin)]
            ]
            table = tabulate(data, headers="firstrow", tablefmt="fancy_grid")
            msg = f"**Permissions for {member.display_name} in {channel.mention}**\n{box(table)}"
            await ctx.send(msg)