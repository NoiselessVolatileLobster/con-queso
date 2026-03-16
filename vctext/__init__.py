from redbot.core.bot import Red
from .vctext import VCText

async def setup(bot: Red):
    await bot.add_cog(VCText(bot))