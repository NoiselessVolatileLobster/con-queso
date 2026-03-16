import logging
import discord
from discord.ui import Modal, TextInput, View, ChannelSelect, RoleSelect, Button
from tabulate import tabulate

from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify, box

log = logging.getLogger("red.NoiselessVolatileLobster.vctext")

# --- UI MODALS ---

class VCPingSetupModal(Modal, title="Set First-Join Ping Message"):
    message_input = TextInput(
        label="Ping Message",
        style=discord.TextStyle.paragraph,
        placeholder="Available vars: {user}, {user.mention}, {vc}",
        required=True,
        max_length=1000
    )

    def __init__(self, view_instance):
        super().__init__()
        self.view_instance = view_instance

    async def on_submit(self, interaction: discord.Interaction):
        log.debug(f"[VCText] Ping Modal submitted by {interaction.user} with message: {self.message_input.value}")
        self.view_instance.custom_message = self.message_input.value
        self.view_instance.check_complete()
        await interaction.response.edit_message(content=self.view_instance.get_status_text(), view=self.view_instance)

class VCRoleSetupModal(Modal, title="Set Auto-Role Mention Message"):
    message_input = TextInput(
        label="Mention Message",
        style=discord.TextStyle.paragraph,
        placeholder="Vars: {user}, {user.mention}, {role}, {vc}",
        default="Welcome {user.mention}! You've been granted the **{role}** role for joining {vc}.",
        required=True,
        max_length=1000
    )

    def __init__(self, view_instance):
        super().__init__()
        self.view_instance = view_instance

    async def on_submit(self, interaction: discord.Interaction):
        log.debug(f"[VCText] Role Modal submitted by {interaction.user} with message: {self.message_input.value}")
        self.view_instance.custom_message = self.message_input.value
        self.view_instance.check_complete()
        await interaction.response.edit_message(content=self.view_instance.get_status_text(), view=self.view_instance)


# --- UI VIEWS ---

class VCPingSetupView(View):
    def __init__(self, cog, ctx, parent_view):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.parent_view = parent_view
        self.selected_vc = None
        self.selected_tc = None
        self.selected_role = None
        self.custom_message = None

    def get_status_text(self):
        status = "**🔔 Configure First-Join Ping**\n"
        status += "Set what happens when the *first* person joins an empty Voice Channel.\n\n"
        status += f"**Voice Channel**: {self.selected_vc.mention if self.selected_vc else '❌ Not set'}\n"
        status += f"**Text Channel**: {self.selected_tc.mention if self.selected_tc else '❌ Not set'}\n"
        status += f"**Role to Ping**: {self.selected_role.mention if self.selected_role else '❌ Not set'}\n"
        status += f"**Message**: {f'`{self.custom_message}`' if self.custom_message else '❌ Not set'}\n"
        return status

    def check_complete(self):
        if self.selected_vc and self.selected_tc and self.selected_role and self.custom_message:
            self.save_btn.disabled = False
            self.save_btn.style = discord.ButtonStyle.success

    @discord.ui.select(cls=ChannelSelect, channel_types=[discord.ChannelType.voice], placeholder="1. Select Voice Channel", row=0)
    async def select_vc(self, interaction: discord.Interaction, select: ChannelSelect):
        self.selected_vc = select.values[0]
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.select(cls=ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="2. Select Text Channel for Ping", row=1)
    async def select_tc(self, interaction: discord.Interaction, select: ChannelSelect):
        self.selected_tc = select.values[0]
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.select(cls=RoleSelect, placeholder="3. Select Role to Ping", row=2)
    async def select_role(self, interaction: discord.Interaction, select: RoleSelect):
        self.selected_role = select.values[0]
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.button(label="4. Set Message", style=discord.ButtonStyle.primary, row=3)
    async def set_msg_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(VCPingSetupModal(self))

    @discord.ui.button(label="5. Save Config", style=discord.ButtonStyle.secondary, disabled=True, row=3)
    async def save_btn(self, interaction: discord.Interaction, button: Button):
        async with self.cog.config.guild(self.ctx.guild).channels() as channels:
            vc_id = str(self.selected_vc.id)
            if vc_id not in channels: channels[vc_id] = {}
            channels[vc_id]["ping"] = {
                "tc_id": self.selected_tc.id,
                "role_id": self.selected_role.id,
                "msg": self.custom_message
            }
        log.debug(f"[VCText] Ping Config saved for VC {self.selected_vc.name} in {self.ctx.guild.name}.")
        await interaction.response.edit_message(content=f"✅ **Ping Configuration Saved for {self.selected_vc.mention}!**", view=None)
        self.stop()

    @discord.ui.button(label="Back", style=discord.ButtonStyle.danger, row=3)
    async def back_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content=self.parent_view.get_status_text(), view=self.parent_view)

class VCRoleSetupView(View):
    def __init__(self, cog, ctx, parent_view):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.parent_view = parent_view
        self.selected_vc = None
        self.selected_tc = None
        self.selected_role = None
        self.custom_message = None

    def get_status_text(self):
        status = "**🎭 Configure Auto-Role & Mention**\n"
        status += "Set what role is given to *anyone* joining the Voice Channel, and where to mention them.\n\n"
        status += f"**Voice Channel**: {self.selected_vc.mention if self.selected_vc else '❌ Not set'}\n"
        status += f"**Role to Assign**: {self.selected_role.mention if self.selected_role else '❌ Not set'}\n"
        status += f"**Text Channel (for Mention)**: {self.selected_tc.mention if self.selected_tc else '❌ Not set'}\n"
        status += f"**Message**: {f'`{self.custom_message}`' if self.custom_message else '❌ Not set'}\n"
        return status

    def check_complete(self):
        if self.selected_vc and self.selected_tc and self.selected_role and self.custom_message:
            self.save_btn.disabled = False
            self.save_btn.style = discord.ButtonStyle.success

    @discord.ui.select(cls=ChannelSelect, channel_types=[discord.ChannelType.voice], placeholder="1. Select Voice Channel", row=0)
    async def select_vc(self, interaction: discord.Interaction, select: ChannelSelect):
        self.selected_vc = select.values[0]
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.select(cls=RoleSelect, placeholder="2. Select Role to Assign", row=1)
    async def select_role(self, interaction: discord.Interaction, select: RoleSelect):
        self.selected_role = select.values[0]
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.select(cls=ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="3. Select Text Channel for Mention", row=2)
    async def select_tc(self, interaction: discord.Interaction, select: ChannelSelect):
        self.selected_tc = select.values[0]
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.button(label="4. Set Message", style=discord.ButtonStyle.primary, row=3)
    async def set_msg_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(VCRoleSetupModal(self))

    @discord.ui.button(label="5. Save Config", style=discord.ButtonStyle.secondary, disabled=True, row=3)
    async def save_btn(self, interaction: discord.Interaction, button: Button):
        async with self.cog.config.guild(self.ctx.guild).channels() as channels:
            vc_id = str(self.selected_vc.id)
            if vc_id not in channels: channels[vc_id] = {}
            channels[vc_id]["role"] = {
                "tc_id": self.selected_tc.id,
                "role_id": self.selected_role.id,
                "msg": self.custom_message
            }
        log.debug(f"[VCText] Role Config saved for VC {self.selected_vc.name} in {self.ctx.guild.name}.")
        await interaction.response.edit_message(content=f"✅ **Role Configuration Saved for {self.selected_vc.mention}!**", view=None)
        self.stop()

    @discord.ui.button(label="Back", style=discord.ButtonStyle.danger, row=3)
    async def back_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content=self.parent_view.get_status_text(), view=self.parent_view)

class VCRemoveView(View):
    def __init__(self, cog, ctx, parent_view):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.parent_view = parent_view
        self.selected_vc = None

    def get_status_text(self):
        return "**❌ Remove Configurations**\nSelect a Voice Channel to clear its tracking logic."

    @discord.ui.select(cls=ChannelSelect, channel_types=[discord.ChannelType.voice], placeholder="Select Voice Channel", row=0)
    async def select_vc(self, interaction: discord.Interaction, select: ChannelSelect):
        self.selected_vc = select.values[0]
        self.rem_ping_btn.disabled = False
        self.rem_role_btn.disabled = False
        self.rem_all_btn.disabled = False
        await interaction.response.edit_message(content=f"Selected **{self.selected_vc.mention}**. What would you like to remove?", view=self)

    @discord.ui.button(label="Remove Ping Logic", style=discord.ButtonStyle.secondary, disabled=True, row=1)
    async def rem_ping_btn(self, interaction: discord.Interaction, button: Button):
        async with self.cog.config.guild(self.ctx.guild).channels() as channels:
            vc_id = str(self.selected_vc.id)
            if vc_id in channels and "ping" in channels[vc_id]:
                del channels[vc_id]["ping"]
                await interaction.response.edit_message(content=f"✅ Removed Ping logic from {self.selected_vc.mention}.", view=None)
            else:
                await interaction.response.edit_message(content=f"⚠️ No Ping logic found for {self.selected_vc.mention}.", view=None)
        self.stop()

    @discord.ui.button(label="Remove Auto-Role Logic", style=discord.ButtonStyle.secondary, disabled=True, row=1)
    async def rem_role_btn(self, interaction: discord.Interaction, button: Button):
        async with self.cog.config.guild(self.ctx.guild).channels() as channels:
            vc_id = str(self.selected_vc.id)
            if vc_id in channels and "role" in channels[vc_id]:
                del channels[vc_id]["role"]
                await interaction.response.edit_message(content=f"✅ Removed Auto-Role logic from {self.selected_vc.mention}.", view=None)
            else:
                await interaction.response.edit_message(content=f"⚠️ No Auto-Role logic found for {self.selected_vc.mention}.", view=None)
        self.stop()

    @discord.ui.button(label="Remove EVERYTHING", style=discord.ButtonStyle.danger, disabled=True, row=1)
    async def rem_all_btn(self, interaction: discord.Interaction, button: Button):
        async with self.cog.config.guild(self.ctx.guild).channels() as channels:
            vc_id = str(self.selected_vc.id)
            if vc_id in channels:
                del channels[vc_id]
                await interaction.response.edit_message(content=f"✅ Removed ALL logic from {self.selected_vc.mention}.", view=None)
            else:
                await interaction.response.edit_message(content=f"⚠️ No logic found for {self.selected_vc.mention}.", view=None)
        self.stop()

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content=self.parent_view.get_status_text(), view=self.parent_view)

class VCDashboardView(View):
    def __init__(self, cog, ctx):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx

    def get_status_text(self):
        return (
            "**🛠️ VCText Unified Dashboard**\n"
            "Welcome to the interactive configuration dashboard.\n"
            "Please select an option below to manage your Voice Channel logic."
        )

    @discord.ui.button(label="🔔 Setup First-Join Ping", style=discord.ButtonStyle.primary, row=0)
    async def setup_ping(self, interaction: discord.Interaction, button: Button):
        view = VCPingSetupView(self.cog, self.ctx, self)
        await interaction.response.edit_message(content=view.get_status_text(), view=view)

    @discord.ui.button(label="🎭 Setup Auto-Role & Mention", style=discord.ButtonStyle.success, row=0)
    async def setup_role(self, interaction: discord.Interaction, button: Button):
        view = VCRoleSetupView(self.cog, self.ctx, self)
        await interaction.response.edit_message(content=view.get_status_text(), view=view)

    @discord.ui.button(label="❌ Remove a Config", style=discord.ButtonStyle.danger, row=1)
    async def remove_config(self, interaction: discord.Interaction, button: Button):
        view = VCRemoveView(self.cog, self.ctx, self)
        await interaction.response.edit_message(content=view.get_status_text(), view=view)


# --- MAIN COG ---

class VCText(commands.Cog):
    """
    Unified manager for Voice Channel pings and auto-role assignments.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=83726194723, force_registration=True)
        
        default_guild = {
            "channels": {}  
            # "vc_id": { 
            #    "ping": { "tc_id": int, "role_id": int, "msg": str },
            #    "role": { "tc_id": int, "role_id": int, "msg": str }
            # }
        }
        self.config.register_guild(**default_guild)
        
        # Initiate migration process in background task
        self.bot.loop.create_task(self.migrate_legacy_data())
        log.debug("[VCText] Cog initialized.")

    async def migrate_legacy_data(self):
        """Migrate from old `mappings` and `role_mappings` to unified `channels` schema."""
        await self.bot.wait_until_red_ready()
        all_guilds = await self.config.all_guilds()
        for guild_id, data in all_guilds.items():
            channels = data.get("channels", {})
            changed = False
            
            legacy_mappings = data.get("mappings", {})
            if legacy_mappings:
                log.debug(f"[VCText] Migrating legacy ping mappings for guild {guild_id}")
                for vc_id, conf in legacy_mappings.items():
                    if vc_id not in channels: channels[vc_id] = {}
                    channels[vc_id]["ping"] = {
                        "tc_id": conf["text_channel_id"],
                        "role_id": conf["role_id"],
                        "msg": conf.get("message", "A tracked voice channel is now active!")
                    }
                await self.config.guild_from_id(guild_id).clear_raw("mappings")
                changed = True
                
            legacy_roles = data.get("role_mappings", {})
            if legacy_roles:
                log.debug(f"[VCText] Migrating legacy role mappings for guild {guild_id}")
                for vc_id, role_id in legacy_roles.items():
                    if vc_id not in channels: channels[vc_id] = {}
                    # Legacy didn't have a TC/Msg for the role mention, so we omit TC/Msg data, 
                    # ensuring the logic in on_voice_state_update handles missing TC smoothly.
                    channels[vc_id]["role"] = {
                        "tc_id": None,
                        "role_id": role_id,
                        "msg": None
                    }
                await self.config.guild_from_id(guild_id).clear_raw("role_mappings")
                changed = True
                
            if changed:
                await self.config.guild_from_id(guild_id).channels.set(channels)
                log.debug(f"[VCText] Migration complete for guild {guild_id}.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
            
        if before.channel == after.channel:
            return
            
        guild = member.guild
        channels_conf = await self.config.guild(guild).channels()

        # --- ROLE REMOVAL LOGIC (Leaving a VC) ---
        if before.channel:
            before_vc_id = str(before.channel.id)
            if before_vc_id in channels_conf and "role" in channels_conf[before_vc_id]:
                role_conf = channels_conf[before_vc_id]["role"]
                role = guild.get_role(role_conf["role_id"])
                
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason=f"Left voice channel {before.channel.name}")
                        log.debug(f"[VCText] Removed role {role.name} from {member} (Left VC).")
                    except discord.Forbidden:
                        log.debug(f"[VCText] Permissions error removing role {role.name} from {member}.")
                    except Exception as e:
                        log.debug(f"[VCText] Unexpected error removing role: {e}")

        # --- JOIN LOGIC (Auto-Role + Mentions + Pings) ---
        if after.channel:
            after_vc_id = str(after.channel.id)
            if after_vc_id in channels_conf:
                conf = channels_conf[after_vc_id]

                # 1. AUTO-ROLE & MENTION
                if "role" in conf:
                    role_conf = conf["role"]
                    role = guild.get_role(role_conf["role_id"])
                    
                    if role and role not in member.roles:
                        try:
                            await member.add_roles(role, reason=f"Joined voice channel {after.channel.name}")
                            log.debug(f"[VCText] Added role {role.name} to {member} (Joined VC).")
                            
                            # Mention the user
                            if role_conf.get("tc_id"):
                                tc = guild.get_channel(role_conf["tc_id"])
                                if tc:
                                    raw_msg = role_conf.get("msg", "Welcome {user.mention}! You've been granted the **{role}** role for joining {vc}.")
                                    formatted_msg = raw_msg.replace("{user}", member.display_name)\
                                                           .replace("{user.mention}", member.mention)\
                                                           .replace("{role}", role.name)\
                                                           .replace("{vc}", after.channel.name)
                                    await tc.send(formatted_msg)
                        except discord.Forbidden:
                            log.debug(f"[VCText] Permissions error adding role {role.name} to {member}.")
                        except Exception as e:
                            log.debug(f"[VCText] Unexpected error adding role: {e}")

                # 2. FIRST-JOIN PING
                if "ping" in conf and len(after.channel.members) == 1:
                    log.debug(f"[VCText] User {member} is FIRST to join {after.channel.name}. Initiating ping sequence.")
                    ping_conf = conf["ping"]
                    tc = guild.get_channel(ping_conf["tc_id"])
                    role = guild.get_role(ping_conf["role_id"])
                    
                    if tc and role:
                        raw_msg = ping_conf.get("msg", "A tracked voice channel is now active!")
                        formatted_msg = raw_msg.replace("{user}", member.display_name)\
                                               .replace("{user.mention}", member.mention)\
                                               .replace("{vc}", after.channel.name)
                        try:
                            await tc.send(f"{role.mention}\n{formatted_msg}", allowed_mentions=discord.AllowedMentions(roles=[role]))
                            log.debug(f"[VCText] Ping successfully sent to {tc.name} for {after.channel.name}.")
                        except discord.Forbidden:
                            log.debug(f"[VCText] Permissions error sending ping in {tc.name}.")
                    else:
                        log.debug(f"[VCText] Ping failed: Missing TC ({ping_conf.get('tc_id')}) or Role ({ping_conf.get('role_id')}).")


    @commands.group(name="vctextset", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def vctextset(self, ctx: commands.Context):
        """Admin commands to manage Unified VC Text Logic."""
        await ctx.send_help()

    @vctextset.command(name="dashboard", aliases=["form", "setup"])
    async def vctextset_dashboard(self, ctx: commands.Context):
        """
        Open the interactive VC Text Configuration Dashboard.
        """
        log.debug(f"[VCText] Dashboard invoked by {ctx.author} in {ctx.guild.name}.")
        view = VCDashboardView(self, ctx)
        await ctx.send(view.get_status_text(), view=view)

    @vctextset.command(name="view", aliases=["list"])
    async def vctextset_view(self, ctx: commands.Context):
        """View a table of all configured VC Text logic for this server."""
        log.debug(f"[VCText] View command invoked by {ctx.author} in {ctx.guild.name}.")
        
        channels = await self.config.guild(ctx.guild).channels()
        if not channels:
            return await ctx.send("No VC Text logic is currently configured for this server.")

        table_data = []
        for vc_id, conf in channels.items():
            vc = ctx.guild.get_channel(int(vc_id))
            vc_name = vc.name if vc else f"Deleted VC ({vc_id})"

            # Format Ping String
            ping_str = "❌ Not Configured"
            if "ping" in conf:
                p_conf = conf["ping"]
                tc = ctx.guild.get_channel(p_conf.get("tc_id", 0))
                role = ctx.guild.get_role(p_conf.get("role_id", 0))
                msg = p_conf.get("msg", "None")[:15] + "..." if len(p_conf.get("msg", "")) > 15 else p_conf.get("msg", "None")
                ping_str = f"TC: {tc.name if tc else 'Del'}\nRole: {role.name if role else 'Del'}\nMsg: {msg}"

            # Format Role String
            role_str = "❌ Not Configured"
            if "role" in conf:
                r_conf = conf["role"]
                tc = ctx.guild.get_channel(r_conf.get("tc_id", 0))
                role = ctx.guild.get_role(r_conf.get("role_id", 0))
                role_str = f"Role: {role.name if role else 'Del'}\nMention TC: {tc.name if tc else 'None'}"

            table_data.append([vc_name, ping_str, role_str])

        rendered_table = tabulate(table_data, headers=["Voice Channel", "First-Join Ping Logic", "Auto-Role Logic"], tablefmt="grid")
        
        for page in pagify(rendered_table, page_length=1980):
            await ctx.send(box(page, lang="none"))