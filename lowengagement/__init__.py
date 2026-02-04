from .lowengagement import LowEngagement

async def setup(bot):
    await bot.add_cog(LowEngagement(bot))