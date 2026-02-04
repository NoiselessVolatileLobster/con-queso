import discord
import io
import datetime
from typing import Optional, List

from redbot.core import commands, checks, Config
from redbot.core.utils.chat_formatting import box
from redbot.core.bot import Red

class UserExport(commands.Cog):
    """
    Export messages from a specific user to a text file.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8473629104, force_registration=True)
        default_guild = {
            "ignored_channels": []
        }
        self.config.register_guild(**default_guild)

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_messages=True)
    async def userexportset(self, ctx):
        """
        Configuration settings for UserExport.
        """
        pass

    @userexportset.command(name="ignore")
    async def userexportset_ignore(self, ctx, channel: discord.TextChannel):
        """
        Toggle ignoring a specific channel during exports.
        
        If a channel is ignored, the bot will skip it when scanning for user messages.
        """
        async with self.config.guild(ctx.guild).ignored_channels() as ignored:
            if channel.id in ignored:
                ignored.remove(channel.id)
                await ctx.send(f"{channel.mention} is no longer ignored.")
            else:
                ignored.append(channel.id)
                await ctx.send(f"{channel.mention} will now be ignored during exports.")

    @userexportset.command(name="view")
    async def userexportset_view(self, ctx):
        """
        View current settings for the guild.
        """
        ignored_ids = await self.config.guild(ctx.guild).ignored_channels()
        ignored_channels_names = []
        for ch_id in ignored_ids:
            ch = ctx.guild.get_channel(ch_id)
            if ch:
                ignored_channels_names.append(f"#{ch.name}")
            else:
                ignored_channels_names.append(f"{ch_id} (Deleted)")

        # Using a code-block table for readability
        table_data = (
            f"Property          | Value\n"
            f"------------------|--------------------------------\n"
            f"Ignored Channels  | {len(ignored_ids)}\n"
        )
        
        await ctx.send(box(table_data, lang="prolog"))
        
        if ignored_channels_names:
            list_str = "\n".join(ignored_channels_names)
            # Ensure we don't hit message limits if the list is huge
            if len(list_str) > 1900:
                list_str = list_str[:1900] + "\n... (truncated)"
            
            await ctx.send(f"**Ignored Channel List:**\n{box(list_str)}")

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_messages=True)
    async def exportmessages(self, ctx, user: discord.Member, days: int = 10):
        """
        Export messages from a specific user into a text file.
        
        Defaults to scanning the last 10 days.
        """
        # Enforce a reasonable limit to prevent timeouts/API abuse, while respecting the 14 day logic mention
        if days > 14:
            await ctx.send("To ensure stability and respect Discord API limits, please choose 14 days or less.")
            return
        if days < 1:
            await ctx.send("Please specify at least 1 day.")
            return

        # Calculate the cutoff time
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        ignored_list = await self.config.guild(ctx.guild).ignored_channels()
        
        messages_found = []
        channels_scanned = 0
        
        progress_msg = await ctx.send(
            f"Starting export for **{user.display_name}** going back **{days} days**.\n"
            "I am scanning all text channels. This may take a moment..."
        )
        
        async with ctx.typing():
            for channel in ctx.guild.text_channels:
                # Skip ignored channels
                if channel.id in ignored_list:
                    continue
                
                # Check if bot has permissions to read history
                perms = channel.permissions_for(ctx.guild.me)
                if not perms.read_message_history or not perms.read_messages:
                    continue

                channels_scanned += 1
                try:
                    # Scan history
                    async for message in channel.history(limit=None, after=cutoff):
                        if message.author.id == user.id:
                            # Format: Timestamp | Channel | Content
                            timestamp_str = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
                            
                            # Clean up content (remove newlines to keep it 1 line per message mostly)
                            clean_content = message.clean_content.replace("\n", "  ")
                            
                            entry = f"[{timestamp_str}] [#{channel.name}]: {clean_content}"
                            
                            # Append attachment links if present
                            if message.attachments:
                                att_list = ", ".join([a.url for a in message.attachments])
                                entry += f" [Attachments: {att_list}]"
                                
                            messages_found.append((message.created_at, entry))
                except Exception:
                    # Silently skip channels that cause errors (e.g. unexpected perm issues)
                    continue

        if not messages_found:
            await progress_msg.edit(content=f"No messages found for **{user.display_name}** in the last {days} days.")
            return

        # Sort messages by timestamp (Oldest to Newest)
        messages_found.sort(key=lambda x: x[0])
        
        # Write to memory buffer
        output_buffer = io.StringIO()
        header = (
            f"User Export for: {user.display_name} ({user.id})\n"
            f"Date Generated: {datetime.datetime.now()}\n"
            f"Range: Last {days} days\n"
            f"Total Messages: {len(messages_found)}\n"
            f"Channels Scanned: {channels_scanned}\n"
            f"--------------------------------------------------\n\n"
        )
        output_buffer.write(header)
        
        for _, entry in messages_found:
            output_buffer.write(entry + "\n")
            
        output_buffer.seek(0)
        
        # Send file
        try:
            filename = f"export_{user.name}_{datetime.date.today()}.txt"
            file_obj = discord.File(output_buffer, filename=filename)
            await progress_msg.delete()
            await ctx.send(
                f"Export complete. Found **{len(messages_found)}** messages in **{channels_scanned}** channels.",
                file=file_obj
            )
        except discord.HTTPException:
            await ctx.send("The file was too large to upload to Discord.")
        finally:
            output_buffer.close()