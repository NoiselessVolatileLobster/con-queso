from .gifonly import GifOnly

async def setup(bot):
    await bot.add_cog(GifOnly(bot))