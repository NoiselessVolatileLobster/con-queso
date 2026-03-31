import logging
import discord
from typing import Union
from tabulate import tabulate

from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box

log = logging.getLogger("red.NoiselessVolatileLobster.watchlist")

class Watchlist(commands.Cog):
    """
    Monitor specific users for actions like nickname changes, invites, deletions, and mentions.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=938472948273, force_registration=True)
        
        default_guild = {
            "channel_id": None,
            "watched_users": []
        }
        self.config.register_guild(**default_guild)
        log.debug("Watchlist cog initialized and Config registered.")

    async def _send_alert(self, guild: discord.Guild, embed: discord.Embed):
        """Helper function to send alerts to the designated channel."""
        log.debug(f"Attempting to send alert in guild: {guild.name} ({guild.id})")
        channel_id = await self.config.guild(guild).channel_id()
        
        if not channel_id:
            log.debug(f"No alert channel configured for guild: {guild.name}")
            return
            
        channel = guild.get_channel(channel_id)
        if not channel:
            log.debug(f"Alert channel configured but not found in guild: {guild.name}")
            return
            
        if not channel.permissions_for(guild.me).send_messages:
            log.debug(f"Missing permissions to send messages in the alert channel for guild: {guild.name}")
            return
            
        if not channel.permissions_for(guild.me).embed_links:
            log.debug(f"Missing permissions to send embeds in the alert channel for guild: {guild.name}")
            return

        try:
            await channel.send(embed=embed)
            log.debug(f"Successfully sent alert in guild: {guild.name}")
        except Exception as e:
            log.debug(f"Error sending alert in guild {guild.name}: {e}")

    # --- ADMIN COMMANDS ---

    @commands.group(name="watchlistset", aliases=["watchlist"])
    @commands.admin_or_permissions(manage_guild=True)
    async def watchlistset(self, ctx: commands.Context):
        """Configuration settings for the Watchlist cog."""
        log.debug(f"watchlistset command invoked by {ctx.author.name} in {ctx.guild.name}")
        pass

    @watchlistset.command(name="channel")
    async def watchlistset_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """
        Set the channel where watchlist notifications will be sent.
        Leave blank to disable notifications.
        """
        log.debug(f"watchlistset channel invoked by {ctx.author.name} with target: {channel}")
        if channel is None:
            await self.config.guild(ctx.guild).channel_id.set(None)
            log.debug(f"Alert channel disabled for {ctx.guild.name}")
            await ctx.send("Watchlist notifications have been disabled.")
            return

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        log.debug(f"Alert channel set to {channel.name} ({channel.id}) for {ctx.guild.name}")
        await ctx.send(f"Watchlist notifications will now be sent to {channel.mention}.")

    @watchlistset.command(name="user")
    async def watchlistset_user(self, ctx: commands.Context, user: discord.Member):
        """
        Toggle a user's presence on the watchlist.
        """
        log.debug(f"watchlistset user invoked by {ctx.author.name} for target: {user.name}")
        async with self.config.guild(ctx.guild).watched_users() as watched:
            if user.id in watched:
                watched.remove(user.id)
                log.debug(f"Removed {user.name} ({user.id}) from the watchlist in {ctx.guild.name}")
                await ctx.send(f"**{user.display_name}** has been removed from the watchlist.")
            else:
                watched.append(user.id)
                log.debug(f"Added {user.name} ({user.id}) to the watchlist in {ctx.guild.name}")
                await ctx.send(f"**{user.display_name}** has been added to the watchlist.")

    @watchlistset.command(name="view")
    async def watchlistset_view(self, ctx: commands.Context):
        """
        View the current configuration and list of watched users.
        """
        log.debug(f"watchlistset view invoked by {ctx.author.name} in {ctx.guild.name}")
        channel_id = await self.config.guild(ctx.guild).channel_id()
        watched_users = await self.config.guild(ctx.guild).watched_users()

        channel_display = f"<#{channel_id}>" if channel_id else "Not Set"
        
        settings_table = tabulate(
            [["Notification Channel", channel_display],
             ["Total Watched Users", len(watched_users)]],
            headers=["Setting", "Value"],
            tablefmt="pretty"
        )

        user_rows = []
        for uid in watched_users:
            member = ctx.guild.get_member(uid)
            name = f"{member.name}" if member else "Unknown/Left"
            user_rows.append([uid, name, "Monitoring"])

        if user_rows:
            users_table = tabulate(user_rows, headers=["User ID", "Name", "Status"], tablefmt="fancy_grid")
        else:
            users_table = "No users are currently on the watchlist."

        message = (
            f"**Watchlist Configuration**\n\n"
            f"Notification Channel: {channel_display}\n\n"
            f"**Watched Users:**\n"
            f"{box(users_table, lang='prolog')}"
        )
        log.debug(f"Displaying view command data for {ctx.guild.name}")
        await ctx.send(message)

    # --- LISTENERS ---

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Listener for Nickname changes."""
        if before.nick == after.nick:
            return

        watched = await self.config.guild(after.guild).watched_users()
        if after.id not in watched:
            return

        log.debug(f"Watched user nickname update triggered for {after.name} in {after.guild.name}")
        
        embed = discord.Embed(
            title="⚠️ Watchlist Alert: Nickname Changed",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=f"{after.name} ({after.id})", icon_url=after.display_avatar.url)
        embed.add_field(name="Old Nickname", value=before.nick or "[No Nickname]", inline=False)
        embed.add_field(name="New Nickname", value=after.nick or "[No Nickname]", inline=False)
        
        await self._send_alert(after.guild, embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        """Listener for Invite creation."""
        if invite.guild is None or invite.inviter is None:
            return
            
        watched = await self.config.guild(invite.guild).watched_users()
        if invite.inviter.id not in watched:
            return

        log.debug(f"Watched user invite creation triggered for {invite.inviter.name} in {invite.guild.name}")

        embed = discord.Embed(
            title="⚠️ Watchlist Alert: Invite Created",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=f"{invite.inviter.name} ({invite.inviter.id})", icon_url=invite.inviter.display_avatar.url)
        embed.add_field(name="Invite Code", value=invite.code, inline=True)
        embed.add_field(name="Channel", value=invite.channel.mention if invite.channel else "Unknown", inline=True)
        embed.add_field(name="Max Uses", value=str(invite.max_uses) if invite.max_uses > 0 else "Unlimited", inline=True)
        
        await self._send_alert(invite.guild, embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Listener for Message deletions."""
        if message.guild is None or message.author.bot:
            return
            
        watched = await self.config.guild(message.guild).watched_users()
        if message.author.id not in watched:
            return

        log.debug(f"Watched user message deletion triggered for {message.author.name} in {message.guild.name}")

        embed = discord.Embed(
            title="⚠️ Watchlist Alert: Message Deleted",
            description=message.content if message.content else "*[No text content or embed/image only]*",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=f"{message.author.name} ({message.author.id})", icon_url=message.author.display_avatar.url)
        embed.add_field(name="Channel", value=message.channel.mention, inline=False)
        
        await self._send_alert(message.guild, embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listener for user mentions."""
        if message.guild is None or message.author.bot:
            return
            
        if not message.mentions:
            return
            
        watched = await self.config.guild(message.guild).watched_users()
        if message.author.id not in watched:
            return

        # Exclude bot mentions and self-mentions to prevent spam/false flags
        real_mentions = [m for m in message.mentions if not m.bot and m.id != message.author.id]
        if not real_mentions:
            return

        log.debug(f"Watched user mention triggered by {message.author.name} in {message.guild.name}")

        mentioned_users_str = ", ".join([m.mention for m in real_mentions])
        
        embed = discord.Embed(
            title="⚠️ Watchlist Alert: Mentioned Someone",
            description=message.content,
            color=discord.Color.purple(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=f"{message.author.name} ({message.author.id})", icon_url=message.author.display_avatar.url)
        embed.add_field(name="Mentioned", value=mentioned_users_str, inline=False)
        embed.add_field(name="Channel", value=message.channel.mention, inline=False)
        embed.add_field(name="Message Link", value=f"[Jump to Message]({message.jump_url})", inline=False)
        
        await self._send_alert(message.guild, embed)