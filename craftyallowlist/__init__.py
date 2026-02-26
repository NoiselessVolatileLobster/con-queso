from .craftyallowlist import CraftyAllowlist

async def setup(bot):
    await bot.add_cog(CraftyAllowlist(bot))