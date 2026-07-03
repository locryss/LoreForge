"""
Pinboard / Rumors — in-world bulletin board entries posted by GMs.
/rumor post | /rumor list | /rumor remove | /rumor pin
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from sqlalchemy import select
from database.session import get_db
from database.models import Rumor
from services.utils import is_gm

rumor_group = app_commands.Group(name="rumor", description="In-world pinboard and rumors")

_RUMOR_COLOR = 0xD97706


@rumor_group.command(name="post", description="GM: Post a rumor or notice to the in-world pinboard")
@app_commands.describe(
    text="The rumor or notice text (written in-world, not as a bot notification)",
    source="Where it came from (e.g. Tavern Gossip, Town Herald, Wanted Poster)",
)
async def rumor_post(interaction: discord.Interaction, text: str, source: str | None = None):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can post rumors.", ephemeral=True)
        return

    await interaction.response.defer()

    # Get in-world date if available
    iwd = None
    try:
        from cogs.calendar import get_current_in_world_date
        iwd = await get_current_in_world_date(interaction.guild_id)
    except Exception:
        pass

    async with get_db() as db:
        rumor = Rumor(
            guild_id=interaction.guild_id,
            text=text,
            source=source or "Unknown",
            is_active=True,
            posted_by=interaction.user.id,
        )
        db.add(rumor)
        await db.flush()
        rumor_id = rumor.id

    embed = discord.Embed(
        title=f"📌 {source or 'Notice Board'}",
        description=text,
        color=_RUMOR_COLOR,
    )
    embed.set_footer(
        text=f"#{rumor_id}  ·  {iwd or 'Unknown date'}  ·  LoreForge Pinboard"
    )
    await interaction.followup.send(embed=embed)


@rumor_group.command(name="list", description="Browse all active rumors and notices")
@app_commands.describe(page="Page number (5 per page)")
async def rumor_list(interaction: discord.Interaction, page: int = 1):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    per_page = 5
    offset = (page - 1) * per_page

    async with get_db() as db:
        result = await db.execute(
            select(Rumor)
            .where(Rumor.guild_id == interaction.guild_id, Rumor.is_active == True)
            .order_by(Rumor.posted_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        rumors = list(result.scalars().all())

        from sqlalchemy import func
        total = (await db.execute(
            select(func.count()).select_from(Rumor).where(
                Rumor.guild_id == interaction.guild_id, Rumor.is_active == True
            )
        )).scalar() or 0

    if not rumors:
        await interaction.response.send_message(
            "No active rumors or notices. GMs can post one with `/rumor post`.", ephemeral=True
        )
        return

    total_pages = max(1, (total + per_page - 1) // per_page)
    embed = discord.Embed(
        title="📌 In-World Pinboard",
        color=_RUMOR_COLOR,
    )
    for r in rumors:
        ts = int(r.posted_at.timestamp())
        embed.add_field(
            name=f"#{r.id}  ·  {r.source}  ·  <t:{ts}:d>",
            value=r.text[:300] + ("..." if len(r.text) > 300 else ""),
            inline=False,
        )
    embed.set_footer(text=f"Page {page}/{total_pages}  ·  {total} active notices  •  LoreForge")
    await interaction.response.send_message(embed=embed)


@rumor_group.command(name="remove", description="GM: Remove a rumor from the pinboard")
@app_commands.describe(rumor_id="ID of the rumor to remove")
async def rumor_remove(interaction: discord.Interaction, rumor_id: int):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can remove rumors.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(
            select(Rumor).where(Rumor.id == rumor_id, Rumor.guild_id == interaction.guild_id)
        )
        rumor = result.scalar_one_or_none()
        if not rumor:
            await interaction.response.send_message("Rumor not found.", ephemeral=True)
            return
        rumor.is_active = False

    await interaction.response.send_message(f"📌 Rumor **#{rumor_id}** has been taken down.", ephemeral=True)


@rumor_group.command(name="view", description="Read a specific rumor in full")
@app_commands.describe(rumor_id="Rumor ID")
async def rumor_view(interaction: discord.Interaction, rumor_id: int):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(
            select(Rumor).where(Rumor.id == rumor_id, Rumor.guild_id == interaction.guild_id)
        )
        rumor = result.scalar_one_or_none()

    if not rumor:
        await interaction.response.send_message("Rumor not found.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📌 {rumor.source or 'Notice Board'} — #{rumor.id}",
        description=rumor.text,
        color=_RUMOR_COLOR,
    )
    ts = int(rumor.posted_at.timestamp())
    status = "Active" if rumor.is_active else "Removed"
    embed.set_footer(text=f"Posted <t:{ts}:D>  ·  {status}  •  LoreForge")
    await interaction.response.send_message(embed=embed)


class RumorCog(commands.Cog, name="Rumors"):
    def __init__(self, bot):
        self.bot = bot
        bot.tree.add_command(rumor_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("rumor")


async def setup(bot):
    await bot.add_cog(RumorCog(bot))
