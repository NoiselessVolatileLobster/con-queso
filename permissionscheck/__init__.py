from .permissionscheck import PermissionsCheck

async def setup(bot):
    await bot.add_cog(PermissionsCheck(bot))