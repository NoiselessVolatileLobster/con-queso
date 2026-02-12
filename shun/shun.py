import discord
import time
from datetime import datetime, timezone
from typing import Optional, Dict

from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import humanize_timedelta, box, pagify
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate

class Shun(commands.Cog):
    """
    Shun people. That'll teach them.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2784539201, force_registration=True)
        
        default_guild = {
            "shuns": {},  # Format: {target_id: {shunner_id: timestamp}}
            "allow_self_shun": False
        }
        self.config.register_guild(**default_guild)

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    async def shun(self, ctx: commands.Context, target: discord.Member):
        """
        Shun a member.
        
        They will know what they did.
        """
        if not ctx.invoked_subcommand:
            await self._shun_member(ctx, target)

    @shun.command(name="list")
    @commands.guild_only()
    async def shun_list(self, ctx: commands.Context):
        """
        Show a list of everyone currently being shunned.
        """
        shuns = await self.config.guild(ctx.guild).shuns()
        
        if not shuns:
            return await ctx.send("No one is currently being shunned. Is nature healing?")

        # Prepare table data
        table_data = []
        headers = ["Target", "Shunned By", "Count"]
        
        # We need to fetch member objects or use names/IDs
        # To avoid massive API spam, we try to resolve from cache or fallback to string ID
        
        for target_id_str, shunners_data in shuns.items():
            if not shunners_data:
                continue
                
            target = ctx.guild.get_member(int(target_id_str))
            target_name = str(target) if target else f"User ID: {target_id_str}"
            
            shunner_names = []
            for shunner_id_str in shunners_data.keys():
                shunner = ctx.guild.get_member(int(shunner_id_str))
                shunner_names.append(str(shunner) if shunner else f"ID: {shunner_id_str}")
            
            # Format the list of shunners to wrap nicely in the table if needed, 
            # but for simplicity in a code block, comma separation is usually best.
            # We limit to first 3 names + "and X others" to prevent table explosion if desired,
            # but prompt asked for "who shunned them", so we list all.
            shunners_str = ", ".join(shunner_names)
            
            table_data.append([target_name, shunners_str, len(shunner_names)])

        if not table_data:
            return await ctx.send("No active shuns found.")

        # formatting table
        # We manually construct a table to ensure it looks good in Discord code blocks
        # calculating column widths
        max_target_len = max(len(row[0]) for row in table_data)
        max_target_len = max(max_target_len, len(headers[0]))
        
        # We don't want the table to be wider than ~60 chars for mobile readability if possible,
        # but names can be long.
        
        lines = []
        header_str = f"{headers[0]:<{max_target_len}} | {headers[1]}"
        lines.append(header_str)
        lines.append("-" * len(header_str))
        
        for row in table_data:
            target = row[0]
            shunners = row[1]
            lines.append(f"{target:<{max_target_len}} | {shunners}")

        full_text = "\n".join(lines)
        
        pages = list(pagify(full_text, delims=["\n"], page_length=1900))
        
        for page in pages:
            await ctx.send(box(page))

    @commands.command()
    @commands.guild_only()
    async def unshun(self, ctx: commands.Context, target: discord.Member):
        """
        Unshun a member.
        
        Reveals how long they were shunned for.
        """
        async with self.config.guild(ctx.guild).shuns() as shuns:
            target_id = str(target.id)
            shunner_id = str(ctx.author.id)

            if target_id not in shuns or shunner_id not in shuns[target_id]:
                return await ctx.send(f"You are not shunning {target.display_name}.")

            timestamp = shuns[target_id].pop(shunner_id)
            
            # Clean up empty keys
            if not shuns[target_id]:
                del shuns[target_id]

        # Calculate duration
        start_time = datetime.fromtimestamp(timestamp, timezone.utc)
        now = datetime.now(timezone.utc)
        duration_str = humanize_timedelta(timedelta=now - start_time)
        
        if not duration_str:
            duration_str = "a few seconds"

        await ctx.send(
            f"{ctx.author.mention} has unshunned {target.mention}. "
            f"They were shunned for {duration_str}. That'll teach them."
        )

    async def _shun_member(self, ctx: commands.Context, target: discord.Member):
        if target.id == ctx.author.id:
            allow_self = await self.config.guild(ctx.guild).allow_self_shun()
            if not allow_self:
                return await ctx.send("You cannot shun yourself. That's just sad.")

        if target.id == self.bot.user.id:
            return await ctx.send("You cannot shun me. I am inevitable.")

        async with self.config.guild(ctx.guild).shuns() as shuns:
            target_id = str(target.id)
            shunner_id = str(ctx.author.id)

            if target_id not in shuns:
                shuns[target_id] = {}

            if shunner_id in shuns[target_id]:
                return await ctx.send(f"You are already shunning {target.display_name}. They know.")

            shuns[target_id][shunner_id] = datetime.now(timezone.utc).timestamp()

        await ctx.send(f"{ctx.author.mention} has shunned {target.mention}.")

    # --- Admin / Settings ---

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def shunset(self, ctx: commands.Context):
        """
        Configuration for the Shun cog.
        """
        pass

    @shunset.command(name="selfshun")
    async def shunset_selfshun(self, ctx: commands.Context, toggle: bool):
        """
        Allow or disallow users to shun themselves.
        """
        await self.config.guild(ctx.guild).allow_self_shun.set(toggle)
        await ctx.send(f"Self-shunning has been {'enabled' if toggle else 'disabled'}.")

    @shunset.command(name="reset")
    async def shunset_reset(self, ctx: commands.Context):
        """
        Clear all shuns in this guild.
        """
        msg = await ctx.send("Are you sure you want to clear ALL shuns in this server? (yes/no)")
        pred = MessagePredicate.yes_or_no(ctx)
        try:
            await self.bot.wait_for("message", check=pred, timeout=30)
        except TimeoutError:
            return await ctx.send("Action cancelled.")

        if pred.result:
            await self.config.guild(ctx.guild).shuns.set({})
            await ctx.send("The slate has been wiped clean. Everyone is unshunned.")
        else:
            await ctx.send("Action cancelled.")

    @shunset.command(name="view")
    async def shunset_view(self, ctx: commands.Context):
        """
        View current settings.
        """
        data = await self.config.guild(ctx.guild).all()
        allow_self = data['allow_self_shun']
        shun_count = len(data['shuns'])
        
        total_shuns = sum(len(shunners) for shunners in data['shuns'].values())

        settings_info = (
            f"**Allow Self Shun:** {allow_self}\n"
            f"**Active Targets:** {shun_count}\n"
            f"**Total Individual Shuns:** {total_shuns}"
        )
        
        await ctx.send(box(settings_info, lang="yaml"))