import discord
import aiohttp
import logging
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
        
        # Bedrock's native console command is always 'allowlist'
        command = f"allowlist {self.action} \"{username}\""
        success = await self.cog.send_crafty_command(self.guild, command)
        
        if success:
            await interaction.followup.send(f"✅ Successfully sent command to {self.action} `{username}`.", ephemeral=True)
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
            "server_id": None
        }
        
        default_user = {
            "bedrock_gamertag": None
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_user(**default_user)

    async def send_crafty_command(self, guild: discord.Guild, command: str) -> bool:
        """Helper to send stdin commands to Crafty API."""
        settings = await self.config.guild(guild).all()
        url = settings.get("url")
        token = settings.get("token")
        server_id = settings.get("server_id")

        if not all([url, token, server_id]):
            log.warning(f"Crafty API settings incomplete for guild {guild.id}")
            return False

        headers = {"Authorization": f"Bearer {token}"}
        payload = {"data": command}
        endpoint = f"{url.rstrip('/')}/api/v2/servers/{server_id}/stdin"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, headers=headers, json=payload, timeout=10) as response:
                    if response.status in (200, 204):
                        return True
                    else:
                        log.error(f"Crafty API Error: {response.status} - {await response.text()}")
                        return False
        except Exception as e:
            log.exception(f"Exception connecting to Crafty API: {e}")
            return False

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

    @craftyallowlistset.command(name="view")
    async def view_settings(self, ctx: commands.Context):
        """View the current CraftyAllowlist configurations."""
        settings = await self.config.guild(ctx.guild).all()
        
        url_display = settings["url"] if settings["url"] else "Not Set"
        token_display = "******** (Set)" if settings["token"] else "Not Set"
        server_id_display = settings["server_id"] if settings["server_id"] else "Not Set"

        table_data = [
            ["API URL", url_display],
            ["API Token", token_display],
            ["Server ID", server_id_display]
        ]

        table_str = tabulate(table_data, headers=["Configuration", "Value"], tablefmt="fancy_grid")
        await ctx.send(f"### CraftyAllowlist Settings\n```\n{table_str}\n```")

    @commands.command(name="allow")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def allow_member(self, ctx: commands.Context, member: discord.Member):
        """Add a Discord user to the Bedrock allowlist using their linked Gamertag."""
        gamertag = await self.config.user(member).bedrock_gamertag()
        
        if not gamertag:
            return await ctx.send(f"⚠️ {member.display_name} has not linked a Bedrock Gamertag yet. Tell them to use `[p]bedrock link <gamertag>` first.")
            
        settings = await self.config.guild(ctx.guild).all()
        if not all([settings["url"], settings["token"], settings["server_id"]]):
            return await ctx.send("⚠️ The Crafty integration is not fully configured. Please check `[p]craftyallowlistset view`.")

        success = await self.send_crafty_command(ctx.guild, f"allowlist add \"{gamertag}\"")
        
        if success:
            await ctx.send(f"✅ Successfully added `{gamertag}` ({member.display_name}) to the Bedrock allowlist!")
        else:
            await ctx.send("❌ Failed to communicate with Crafty Controller. Check the logs or your API settings.")

    @commands.command(name="mcinvite")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def mcinvite_manage(self, ctx: commands.Context):
        """Open the interactive form to manually manage the Bedrock allowlist."""
        settings = await self.config.guild(ctx.guild).all()
        if not all([settings["url"], settings["token"], settings["server_id"]]):
            return await ctx.send("⚠️ The Crafty integration is not fully configured. Please use `[p]craftyallowlistset` first.")

        view = AllowlistManageView(cog=self, guild=ctx.guild)
        await ctx.send("Use the buttons below to open the manual allowlist management form:", view=view)

    # --- USER FACING COMMANDS ---

    @commands.group(name="bedrock", invoke_without_command=True)
    async def bedrock(self, ctx: commands.Context):
        """Manage your linked Minecraft Bedrock account."""
        await ctx.send_help(ctx.command)

    @bedrock.command(name="howto")
    async def bedrock_howto(self, ctx: commands.Context):
        """Learn how to find your Bedrock Gamertag."""
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
            value=f"Once you know your Gamertag, link it to the bot by typing:\n`{ctx.prefix}bedrock link YourGamertagHere`",
            inline=False
        )
        await ctx.send(embed=embed)

    @bedrock.command(name="link")
    async def bedrock_link(self, ctx: commands.Context, *, gamertag: str):
        """Link your Minecraft Bedrock Gamertag to your Discord account."""
        gamertag = gamertag.strip()
        
        await self.config.user(ctx.author).bedrock_gamertag.set(gamertag)
        await ctx.send(f"✅ Your Bedrock Gamertag has been set to: `{gamertag}`\n*(Administrators can now add you to the server using your Discord mention!)*")