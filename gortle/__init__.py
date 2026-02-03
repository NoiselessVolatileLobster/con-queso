from .gortle import Gortle

async def setup(bot):
    await bot.add_cog(Gortle(bot))