from .pdfmaker import PdfMaker

async def setup(bot):
    await bot.add_cog(PdfMaker(bot))