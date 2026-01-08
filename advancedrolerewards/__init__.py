from .advancedrolerewards import AdvancedRoleRewards

async def setup(bot):
    await bot.add_cog(AdvancedRoleRewards(bot))