from .leveluptracker import LevelUpTracker

__red_end_user_data_statement__ = (
    "This cog stores user IDs, join timestamps, and timestamps of when "
    "users reached specific levels for statistical tracking."
)

async def setup(bot):
    await bot.add_cog(LevelUpTracker(bot))