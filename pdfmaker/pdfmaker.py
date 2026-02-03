import discord
import io
import aiohttp
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box
from fpdf import FPDF

class PdfMaker(commands.Cog):
    """
    Convert Python files to formatted PDFs.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2784561001, force_registration=True)
        
        default_guild = {
            "font_size": 10,
            "header_text": "Python Source Code",
            "show_line_numbers": False
        }
        self.config.register_guild(**default_guild)

    async def _download_file(self, url: str) -> str:
        """Downloads the file content from Discord."""
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise ValueError("Failed to download file.")
                return await resp.text()

    def _generate_pdf(self, content: str, filename: str, font_size: int, header_text: str, line_numbers: bool) -> io.BytesIO:
        """
        Generates a PDF file in memory using FPDF.
        Note: FPDF standard fonts only support Latin-1. We sanitize text to prevent crashes.
        """
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Courier", size=font_size)
        
        # Add Header
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, f"{header_text}: {filename}", 0, 1, 'C')
        pdf.ln(5)
        
        # Reset font for code
        pdf.set_font("Courier", size=font_size)
        
        # Sanitize content for Latin-1 (Standard FPDF limitation)
        # Replaces characters it can't print with '?' to avoid crashing
        content = content.encode('latin-1', 'replace').decode('latin-1')
        
        lines = content.split('\n')
        line_height = font_size / 2
        
        for i, line in enumerate(lines, 1):
            prefix = f"{i:4d} | " if line_numbers else ""
            full_line = f"{prefix}{line}"
            pdf.multi_cell(0, line_height + 2, txt=full_line)
            
        # Output to buffer
        # FPDF output(dest='S') returns a string, we encode to bytes for Discord
        try:
            pdf_string = pdf.output(dest='S')
            buffer = io.BytesIO(pdf_string.encode('latin-1'))
        except Exception:
            # Fallback for different FPDF versions/encodings
            buffer = io.BytesIO(pdf.output(dest='S').encode('latin-1', 'ignore'))
            
        buffer.seek(0)
        return buffer

    @commands.command()
    @commands.bot_has_permissions(attach_files=True)
    async def makepdf(self, ctx):
        """
        Upload a .py file to convert it to a PDF.
        
        Attach a python file to this command.
        """
        if not ctx.message.attachments:
            return await ctx.send("Please attach a `.py` file to convert.")
        
        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith(".py"):
            return await ctx.send("The attached file must be a Python (`.py`) file.")

        async with ctx.typing():
            try:
                content = await self._download_file(attachment.url)
            except ValueError:
                return await ctx.send("I couldn't download that file. Please try again.")

            settings = await self.config.guild(ctx.guild).all()
            
            try:
                pdf_buffer = await self.bot.loop.run_in_executor(
                    None, 
                    self._generate_pdf, 
                    content, 
                    attachment.filename,
                    settings['font_size'], 
                    settings['header_text'],
                    settings['show_line_numbers']
                )
            except Exception as e:
                return await ctx.send(f"An error occurred while generating the PDF: {e}")

            new_filename = f"{attachment.filename.replace('.py', '')}.pdf"
            file = discord.File(pdf_buffer, filename=new_filename)
            
            await ctx.send(f"Here is your PDF for `{attachment.filename}`:", file=file)

    @commands.group(name="pdfmakerset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def pdfmakerset(self, ctx):
        """Configuration settings for PdfMaker."""
        pass

    @pdfmakerset.command(name="view")
    async def pdfmakerset_view(self, ctx):
        """View current settings for this guild."""
        data = await self.config.guild(ctx.guild).all()
        
        table_data = [
            ["Setting", "Value"],
            ["Font Size", str(data['font_size'])],
            ["Header Text", data['header_text']],
            ["Line Numbers", str(data['show_line_numbers'])]
        ]
        
        # Calculate column widths
        col_widths = [max(len(str(row[i])) for row in table_data) for i in range(len(table_data[0]))]
        
        # Build the table
        header = f"+-{'-' * col_widths[0]}-+-{'-' * col_widths[1]}-+"
        rows = [header]
        
        for i, row in enumerate(table_data):
            row_str = f"| {str(row[0]).ljust(col_widths[0])} | {str(row[1]).ljust(col_widths[1])} |"
            rows.append(row_str)
            if i == 0:
                rows.append(header) # Separator after title
        
        rows.append(header)
        table = "\n".join(rows)
        
        await ctx.send(f"**Current PDF Settings**\n{box(table, lang='text')}")

    @pdfmakerset.command(name="fontsize")
    async def pdfmakerset_fontsize(self, ctx, size: int):
        """
        Set the font size for the PDF body.
        
        Range: 4 - 24
        """
        if not 4 <= size <= 24:
            return await ctx.send("Font size must be between 4 and 24.")
        
        await self.config.guild(ctx.guild).font_size.set(size)
        await ctx.send(f"Font size set to `{size}`.")

    @pdfmakerset.command(name="header")
    async def pdfmakerset_header(self, ctx, *, text: str):
        """
        Set the header text that appears at the top of the PDF.
        """
        if len(text) > 50:
            return await ctx.send("Header text cannot be longer than 50 characters.")
            
        await self.config.guild(ctx.guild).header_text.set(text)
        await ctx.send(f"Header text set to: `{text}`")

    @pdfmakerset.command(name="linenumbers")
    async def pdfmakerset_linenumbers(self, ctx, toggle: bool):
        """
        Toggle line numbers in the generated PDF.
        """
        await self.config.guild(ctx.guild).show_line_numbers.set(toggle)
        state = "enabled" if toggle else "disabled"
        await ctx.send(f"Line numbers have been {state}.")