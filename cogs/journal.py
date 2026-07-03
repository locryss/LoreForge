"""
Character Journals — private RP logs from the character's perspective.
/journal write | /journal view | /journal share | /journal list
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from sqlalchemy import select
from database.session import get_db
from database.models import CharacterJournal, Character
from services.utils import is_gm

journal_group = app_commands.Group(name="journal", description="Your character's private journal")


async def _get_active_char(interaction: discord.Interaction) -> "Character | None":
    async with get_db() as db:
        result = await db.execute(
            select(Character).where(
                Character.user_id == interaction.user.id,
                Character.guild_id == interaction.guild_id,
                Character.is_active == True,
            )
        )
        return result.scalar_one_or_none()


async def _get_in_world_date(guild_id: int) -> str | None:
    try:
        from cogs.calendar import get_current_in_world_date
        return await get_current_in_world_date(guild_id)
    except Exception:
        return None


@journal_group.command(name="write", description="Write a journal entry from your character's perspective")
@app_commands.describe(title="Entry title (optional)", content="The journal entry text")
async def journal_write(interaction: discord.Interaction, content: str, title: str | None = None):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    char = await _get_active_char(interaction)
    if not char:
        await interaction.response.send_message("You need an active character to write in a journal. Use `/character use` first.", ephemeral=True)
        return

    iwd = await _get_in_world_date(interaction.guild_id)

    async with get_db() as db:
        entry = CharacterJournal(
            character_id=char.id,
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            title=title or None,
            content=content,
            is_public=False,
            in_world_date=iwd,
        )
        db.add(entry)
        await db.flush()
        entry_id = entry.id

    embed = discord.Embed(
        title=f"📖 Journal Entry #{entry_id}{f' — {title}' if title else ''}",
        description=content[:2000],
        color=0x6366F1,
    )
    if iwd:
        embed.add_field(name="In-World Date", value=iwd, inline=True)
    embed.add_field(name="Character", value=char.name, inline=True)
    embed.set_footer(text=f"Private  •  Use /journal share {entry_id} to make it public  •  LoreForge")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@journal_group.command(name="view", description="View your character's journal entries (private)")
@app_commands.describe(page="Page number")
async def journal_view(interaction: discord.Interaction, page: int = 1):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    char = await _get_active_char(interaction)
    if not char:
        await interaction.response.send_message("You need an active character to view a journal.", ephemeral=True)
        return

    per_page = 5
    offset = (page - 1) * per_page

    async with get_db() as db:
        result = await db.execute(
            select(CharacterJournal)
            .where(CharacterJournal.character_id == char.id)
            .order_by(CharacterJournal.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        entries = list(result.scalars().all())

        from sqlalchemy import func
        count_result = await db.execute(
            select(func.count()).select_from(CharacterJournal).where(CharacterJournal.character_id == char.id)
        )
        total = count_result.scalar() or 0

    if not entries:
        await interaction.response.send_message(
            f"**{char.name}** has no journal entries yet. Use `/journal write` to start one.",
            ephemeral=True,
        )
        return

    total_pages = max(1, (total + per_page - 1) // per_page)
    embed = discord.Embed(
        title=f"📖 {char.name}'s Journal",
        color=0x6366F1,
    )
    for entry in entries:
        visibility = "🌐 Public" if entry.is_public else "🔒 Private"
        ts = int(entry.created_at.timestamp())
        heading = f"#{entry.id}{f' — {entry.title}' if entry.title else ''}"
        iwd_str = f"  ·  {entry.in_world_date}" if entry.in_world_date else ""
        embed.add_field(
            name=f"{heading}  ·  {visibility}  ·  <t:{ts}:d>{iwd_str}",
            value=entry.content[:300] + ("..." if len(entry.content) > 300 else ""),
            inline=False,
        )
    embed.set_footer(text=f"Page {page}/{total_pages}  ·  {total} entries  •  LoreForge")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@journal_group.command(name="read", description="Read a specific journal entry by ID")
@app_commands.describe(entry_id="Journal entry ID")
async def journal_read(interaction: discord.Interaction, entry_id: int):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(select(CharacterJournal).where(CharacterJournal.id == entry_id))
        entry = result.scalar_one_or_none()

    if not entry:
        await interaction.response.send_message("Journal entry not found.", ephemeral=True)
        return

    # Only the author (or GM) can read private entries
    is_author = entry.user_id == interaction.user.id
    gm = await is_gm(interaction)
    if not entry.is_public and not is_author and not gm:
        await interaction.response.send_message("That entry is private.", ephemeral=True)
        return

    async with get_db() as db:
        char_result = await db.execute(select(Character).where(Character.id == entry.character_id))
        char = char_result.scalar_one_or_none()

    embed = discord.Embed(
        title=f"📖 {entry.title or f'Journal Entry #{entry.id}'}",
        description=entry.content,
        color=0x6366F1,
    )
    if char:
        embed.set_author(name=char.name, icon_url=char.avatar_url or discord.Embed.Empty)
    if entry.in_world_date:
        embed.add_field(name="In-World Date", value=entry.in_world_date, inline=True)
    ts = int(entry.created_at.timestamp())
    embed.add_field(name="Written", value=f"<t:{ts}:D>", inline=True)
    visibility = "🌐 Public" if entry.is_public else "🔒 Private"
    embed.set_footer(text=f"{visibility}  •  LoreForge")
    ephemeral = not entry.is_public
    await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


@journal_group.command(name="share", description="Make a journal entry public (or private again)")
@app_commands.describe(entry_id="Journal entry ID to toggle public/private")
async def journal_share(interaction: discord.Interaction, entry_id: int):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(
            select(CharacterJournal).where(
                CharacterJournal.id == entry_id,
                CharacterJournal.user_id == interaction.user.id,
            )
        )
        entry = result.scalar_one_or_none()

    if not entry:
        await interaction.response.send_message("Entry not found or you don't own it.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(select(CharacterJournal).where(CharacterJournal.id == entry_id))
        entry = result.scalar_one_or_none()
        entry.is_public = not entry.is_public
        new_state = entry.is_public

    state_str = "🌐 **public**" if new_state else "🔒 **private**"
    await interaction.response.send_message(
        f"Journal entry **#{entry_id}** is now {state_str}.",
        ephemeral=True,
    )


@journal_group.command(name="delete", description="Delete one of your journal entries")
@app_commands.describe(entry_id="Journal entry ID to delete")
async def journal_delete(interaction: discord.Interaction, entry_id: int):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(
            select(CharacterJournal).where(
                CharacterJournal.id == entry_id,
                CharacterJournal.user_id == interaction.user.id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            await interaction.response.send_message("Entry not found or you don't own it.", ephemeral=True)
            return
        await db.delete(entry)

    await interaction.response.send_message(f"Journal entry #{entry_id} deleted.", ephemeral=True)


@journal_group.command(name="gm-view", description="GM: View all journal entries for a character")
@app_commands.describe(character_name="Character name")
async def journal_gm_view(interaction: discord.Interaction, character_name: str):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can view all journals.", ephemeral=True)
        return

    async with get_db() as db:
        char_result = await db.execute(
            select(Character).where(
                Character.guild_id == interaction.guild_id,
                Character.name.ilike(f"%{character_name}%"),
            )
        )
        char = char_result.scalar_one_or_none()
        if not char:
            await interaction.response.send_message("Character not found.", ephemeral=True)
            return

        result = await db.execute(
            select(CharacterJournal)
            .where(CharacterJournal.character_id == char.id)
            .order_by(CharacterJournal.created_at.desc())
            .limit(10)
        )
        entries = list(result.scalars().all())

    if not entries:
        await interaction.response.send_message(f"**{char.name}** has no journal entries.", ephemeral=True)
        return

    embed = discord.Embed(title=f"📖 {char.name}'s Journal (GM View)", color=0x6366F1)
    for entry in entries:
        visibility = "🌐" if entry.is_public else "🔒"
        ts = int(entry.created_at.timestamp())
        embed.add_field(
            name=f"{visibility} #{entry.id}{f' — {entry.title}' if entry.title else ''} · <t:{ts}:d>",
            value=entry.content[:250] + ("..." if len(entry.content) > 250 else ""),
            inline=False,
        )
    embed.set_footer(text=f"{len(entries)} entries shown  •  LoreForge")
    await interaction.response.send_message(embed=embed, ephemeral=True)


class JournalCog(commands.Cog, name="Journal"):
    def __init__(self, bot):
        self.bot = bot
        bot.tree.add_command(journal_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("journal")


async def setup(bot):
    await bot.add_cog(JournalCog(bot))
