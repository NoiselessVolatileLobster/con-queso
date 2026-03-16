import logging
from .topicchange import TopicChange

log = logging.getLogger("red.NoiselessVolatileLobster.topicchange")

async def setup(bot):
    log.debug("Initializing the TopicChange cog...")
    cog = TopicChange(bot)
    await bot.add_cog(cog)
    log.debug("TopicChange cog added successfully.")