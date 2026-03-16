import logging
import time
import random
import discord
from typing import Optional
from tabulate import tabulate

from redbot.core import Config, commands, app_commands
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.bot import Red

log = logging.getLogger("red.NoiselessVolatileLobster.topicchange")

class SuggestionModal(discord.ui.Modal, title="Suggest a Topic"):
    """Modal for trusted users to suggest a topic."""
    
    topic_input = discord.ui.TextInput(
        label="What topic would you like to suggest?",
        style=discord.TextStyle.paragraph,
        placeholder="Type your interesting question or topic here...",
        required=True,
        max_length=1500,
    )

    def __init__(self, cog: "TopicChange", guild: discord.Guild):
        super().__init__()
        self.cog = cog
        self.guild = guild
        log.debug(f"Initialized SuggestionModal for guild {guild.id}")

    async def on_submit(self, interaction: discord.Interaction):
        log.debug(f"SuggestionModal submitted by {interaction.user.id} in guild {self.guild.id}")
        
        async with self.cog.config.guild(self.guild).topics() as topics:
            next_id = await self.cog.config.guild(self.guild).next_topic_id()
            
            topics[str(next_id)] = {
                "text": self.topic_input.value,
                "author_id": interaction.user.id,
                "approved": False,
                "last_posted": 0
            }
            
            await self.cog.config.guild(self.guild).next_topic_id.set(next_id + 1)
            
        log.debug(f"Topic '{self.topic_input.value}' saved with ID {next_id} (Pending Approval).")
        await interaction.response.send_message(
            "Thank you! Your topic suggestion has been submitted for administrator review.",
            ephemeral=True
        )


class TopicChange(commands.Cog):
    """Seamlessly change topics using random suggestions and GIFs."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=839210045123, force_registration=True)
        
        default_guild = {
            "gif_url": "https://tenor.com/view/topic-change-new-topic-gif-25126135",
            "trusted_roles": [],
            "next_topic_id": 1,
            "topics": {}
        }
        
        self.config.register_guild(**default_guild)
        log.debug("TopicChange config initialized with default values.")

    # -------------------------------------------------------------------------
    # SLASH COMMANDS (User Experience)
    # -------------------------------------------------------------------------

    @app_commands.command(name="topicchange", description="Request a new topic for the current channel.")
    async def slash_topicchange(self, interaction: discord.Interaction):
        """Changes the topic gracefully by posting a GIF and a random question."""
        log.debug(f"User {interaction.user.id} initiated /topicchange in channel {interaction.channel.id}")
        
        guild = interaction.guild
        if not guild:
            log.debug("Slash command run outside a guild.")
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        all_topics = await self.config.guild(guild).topics()
        approved_topics = {tid: tdata for tid, tdata in all_topics.items() if tdata.get("approved")}
        
        if not approved_topics:
            log.debug(f"Guild {guild.id} has no approved topics available.")
            await interaction.response.send_message(
                "There are currently no approved topics available! Please ask an administrator to add some.", 
                ephemeral=True
            )
            return

        # Find the oldest last_posted time to prioritize topics that haven't been selected
        min_posted_time = min([t["last_posted"] for t in approved_topics.values()])
        priority_topics = [
            (tid, tdata) for tid, tdata in approved_topics.items() 
            if tdata["last_posted"] == min_posted_time
        ]
        
        # Pick a random topic from the priority list
        selected_tid, selected_topic = random.choice(priority_topics)
        log.debug(f"Selected topic ID {selected_tid} for channel {interaction.channel.id}")

        # Send the ephemeral acknowledgement
        await interaction.response.send_message("Asking to change topic now...", ephemeral=True)

        # Update last_posted
        async with self.config.guild(guild).topics() as topics:
            topics[selected_tid]["last_posted"] = int(time.time())

        # Send the GIF
        gif_url = await self.config.guild(guild).gif_url()
        log.debug(f"Sending GIF: {gif_url}")
        await interaction.channel.send(gif_url)
        
        # Send the question
        question_text = selected_topic["text"]
        log.debug(f"Sending Topic: {question_text}")
        await interaction.channel.send(f"**New Topic:** {question_text}")

    @app_commands.command(name="topicsuggestion", description="Suggest a new topic (Requires Trusted Role).")
    async def slash_topicsuggestion(self, interaction: discord.Interaction):
        """Allows trusted users to suggest new topics via a Modal."""
        log.debug(f"User {interaction.user.id} initiated /topicsuggestion")
        
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        trusted_roles = await self.config.guild(guild).trusted_roles()
        
        # If no roles are set, or user has one of the roles, allow access
        has_permission = False
        if not trusted_roles:
            log.debug("No trusted roles set; denying suggestion.")
        else:
            author_role_ids = [r.id for r in interaction.user.roles]
            has_permission = any(role_id in author_role_ids for role_id in trusted_roles)

        # Allow server owner or admin to bypass role check
        if interaction.user == guild.owner or interaction.user.guild_permissions.administrator:
            has_permission = True

        if not has_permission:
            log.debug(f"User {interaction.user.id} lacks trusted roles. Access denied.")
            await interaction.response.send_message(
                "You do not have a required trusted role to suggest topics.", 
                ephemeral=True
            )
            return

        # Pop up the modal
        log.debug(f"Sending SuggestionModal to user {interaction.user.id}")
        await interaction.response.send_modal(SuggestionModal(self, guild))


    # -------------------------------------------------------------------------
    # ADMIN COMMANDS
    # -------------------------------------------------------------------------

    @commands.group(name="topicchangeset")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def topicchangeset(self, ctx: commands.Context):
        """Configure the TopicChange system."""
        pass

    @topicchangeset.command(name="view")
    async def topicchangeset_view(self, ctx: commands.Context):
        """View the current configuration and settings."""
        log.debug(f"Running topicchangeset view in guild {ctx.guild.id}")
        
        settings = await self.config.guild(ctx.guild).all()
        gif_url = settings["gif_url"]
        
        trusted_role_ids = settings["trusted_roles"]
        roles = [ctx.guild.get_role(r_id) for r_id in trusted_role_ids]
        roles_text = ", ".join([r.name for r in roles if r]) if roles else "None set"
        
        all_topics = settings["topics"]
        total_topics = len(all_topics)
        approved_topics = sum(1 for t in all_topics.values() if t.get("approved"))
        pending_topics = total_topics - approved_topics
        
        msg = (
            f"**TopicChange Configuration**\n"
            f"**GIF URL:** <{gif_url}>\n"
            f"**Trusted Roles:** {roles_text}\n"
            f"**Total Topics:** {total_topics} ({approved_topics} Approved, {pending_topics} Pending)"
        )
        await ctx.send(msg)

    @topicchangeset.command(name="gif")
    async def topicchangeset_gif(self, ctx: commands.Context, url: str):
        """Set the GIF URL that posts before the topic."""
        log.debug(f"Setting GIF to {url} in guild {ctx.guild.id}")
        await self.config.guild(ctx.guild).gif_url.set(url)
        await ctx.send(f"Topic change GIF updated successfully.")

    # --- Role Management ---

    @topicchangeset.group(name="role")
    async def topicchangeset_role(self, ctx: commands.Context):
        """Manage trusted roles for topic suggestions."""
        pass

    @topicchangeset_role.command(name="add")
    async def topicchangeset_role_add(self, ctx: commands.Context, role: discord.Role):
        """Add a trusted role."""
        log.debug(f"Adding trusted role {role.id} in guild {ctx.guild.id}")
        async with self.config.guild(ctx.guild).trusted_roles() as trusted_roles:
            if role.id not in trusted_roles:
                trusted_roles.append(role.id)
                await ctx.send(f"Role `{role.name}` has been added to trusted roles.")
            else:
                await ctx.send(f"Role `{role.name}` is already a trusted role.")

    @topicchangeset_role.command(name="remove")
    async def topicchangeset_role_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a trusted role."""
        log.debug(f"Removing trusted role {role.id} in guild {ctx.guild.id}")
        async with self.config.guild(ctx.guild).trusted_roles() as trusted_roles:
            if role.id in trusted_roles:
                trusted_roles.remove(role.id)
                await ctx.send(f"Role `{role.name}` has been removed from trusted roles.")
            else:
                await ctx.send(f"Role `{role.name}` is not a trusted role.")

    @topicchangeset_role.command(name="list")
    async def topicchangeset_role_list(self, ctx: commands.Context):
        """List all trusted roles."""
        log.debug(f"Listing trusted roles in guild {ctx.guild.id}")
        trusted_role_ids = await self.config.guild(ctx.guild).trusted_roles()
        if not trusted_role_ids:
            return await ctx.send("There are currently no trusted roles configured.")
            
        roles = [ctx.guild.get_role(r_id) for r_id in trusted_role_ids]
        headers = ["Role Name", "Role ID"]
        table_data = [[r.name if r else "Deleted Role", r_id] for r, r_id in zip(roles, trusted_role_ids)]
        
        table = tabulate(table_data, headers=headers, tablefmt="psql")
        for page in pagify(table):
            await ctx.send(box(page, lang="prolog"))

    # --- Topic Management ---

    @topicchangeset.group(name="topic")
    async def topicchangeset_topic(self, ctx: commands.Context):
        """Manage questions and topics."""
        pass

    @topicchangeset_topic.command(name="add")
    async def topicchangeset_topic_add(self, ctx: commands.Context, *, topic_text: str):
        """Manually add a pre-approved topic."""
        log.debug(f"Admin {ctx.author.id} adding topic in guild {ctx.guild.id}")
        async with self.config.guild(ctx.guild).topics() as topics:
            next_id = await self.config.guild(ctx.guild).next_topic_id()
            topics[str(next_id)] = {
                "text": topic_text,
                "author_id": ctx.author.id,
                "approved": True,
                "last_posted": 0
            }
            await self.config.guild(ctx.guild).next_topic_id.set(next_id + 1)
        await ctx.send(f"Topic added and automatically approved with ID `{next_id}`.")

    @topicchangeset_topic.command(name="remove")
    async def topicchangeset_topic_remove(self, ctx: commands.Context, topic_id: str):
        """Remove a topic by its ID."""
        log.debug(f"Admin {ctx.author.id} removing topic {topic_id} in guild {ctx.guild.id}")
        async with self.config.guild(ctx.guild).topics() as topics:
            if topic_id in topics:
                del topics[topic_id]
                await ctx.send(f"Topic ID `{topic_id}` has been successfully removed.")
            else:
                await ctx.send(f"Topic ID `{topic_id}` was not found.")

    @topicchangeset_topic.command(name="approve")
    async def topicchangeset_topic_approve(self, ctx: commands.Context, topic_id: str):
        """Approve a suggested topic by its ID."""
        log.debug(f"Admin {ctx.author.id} approving topic {topic_id} in guild {ctx.guild.id}")
        async with self.config.guild(ctx.guild).topics() as topics:
            if topic_id in topics:
                if topics[topic_id]["approved"]:
                    await ctx.send(f"Topic ID `{topic_id}` is already approved.")
                else:
                    topics[topic_id]["approved"] = True
                    await ctx.send(f"Topic ID `{topic_id}` has been approved and added to the rotation!")
            else:
                await ctx.send(f"Topic ID `{topic_id}` was not found.")

    @topicchangeset_topic.command(name="list")
    async def topicchangeset_topic_list(self, ctx: commands.Context):
        """List all approved topics."""
        log.debug(f"Listing approved topics in guild {ctx.guild.id}")
        all_topics = await self.config.guild(ctx.guild).topics()
        
        approved_topics = {tid: tdata for tid, tdata in all_topics.items() if tdata.get("approved")}
        if not approved_topics:
            return await ctx.send("There are currently no approved topics.")

        headers = ["ID", "Author ID", "Topic Snippet"]
        table_data = []
        for tid, tdata in approved_topics.items():
            snippet = (tdata["text"][:60] + '...') if len(tdata["text"]) > 60 else tdata["text"]
            table_data.append([tid, tdata["author_id"], snippet])
            
        table = tabulate(table_data, headers=headers, tablefmt="psql")
        for page in pagify(table):
            await ctx.send(box(page, lang="prolog"))

    @topicchangeset_topic.command(name="pending")
    async def topicchangeset_topic_pending(self, ctx: commands.Context):
        """List all pending/unapproved topics."""
        log.debug(f"Listing pending topics in guild {ctx.guild.id}")
        all_topics = await self.config.guild(ctx.guild).topics()
        
        pending_topics = {tid: tdata for tid, tdata in all_topics.items() if not tdata.get("approved")}
        if not pending_topics:
            return await ctx.send("There are currently no pending topics to approve.")

        headers = ["ID", "Author ID", "Topic Snippet"]
        table_data = []
        for tid, tdata in pending_topics.items():
            snippet = (tdata["text"][:60] + '...') if len(tdata["text"]) > 60 else tdata["text"]
            table_data.append([tid, tdata["author_id"], snippet])
            
        table = tabulate(table_data, headers=headers, tablefmt="psql")
        for page in pagify(table):
            await ctx.send(box(page, lang="prolog"))