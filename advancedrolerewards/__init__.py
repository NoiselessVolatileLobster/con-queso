from .advancedrolerewards import AdvancedRoleRewards

async def setup(bot):
    bot.add_cog(AdvancedRoleRewards(bot))