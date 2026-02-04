from .userexport import UserExport

async def setup(bot):
    await bot.add_cog(UserExport(bot))