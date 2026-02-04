from .sortinghat import SortingHat

async def setup(bot):
    await bot.add_cog(SortingHat(bot))