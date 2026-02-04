import discord
import re
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify

class GifOnly(commands.Cog):
    """
    Enforce GIF-only conversation in specific channels.
    Supports uploaded files (Gboard, etc.) and common GIF links.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=981237645, force_registration=True)
        
        # Default configuration
        default_guild = {
            "channels": [],      # List of channel IDs
            "log_channel": None, # Channel ID for logging deletions
            "ignored_roles": []  # IDs of roles that bypass checks
        }
        self.config.register_guild(**default_guild)

        # Regex to find URLs in messages
        self.url_regex = re.compile(r'(https?://\S+)')
        
        # Common GIF providers that might not end in .gif
        self.gif_domains = [
            "tenor.com",
            "giphy.com",
            "imgur.com",
            "gfycat.com",
            "cdn.discordapp.com",
            "media.discordapp.net",
            "klipy.com",
            "pinterest.com",
            "reddit.com"
        ]

    async def is_gif(self, message: discord.Message) -> bool:
        """
        Logic to determine if a message contains a GIF.
        Checks attachments and URL patterns.
        """
        # 1. Check Attachments (Handles Gboard direct uploads, Discord uploads)
        if message.attachments:
            for attachment in message.attachments:
                filename = attachment.filename.lower()
                content_type = getattr(attachment, "content_type", "") or ""

                if filename.endswith('.gif'):
                    return True
                if content_type == "image/gif":
                    return True
                # Some mobile keyboards upload as .mp4 (video) instead of gif
                if filename.endswith('.mp4') or content_type == "video/mp4":
                    return True

        # 2. Check Content for Links
        content = message.content.lower()
        urls = self.url_regex.findall(content)

        for url in urls:
            # Check if link ends in .gif (most direct links)
            if url.endswith('.gif') or url.endswith('.gifv'):
                return True
            
            # Check if link is from a known GIF provider
            if any(domain in url for domain in self.gif_domains):
                return True

        return False

    async def log_deletion(self, message: discord.Message, guild_config):
        """
        Logs the deleted message to the configured log channel.
        """
        log_channel_id = guild_config["log_channel"]
        if not log_channel_id:
            return

        log_channel = message.guild.get_channel(log_channel_id)
        if not log_channel:
            return

        embed = discord.Embed(
            title="Non-GIF Message Deleted",
            description=f"**Author:** {message.author.mention} ({message.author.id})\n**Channel:** {message.channel.mention}",
            color=discord.Color.red()
        )
        
        if message.content:
            # Truncate content if too long
            content = (message.content[:1000] + '..') if len(message.content) > 1000 else message.content
            embed.add_field(name="Content", value=content, inline=False)
        
        if message.attachments:
            att_names = [a.filename for a in message.attachments]
            embed.add_field(name="Attachments", value=", ".join(att_names), inline=False)

        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            pass # Bot doesn't have permission to send in log channel

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots, DMs, and empty messages (system messages)
        if message.author.bot or not message.guild:
            return
        
        # Ignore messages with no content and no attachments (like system pins)
        if not message.content and not message.attachments:
            return

        # Fetch settings
        settings = await self.config.guild(message.guild).all()
        
        # Check if current channel is monitored
        if message.channel.id not in settings["channels"]:
            return

        # Check ignored roles
        if settings["ignored_roles"]:
            user_role_ids = [r.id for r in message.author.roles]
            if any(r_id in user_role_ids for r_id in settings["ignored_roles"]):
                return

        # Run GIF detection
        is_valid_gif = await self.is_gif(message)

        if not is_valid_gif:
            try:
                await message.delete()
                await self.log_deletion(message, settings)
                
                msg = await message.channel.send(f"{message.author.mention}, only GIFs are allowed in this channel!", delete_after=5)
            except discord.Forbidden:
                pass # Missing Manage Messages permission
            except discord.NotFound:
                pass # Message already deleted

    # --- Admin Commands ---

    @commands.group(name="gifonlyset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_channels=True)
    async def gifonlyset(self, ctx):
        """Manage GIF-only channel settings."""
        pass

    @gifonlyset.command(name="view")
    async def gifonly_view(self, ctx):
        """View current GIF-only configuration."""
        settings = await self.config.guild(ctx.guild).all()
        
        channels_list = []
        for c_id in settings["channels"]:
            channel = ctx.guild.get_channel(c_id)
            channels_list.append(channel.mention if channel else f"<Deleted: {c_id}>")
            
        log_channel = ctx.guild.get_channel(settings["log_channel"]) if settings["log_channel"] else "None"
        if hasattr(log_channel, "mention"):
            log_channel = log_channel.mention

        ignored_roles_list = []
        for r_id in settings["ignored_roles"]:
            role = ctx.guild.get_role(r_id)
            ignored_roles_list.append(role.name if role else f"Deleted Role ({r_id})")

        embed = discord.Embed(title="GIF-Only Settings", color=discord.Color.blue())
        embed.add_field(name="Log Channel", value=str(log_channel), inline=False)
        
        channels_str = "\n".join(channels_list) if channels_list else "None"
        embed.add_field(name="Monitored Channels", value=channels_str, inline=False)
        
        roles_str = ", ".join(ignored_roles_list) if ignored_roles_list else "None"
        embed.add_field(name="Ignored Roles", value=roles_str, inline=False)
        
        await ctx.send(embed=embed)

    @gifonlyset.command(name="add")
    async def gif_add(self, ctx, channel: discord.TextChannel):
        """Add a channel to the GIF-only enforcement list."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id in channels:
                await ctx.send(f"{channel.mention} is already a GIF-only channel.")
            else:
                channels.append(channel.id)
                await ctx.send(f"{channel.mention} is now a GIF-only channel.")

    @gifonlyset.command(name="remove")
    async def gif_remove(self, ctx, channel: discord.TextChannel):
        """Remove a channel from the GIF-only enforcement list."""
        async with self.config.guild(ctx.guild).channels() as channels:
            if channel.id not in channels:
                await ctx.send(f"{channel.mention} is not in the GIF-only list.")
            else:
                channels.remove(channel.id)
                await ctx.send(f"{channel.mention} is no longer a GIF-only channel.")

    @gifonlyset.command(name="list")
    async def gif_list(self, ctx):
        """List all active GIF-only channels in a table."""
        channel_ids = await self.config.guild(ctx.guild).channels()
        
        if not channel_ids:
            await ctx.send("There are no GIF-only channels set.")
            return

        data = []
        for c_id in channel_ids:
            channel = ctx.guild.get_channel(c_id)
            if channel:
                data.append([channel.id, channel.name])
            else:
                data.append([c_id, "Unknown/Deleted"])

        table = box(
            f"{'ID':<20} | {'Name'}\n" + 
            "-"*40 + "\n" + 
            "\n".join([f"{row[0]:<20} | {row[1]}" for row in data]),
            lang="prolog"
        )
        
        await ctx.send(f"**GIF-Only Channels**\n{table}")

    @gifonlyset.command(name="logchannel")
    async def gif_logchannel(self, ctx, channel: discord.TextChannel = None):
        """
        Set the channel where deleted messages are logged. 
        Leave blank to disable logging.
        """
        if channel:
            await self.config.guild(ctx.guild).log_channel.set(channel.id)
            await ctx.send(f"Deleted messages will now be logged to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel.set(None)
            await ctx.send("Logging disabled.")

    @gifonlyset.command(name="ignore")
    async def gif_ignore(self, ctx, role: discord.Role):
        """Toggle a role to be ignored by the GIF check."""
        async with self.config.guild(ctx.guild).ignored_roles() as roles:
            if role.id in roles:
                roles.remove(role.id)
                await ctx.send(f"Role **{role.name}** is no longer ignored.")
            else:
                roles.append(role.id)
                await ctx.send(f"Role **{role.name}** is now ignored. Members with this role can send non-GIFs.")