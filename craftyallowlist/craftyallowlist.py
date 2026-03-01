import discord
from discord import app_commands
import aiohttp
import logging
import typing
import inspect
from tabulate import tabulate
from redbot.core import Config, commands, checks
from redbot.core.bot import Red

log = logging.getLogger("red.craftyallowlist")

class AllowlistModal(discord.ui.Modal):
    def __init__(self, action: str, cog, guild: discord.Guild):
        super().__init__(title=f"{action.capitalize()} User to Allowlist")
        self.action = action.lower()
        self.cog = cog
        self.guild = guild

        self.username_input = discord.ui.TextInput(
            label="Minecraft Bedrock Username",
            placeholder="e.g., Steve123",
            min_length=3,
            max_length=16,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.username_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        username = self.username_input.value.strip()
        
        command = f"allowlist {self.action} \"{username}\""
        success = await self.cog.send_crafty_command(self.guild, command)
        
        if success:
            # Gamertag hidden for privacy
            await interaction.followup.send(f"✅ Successfully sent command to {self.action} the specified user.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Failed to communicate with Crafty Controller. Check your `[p]craftyallowlistset view` settings.", ephemeral=True)


class AllowlistManageView(discord.ui.View):
    def __init__(self, cog, guild: discord.Guild):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild

    @discord.ui.button(label="Add to Allowlist", style=discord.ButtonStyle.success, custom_id="crafty_add_btn")
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AllowlistModal(action="add", cog=self.cog, guild=self.guild)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Remove from Allowlist", style=discord.ButtonStyle.danger, custom_id="crafty_remove_btn")
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AllowlistModal(action="remove", cog=self.cog, guild=self.guild)
        await interaction.response.send_modal(modal)


class CraftyAllowlist(commands.Cog):
    """Manage a Minecraft Bedrock allowlist via Crafty Controller API."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=948372615243, force_registration=True)
        
        default_guild = {
            "url": None,
            "token": None,
            "server_id": None,
            "req_role": None,
            "req_days": 0,
            "req_level": 0,
            "notify_channel": None,
            "success_channel": None,
            "embed_title": "🎉 Allowlist Updated!",
            "embed_desc": "{member.mention} (`{gamertag}`) has been successfully added to the allowlist.",
            "embed_footer": "Welcome to the server!"
        }
        
        default_user = {
            "bedrock_gamertag": None
        }

        default_member = {
            "notified_eligible": False,
            "added_to_allowlist": False
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)
        self.config.register_member(**default_member)

    async def send_crafty_command(self, guild: discord.Guild, command: str) -> bool:
        """Helper to send stdin commands to Crafty API."""
        settings = await self.config.guild(guild).all()
        url = settings.get("url")
        token = settings.get("token")
        server_id = settings.get("server_id")

        if not all([url, token, server_id]):
            return False

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain" 
        }
        endpoint = f"{url.rstrip('/')}/api/v2/servers/{server_id}/stdin"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, headers=headers, data=command, timeout=10) as response:
                    if response.status in (200, 204):
                        return True
                    else:
                        log.error(f"Crafty API Error: {response.status} - {await response.text()}")
                        return False
        except Exception as e:
            log.exception(f"Exception connecting to Crafty API: {e}")
            return False

    async def send_success_embed(self, member: discord.Member, gamertag: str):
        """Builds and sends the configurable success embed."""
        settings = await self.config.guild(member.guild).all()
        channel_id = settings.get("success_channel")
        if not channel_id:
            return
            
        channel = member.guild.get_channel(channel_id)
        if not channel:
            return

        def format_text(text: str) -> str:
            if not text:
                return ""
            return text.replace("{member.mention}", member.mention)\
                       .replace("{member.display_name}", member.display_name)\
                       .replace("{member.name}", member.name)\
                       .replace("{gamertag}", gamertag)

        title = format_text(settings.get("embed_title", ""))
        desc = format_text(settings.get("embed_desc", ""))
        footer = format_text(settings.get("embed_footer", ""))
        
        if not title and not desc:
            return # Discord requires at least a title or description for an embed

        embed = discord.Embed(color=discord.Color.green())
        if title:
            embed.title = title
        if desc:
            embed.description = desc
        if footer:
            embed.set_footer(text=footer)

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.warning(f"Missing permissions to send success embed in channel {channel.name} (ID: {channel.id}).")

    async def check_eligibility_and_allow(self, member: discord.Member, current_level: typing.Optional[int] = None):
        """Checks if a member meets all requirements and processes them."""
        settings = await self.config.guild(member.guild).all()
        req_role_id = settings.get("req_role")
        req_days = settings.get("req_days")
        req_level = settings.get("req_level")
        notify_channel_id = settings.get("notify_channel")
        
        if not all([req_role_id, req_level]):
            return
            
        role = member.guild.get_role(req_role_id)
        if not role or role not in member.roles:
            return
            
        if member.joined_at is None or (discord.utils.utcnow() - member.joined_at).days < req_days:
            return
            
        if current_level is None:
            levelup_cog = self.bot.get_cog("LevelUp")
            if levelup_cog:
                level_result = levelup_cog.get_level(member)
                if inspect.isawaitable(level_result):
                    current_level = await level_result
                else:
                    current_level = level_result
            else:
                return
                
        if current_level < req_level:
            return

        gamertag = await self.config.user(member).bedrock_gamertag()
        
        if gamertag:
            if not await self.config.member(member).added_to_allowlist():
                success = await self.send_crafty_command(member.guild, f"allowlist add \"{gamertag}\"")
                if success:
                    await self.config.member(member).added_to_allowlist.set(True)
                    await self.send_success_embed(member, gamertag)
        else:
            if notify_channel_id and not await self.config.member(member).notified_eligible():
                channel = member.guild.get_channel(notify_channel_id)
                if channel:
                    await channel.send(
                        f"🎉 Hey {member.mention}, you've reached the required level and time in the server to join our Minecraft Bedrock server!\n"
                        f"To get access, please link your gamertag using the slash command: `/mclink`"
                    )
                    await self.config.member(member).notified_eligible.set(True)

    # --- EVENT LISTENERS ---

    @commands.Cog.listener()
    async def on_member_levelup(self, guild: discord.Guild, member: discord.Member, message: typing.Optional[str], channel: discord.abc.Messageable, new_level: int, *args, **kwargs):
        """Listens to the vrt-cog LevelUp event."""
        await self.check_eligibility_and_allow(member, current_level=new_level)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Catches when a member is manually given the required role."""
        if before.roles != after.roles:
            await self.check_eligibility_and_allow(after)

    # --- ADMIN SETTINGS & COMMANDS ---

    @commands.group(name="craftyallowlistset", aliases=["cas"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def craftyallowlistset(self, ctx: commands.Context):
        """Configuration settings for CraftyAllowlist."""
        pass

    @craftyallowlistset.command(name="url")
    async def set_url(self, ctx: commands.Context, url: str):
        """Set the base URL of your Crafty Controller (e.g. https://crafty.mydomain.com:8443)."""
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        await self.config.guild(ctx.guild).url.set(url)
        await ctx.send(f"✅ Crafty Controller URL set to: `{url}`")

    @craftyallowlistset.command(name="token")
    async def set_token(self, ctx: commands.Context, token: str):
        """Set the API token generated in Crafty Controller."""
        await self.config.guild(ctx.guild).token.set(token)
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            await ctx.message.delete()
        await ctx.send("✅ Crafty Controller API token updated successfully (message deleted for security).")

    @craftyallowlistset.command(name="serverid")
    async def set_serverid(self, ctx: commands.Context, server_id: str):
        """Set the UUID of the Bedrock server in Crafty Controller."""
        await self.config.guild(ctx.guild).server_id.set(server_id)
        await ctx.send(f"✅ Crafty Controller Server ID set to: `{server_id}`")

    @craftyallowlistset.command(name="role")
    async def set_role(self, ctx: commands.Context, role: discord.Role):
        """Set the Discord role required for auto-allowlisting."""
        await self.config.guild(ctx.guild).req_role.set(role.id)
        await ctx.send(f"✅ Required role set to: `{role.name}`")

    @craftyallowlistset.command(name="days")
    async def set_days(self, ctx: commands.Context, days: int):
        """Set the number of days a user must be in the server for auto-allowlisting."""
        if days < 0:
            return await ctx.send("❌ Days cannot be negative.")
        await self.config.guild(ctx.guild).req_days.set(days)
        await ctx.send(f"✅ Required days in server set to: `{days}`")

    @craftyallowlistset.command(name="level")
    async def set_level(self, ctx: commands.Context, level: int):
        """Set the LevelUp level required for auto-allowlisting."""
        if level < 0:
            return await ctx.send("❌ Level cannot be negative.")
        await self.config.guild(ctx.guild).req_level.set(level)
        await ctx.send(f"✅ Required LevelUp level set to: `{level}`")

    @craftyallowlistset.command(name="notifychannel")
    async def set_notifychannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where eligibility notifications are sent."""
        await self.config.guild(ctx.guild).notify_channel.set(channel.id)
        await ctx.send(f"✅ Notification channel set to: {channel.mention}")

    @craftyallowlistset.command(name="successchannel")
    async def set_successchannel(self, ctx: commands.Context, channel: typing.Optional[discord.TextChannel] = None):
        """Set the channel where success embeds are posted. Leave blank to disable."""
        if channel:
            await self.config.guild(ctx.guild).success_channel.set(channel.id)
            await ctx.send(f"✅ Success embed channel set to: {channel.mention}")
        else:
            await self.config.guild(ctx.guild).success_channel.set(None)
            await ctx.send("✅ Success embed channel disabled.")

    @craftyallowlistset.command(name="embedtitle")
    async def set_embedtitle(self, ctx: commands.Context, *, title: str = ""):
        """Set the title of the success embed. 
        Placeholders: `{member.mention}`, `{member.display_name}`, `{member.name}`, `{gamertag}`"""
        await self.config.guild(ctx.guild).embed_title.set(title)
        if title:
            await ctx.send(f"✅ Embed title updated.")
        else:
            await ctx.send("✅ Embed title cleared.")

    @craftyallowlistset.command(name="embeddesc")
    async def set_embeddesc(self, ctx: commands.Context, *, description: str = ""):
        """Set the description of the success embed. 
        Placeholders: `{member.mention}`, `{member.display_name}`, `{member.name}`, `{gamertag}`"""
        await self.config.guild(ctx.guild).embed_desc.set(description)
        if description:
            await ctx.send(f"✅ Embed description updated.")
        else:
            await ctx.send("✅ Embed description cleared.")

    @craftyallowlistset.command(name="embedfooter")
    async def set_embedfooter(self, ctx: commands.Context, *, footer: str = ""):
        """Set the footer of the success embed.
        Placeholders: `{member.mention}`, `{member.display_name}`, `{member.name}`, `{gamertag}`"""
        await self.config.guild(ctx.guild).embed_footer.set(footer)
        if footer:
            await ctx.send(f"✅ Embed footer updated.")
        else:
            await ctx.send("✅ Embed footer cleared.")

    @craftyallowlistset.command(name="view")
    async def view_settings(self, ctx: commands.Context):
        """View the current CraftyAllowlist configurations."""
        settings = await self.config.guild(ctx.guild).all()
        
        url_display = settings["url"] if settings["url"] else "Not Set"
        token_display = "******** (Set)" if settings["token"] else "Not Set"
        server_id_display = settings["server_id"] if settings["server_id"] else "Not Set"
        
        role_obj = ctx.guild.get_role(settings["req_role"]) if settings["req_role"] else None
        req_role_display = role_obj.name if role_obj else "Not Set"
        
        req_days_display = str(settings["req_days"])
        req_level_display = str(settings["req_level"])
        
        notify_channel_obj = ctx.guild.get_channel(settings["notify_channel"]) if settings["notify_channel"] else None
        notify_channel_display = f"#{notify_channel_obj.name}" if notify_channel_obj else "Not Set"

        success_channel_obj = ctx.guild.get_channel(settings["success_channel"]) if settings["success_channel"] else None
        success_channel_display = f"#{success_channel_obj.name}" if success_channel_obj else "Not Set"

        def truncate(text, length=30):
            return (text[:length] + '...') if text and len(text) > length else (text if text else "None")

        table_data = [
            ["API URL", url_display],
            ["API Token", token_display],
            ["Server ID", server_id_display],
            ["Required Role", req_role_display],
            ["Required Days", req_days_display],
            ["Required Level", req_level_display],
            ["Notify Channel", notify_channel_display],
            ["Success Channel", success_channel_display],
            ["Embed Title", truncate(settings["embed_title"])],
            ["Embed Desc", truncate(settings["embed_desc"], 50)],
            ["Embed Footer", truncate(settings["embed_footer"])]
        ]

        table_str = tabulate(table_data, headers=["Configuration", "Value"], tablefmt="fancy_grid")
        await ctx.send(f"### CraftyAllowlist Settings\n```\n{table_str}\n```")

    @commands.command(name="mcinvite")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def mcinvite_manage(self, ctx: commands.Context, member: typing.Optional[discord.Member] = None):
        """Add a user to the Bedrock allowlist directly, or open the form if no user is specified."""
        settings = await self.config.guild(ctx.guild).all()
        if not all([settings["url"], settings["token"], settings["server_id"]]):
            return await ctx.send("⚠️ The Crafty integration is not fully configured. Please use `[p]craftyallowlistset` first.")

        if member is None:
            view = AllowlistManageView(cog=self, guild=ctx.guild)
            return await ctx.send("Use the buttons below to open the manual allowlist management form:", view=view)

        gamertag = await self.config.user(member).bedrock_gamertag()
        
        if not gamertag:
            return await ctx.send(
                f"⚠️ {member.display_name} has not linked a Bedrock Gamertag yet.\n"
                f"Hey {member.mention}, please link your Minecraft Gamertag by using the `/mclink` slash command!"
            )

        success = await self.send_crafty_command(ctx.guild, f"allowlist add \"{gamertag}\"")
        
        if success:
            await self.config.member(member).added_to_allowlist.set(True)
            # Gamertag hidden for privacy
            await ctx.send(f"✅ Successfully added **{member.display_name}** to the Bedrock allowlist!")
            await self.send_success_embed(member, gamertag)
        else:
            await ctx.send("❌ Failed to communicate with Crafty Controller. Check the logs or your API settings.")

    @commands.command(name="mcuninvite", aliases=["unallow"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def mcuninvite_member(self, ctx: commands.Context, member: discord.Member):
        """Remove a Discord user from the Bedrock allowlist."""
        gamertag = await self.config.user(member).bedrock_gamertag()
        
        if not gamertag:
            return await ctx.send(f"⚠️ {member.display_name} does not have a linked Bedrock Gamertag.")
            
        settings = await self.config.guild(ctx.guild).all()
        if not all([settings["url"], settings["token"], settings["server_id"]]):
            return await ctx.send("⚠️ The Crafty integration is not fully configured. Please check `[p]craftyallowlistset view`.")

        success = await self.send_crafty_command(ctx.guild, f"allowlist remove \"{gamertag}\"")
        
        if success:
            await self.config.member(member).added_to_allowlist.set(False)
            # Gamertag hidden for privacy
            await ctx.send(f"✅ Successfully removed **{member.display_name}** from the Bedrock allowlist.")
        else:
            await ctx.send("❌ Failed to communicate with Crafty Controller. Check the logs or your API settings.")

    @commands.command(name="mcrecheck")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def mcrecheck(self, ctx: commands.Context, member: typing.Optional[discord.Member] = None):
        """Force a recheck of allowlist requirements for a specific member or all members."""
        settings = await self.config.guild(ctx.guild).all()
        if not all([settings["url"], settings["token"], settings["server_id"]]):
            return await ctx.send("⚠️ The Crafty integration is not fully configured. Please use `[p]craftyallowlistset` first.")

        if member:
            await self.check_eligibility_and_allow(member)
            await ctx.send(f"✅ Re-evaluation complete for **{member.display_name}**.")
        else:
            msg = await ctx.send("🔄 Re-evaluating all server members. This may take a moment...")
            async with ctx.typing():
                for m in ctx.guild.members:
                    if not m.bot:
                        await self.check_eligibility_and_allow(m)
            await msg.edit(content="✅ Finished re-evaluating all members.")

    # --- USER FACING SLASH COMMANDS ---

    @app_commands.command(name="mchowto", description="Learn how to find your Minecraft Bedrock Gamertag.")
    async def mchowto(self, interaction: discord.Interaction):
        """Provides instructions on finding a Bedrock Gamertag via slash command."""
        embed = discord.Embed(
            title="How to find your Minecraft Bedrock Gamertag",
            description="Minecraft Bedrock Edition uses your Xbox Live Gamertag for server allowlists.",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Method 1: From the Main Menu",
            value="1. Launch Minecraft Bedrock Edition.\n2. Look above your character on the right side of the main menu.\n3. Your Gamertag is the name displayed there.",
            inline=False
        )
        embed.add_field(
            name="Method 2: Using the Xbox App",
            value="1. Open the Xbox app on your PC or mobile device.\n2. Go to your profile.\n3. The name displayed at the top is your Xbox Live Gamertag.",
            inline=False
        )
        embed.add_field(
            name="Linking your Account",
            value="Once you know your Gamertag, link it securely by typing:\n`/mclink gamertag:YourGamertagHere`",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="mclink", description="Securely link your Minecraft Bedrock Gamertag to your Discord account.")
    @app_commands.describe(gamertag="Your exact Xbox Live Gamertag")
    async def mclink(self, interaction: discord.Interaction, gamertag: str):
        """Links the user's gamertag securely via an ephemeral slash command."""
        gamertag = gamertag.strip()
        await self.config.user(interaction.user).bedrock_gamertag.set(gamertag)
        
        # Gamertag hidden from the confirmation message too, though ephemeral adds a layer of security
        await interaction.response.send_message("✅ Your Bedrock Gamertag has been securely linked to your account!", ephemeral=True)
        
        if isinstance(interaction.user, discord.Member):
            await self.check_eligibility_and_allow(interaction.user)