from .shun import Shun

__red_end_user_data_statement__ = "This cog stores user IDs and timestamps to track shunning status."

async def setup(bot):
    await bot.add_cog(Shun(bot))