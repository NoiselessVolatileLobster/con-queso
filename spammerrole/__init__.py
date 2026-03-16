from .spammerrole import SpammerRole

async def setup(bot):
    await bot.add_cog(SpammerRole(bot))