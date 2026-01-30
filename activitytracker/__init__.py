from .activitytracker import ActivityTracker

async def setup(bot):
    await bot.add_cog(ActivityTracker(bot))