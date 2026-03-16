import logging
import discord
from discord.ui import Modal, TextInput, View, ChannelSelect, RoleSelect, Button
from tabulate import tabulate

from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify, box

log = logging.getLogger("red.NoiselessVolatileLobster.vctext")

class VCTextSetupModal(Modal, title="Set VC Ping Message"):
    """Modal for capturing the message in the interactive setup form."""
    message_input = TextInput(
        label="Ping Message",
        style=discord.TextStyle.paragraph,
        placeholder="Available variables: {user}, {user.mention}, {vc}",
        required=True,
        max_length=1000
    )

    def __init__(self, view_instance):
        super().__init__()
        self.view_instance = view_instance

    async def on_submit(self, interaction: discord.Interaction):
        log.debug(f"[VCText] Form Modal submitted by {interaction.user} with message: {self.message_input.value}")
        self.view_instance.custom_message = self.message_input.value
        self.view_instance.check_complete()
        await interaction.response.edit_message(content=self.view_instance.get_status_text(), view=self.view_instance)

class VCTextSetupView(View):
    """Component v2 View for adding a new mapping interactively."""
    def __init__(self, cog, ctx):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.selected_vc = None
        self.selected_tc = None
        self.selected_role = None
        self.custom_message = None

    def get_status_text(self):
        status = "**Interactive VC Text Configuration Form**\n"
        status += "Please use the dropdowns below to select your components. Once everything is set, save your configuration.\n\n"
        status += f"**Voice Channel**: {self.selected_vc.mention if self.selected_vc else '❌ Not set'}\n"
        status += f"**Text Channel**: {self.selected_tc.mention if self.selected_tc else '❌ Not set'}\n"
        status += f"**Role to Ping**: {self.selected_role.mention if self.selected_role else '❌ Not set'}\n"
        status += f"**Message**: {f'`{self.custom_message}`' if self.custom_message else '❌ Not set'}\n"
        return status

    def check_complete(self):
        """Enable the save button only if all components are provided."""
        if self.selected_vc and self.selected_tc and self.selected_role and self.custom_message:
            self.save_btn.disabled = False
            self.save_btn.style = discord.ButtonStyle.success
        else:
            self.save_btn.disabled = True
            self.save_btn.style = discord.ButtonStyle.secondary

    @discord.ui.select(cls=ChannelSelect, channel_types=[discord.ChannelType.voice], placeholder="1. Select Voice Channel", min_values=1, max_values=1, row=0)
    async def select_vc(self, interaction: discord.Interaction, select: ChannelSelect):
        self.selected_vc = select.values[0]
        log.debug(f"[VCText] Form View: User {interaction.user} selected Voice Channel: {self.selected_vc.name}")
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.select(cls=ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="2. Select Text Channel", min_values=1, max_values=1, row=1)
    async def select_tc(self, interaction: discord.Interaction, select: ChannelSelect):
        self.selected_tc = select.values[0]
        log.debug(f"[VCText] Form View: User {interaction.user} selected Text Channel: {self.selected_tc.name}")
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.select(cls=RoleSelect, placeholder="3. Select Role to Ping", min_values=1, max_values=1, row=2)
    async def select_role(self, interaction: discord.Interaction, select: RoleSelect):
        self.selected_role = select.values[0]
        log.debug(f"[VCText] Form View: User {interaction.user} selected Role: {self.selected_role.name}")
        self.check_complete()
        await interaction.response.edit_message(content=self.get_status_text(), view=self)

    @discord.ui.button(label="4. Set Custom Message", style=discord.ButtonStyle.primary, row=3)
    async def set_msg_btn(self, interaction: discord.Interaction, button: Button):
        log.debug(f"[VCText] Form View: User {interaction.user} clicked Set Message button.")
        await interaction.response.send_modal(VCTextSetupModal(self))

    @discord.ui.button(label="5. Save Configuration", style=discord.ButtonStyle.secondary, disabled=True, row=3)
    async def save_btn(self, interaction: discord.Interaction, button: Button):
        log.debug(f"[VCText] Form View: User {interaction.user} clicked Save Configuration button.")
        
        async with self.cog.config.guild(self.ctx.guild).mappings() as mappings:
            mappings[str(self.selected_vc.id)] = {
                "text_channel_id": self.selected_tc.id,
                "role_id": self.selected_role.id,
                "message": self.custom_message
            }
            
        await interaction.response.edit_message(content=f"✅ **Configuration Saved!**\nVoice Channel {self.selected_vc.mention} will now ping {self.selected_role.name} in {self.selected_tc.mention}.", view=None)
        log.debug(f"[VCText] Form Config saved for guild {self.ctx.guild.name} ({self.ctx.guild.id}).")
        self.stop()

class VCText(commands.Cog):
    """
    Ping a configured role when the first person joins a voice channel.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=83726194723, force_registration=True)
        
        default_guild = {
            "mappings": {},  # "voice_channel_id": { "text_channel_id": int, "role_id": int, "message": str }
            "role_mappings": {}  # "voice_channel_id": role_id
        }
        self.config.register_guild(**default_guild)
        log.debug("[VCText] Cog initialized.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Listener to detect when a user joins a voice channel."""
        if member.bot:
            return
            
        # We only care if they actually changed channels
        if before.channel == after.channel:
            return
            
        guild = member.guild
        role_mappings = await self.config.guild(guild).role_mappings()

        # --- ROLE REMOVAL LOGIC (Leaving a VC) ---
        if before.channel:
            before_vc_id = str(before.channel.id)
            if before_vc_id in role_mappings:
                role_id = role_mappings[before_vc_id]
                role = guild.get_role(role_id)
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason=f"Left voice channel {before.channel.name}")
                        log.debug(f"[VCText] Removed role {role.name} from {member} (Left VC {before.channel.name})")
                    except discord.Forbidden:
                        log.debug(f"[VCText] Permissions error: Cannot remove role {role.name} from {member}.")
                    except Exception as e:
                        log.debug(f"[VCText] Unexpected error removing role: {e}")

        # We only care about join events from here on for ping logic / role addition
        if not after.channel:
            return

        # --- ROLE ADDITION LOGIC (Joining a VC) ---
        after_vc_id = str(after.channel.id)
        if after_vc_id in role_mappings:
            role_id = role_mappings[after_vc_id]
            role = guild.get_role(role_id)
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"Joined voice channel {after.channel.name}")
                    log.debug(f"[VCText] Added role {role.name} to {member} (Joined VC {after.channel.name})")
                except discord.Forbidden:
                    log.debug(f"[VCText] Permissions error: Cannot add role {role.name} to {member}.")
                except Exception as e:
                    log.debug(f"[VCText] Unexpected error adding role: {e}")

        vc_id_str = str(after.channel.id)
        mappings = await self.config.guild(guild).mappings()

        if vc_id_str not in mappings:
            log.debug(f"[VCText] User {member} joined {after.channel.name}, but it is not a tracked ping channel.")
            return

        # Check if they are the first person in the channel
        if len(after.channel.members) == 1:
            log.debug(f"[VCText] User {member} is the FIRST to join {after.channel.name}. Initiating ping sequence.")
            
            mapping = mappings[vc_id_str]
            tc_id = mapping.get("text_channel_id")
            role_id = mapping.get("role_id")
            raw_msg = mapping.get("message", "A tracked voice channel is now active!")

            text_channel = guild.get_channel(tc_id)
            role = guild.get_role(role_id)

            if not text_channel or not role:
                log.debug(f"[VCText] Execution failed: Missing text channel (ID: {tc_id}) or role (ID: {role_id}) in guild {guild.name}.")
                return

            # Format variables
            formatted_msg = raw_msg.replace("{user}", member.display_name)\
                                   .replace("{user.mention}", member.mention)\
                                   .replace("{vc}", after.channel.name)
            
            final_content = f"{role.mention}\n{formatted_msg}"

            try:
                # Use AllowedMentions to ensure the role gets pinged successfully even if it isn't set to 'mentionable' natively
                await text_channel.send(final_content, allowed_mentions=discord.AllowedMentions(roles=[role]))
                log.debug(f"[VCText] Ping successfully sent to {text_channel.name} for {after.channel.name}.")
            except discord.Forbidden:
                log.debug(f"[VCText] Permissions error: Cannot send messages in {text_channel.name}.")
            except Exception as e:
                log.debug(f"[VCText] Unexpected error sending ping: {e}")
        else:
            log.debug(f"[VCText] User {member} joined {after.channel.name}, but channel already has {len(after.channel.members)} members.")

    @commands.group(name="vctextset", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def vctextset(self, ctx: commands.Context):
        """Admin commands to manage VC Text mappings."""
        await ctx.send_help()

    @vctextset.command(name="addform", aliases=["form"])
    async def vctextset_addform(self, ctx: commands.Context):
        """
        Interactively add a new VC Text mapping using a UI Form.
        """
        log.debug(f"[VCText] Interactive form initiated by {ctx.author} in {ctx.guild.name}.")
        view = VCTextSetupView(self, ctx)
        await ctx.send(view.get_status_text(), view=view)

    @vctextset.command(name="add")
    async def vctextset_add(self, ctx: commands.Context, voice_channel: discord.VoiceChannel, text_channel: discord.TextChannel, role: discord.Role, *, message: str):
        """
        Add a VC Text mapping via text command.
        
        Variables you can use in the message: `{user}`, `{user.mention}`, `{vc}`
        """
        log.debug(f"[VCText] Manual add command triggered by {ctx.author}. VC: {voice_channel.name}, TC: {text_channel.name}, Role: {role.name}")
        
        async with self.config.guild(ctx.guild).mappings() as mappings:
            mappings[str(voice_channel.id)] = {
                "text_channel_id": text_channel.id,
                "role_id": role.id,
                "message": message
            }
            
        await ctx.send(f"✅ Mapping added! Joining {voice_channel.mention} will ping {role.name} in {text_channel.mention}.")

    @vctextset.command(name="remove")
    async def vctextset_remove(self, ctx: commands.Context, voice_channel: discord.VoiceChannel):
        """Remove an existing VC Text mapping by specifying the Voice Channel."""
        log.debug(f"[VCText] Remove command triggered by {ctx.author} for VC {voice_channel.name}.")
        
        async with self.config.guild(ctx.guild).mappings() as mappings:
            if str(voice_channel.id) in mappings:
                del mappings[str(voice_channel.id)]
                await ctx.send(f"✅ The mapping for {voice_channel.mention} has been removed.")
                log.debug(f"[VCText] Successfully removed mapping for {voice_channel.name}.")
            else:
                await ctx.send(f"❌ No configuration found for {voice_channel.mention}.")
                log.debug(f"[VCText] Failed to remove: no mapping found for {voice_channel.name}.")

    @vctextset.command(name="view", aliases=["list"])
    async def vctextset_view(self, ctx: commands.Context):
        """View a table of all configured VC Text mappings for this server."""
        log.debug(f"[VCText] View command triggered by {ctx.author} in {ctx.guild.name}.")
        
        mappings = await self.config.guild(ctx.guild).mappings()
        if not mappings:
            log.debug(f"[VCText] View command returned empty for {ctx.guild.name}.")
            return await ctx.send("No VC Text mappings are currently configured for this server.")

        table_data = []
        for vc_id, data in mappings.items():
            vc = ctx.guild.get_channel(int(vc_id))
            tc = ctx.guild.get_channel(data.get("text_channel_id"))
            role = ctx.guild.get_role(data.get("role_id"))

            vc_name = vc.name if vc else f"Deleted VC ({vc_id})"
            tc_name = tc.name if tc else f"Deleted TC ({data.get('text_channel_id')})"
            role_name = role.name if role else f"Deleted Role ({data.get('role_id')})"

            msg_preview = data.get("message", "No Message")
            if len(msg_preview) > 25:
                msg_preview = msg_preview[:22] + "..."

            table_data.append([vc_name, tc_name, role_name, msg_preview])

        # Generate a scannable table
        rendered_table = tabulate(table_data, headers=["Voice Channel", "Text Channel", "Target Role", "Message Preview"], tablefmt="grid")
        
        log.debug(f"[VCText] Successfully generated mappings table for {ctx.guild.name}.")
        
        # Pagify the code block string in case there are many entries
        for page in pagify(rendered_table, page_length=1980):
            await ctx.send(box(page, lang="none"))

    @vctextset.command(name="roleadd")
    async def vctextset_roleadd(self, ctx: commands.Context, voice_channel: discord.VoiceChannel, role: discord.Role):
        """Add a VC Role mapping: assign a role when a user joins this Voice Channel."""
        log.debug(f"[VCText] Role mapping add command triggered by {ctx.author}. VC: {voice_channel.name}, Role: {role.name}")
        
        async with self.config.guild(ctx.guild).role_mappings() as role_mappings:
            role_mappings[str(voice_channel.id)] = role.id
            
        await ctx.send(f"✅ Role mapping added! Joining {voice_channel.mention} will grant the {role.name} role.")

    @vctextset.command(name="roleremove")
    async def vctextset_roleremove(self, ctx: commands.Context, voice_channel: discord.VoiceChannel):
        """Remove a VC Role mapping."""
        log.debug(f"[VCText] Role mapping remove command triggered by {ctx.author} for VC {voice_channel.name}.")
        
        async with self.config.guild(ctx.guild).role_mappings() as role_mappings:
            if str(voice_channel.id) in role_mappings:
                del role_mappings[str(voice_channel.id)]
                await ctx.send(f"✅ The role mapping for {voice_channel.mention} has been removed.")
                log.debug(f"[VCText] Successfully removed role mapping for {voice_channel.name}.")
            else:
                await ctx.send(f"❌ No role mapping found for {voice_channel.mention}.")
                log.debug(f"[VCText] Failed to remove: no role mapping found for {voice_channel.name}.")

    @vctextset.command(name="roleview", aliases=["rolelist"])
    async def vctextset_roleview(self, ctx: commands.Context):
        """View a table of all configured VC Role mappings for this server."""
        log.debug(f"[VCText] Role view command triggered by {ctx.author} in {ctx.guild.name}.")
        
        role_mappings = await self.config.guild(ctx.guild).role_mappings()
        if not role_mappings:
            log.debug(f"[VCText] Role view command returned empty for {ctx.guild.name}.")
            return await ctx.send("No VC Role mappings are currently configured for this server.")

        table_data = []
        for vc_id, role_id in role_mappings.items():
            vc = ctx.guild.get_channel(int(vc_id))
            role = ctx.guild.get_role(role_id)

            vc_name = vc.name if vc else f"Deleted VC ({vc_id})"
            role_name = role.name if role else f"Deleted Role ({role_id})"

            table_data.append([vc_name, role_name])

        # Generate a scannable table
        rendered_table = tabulate(table_data, headers=["Voice Channel", "Assigned Role"], tablefmt="grid")
        
        log.debug(f"[VCText] Successfully generated role mappings table for {ctx.guild.name}.")
        
        # Pagify the code block string in case there are many entries
        for page in pagify(rendered_table, page_length=1980):
            await ctx.send(box(page, lang="none"))