"""
Relationship Bonds — narrative bonds between characters.
/bond set @user <description> | /bond view | /bond remove @user | /bond all (GM)
"""

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from database.session import get_db
from database.models import CharacterBond, Character
from services.utils import is_gm

bond_group = app_commands.Group(name="bond", description="Narrative relationship bonds between characters")

_BOND_COLOR = 0xF1C40F


async def _get_active_char(user_id: int, guild_id: int) -> "Character | None":
    async with get_db() as db:
        result = await db.execute(
            select(Character).where(
                Character.user_id == user_id,
                Character.guild_id == guild_id,
                Character.is_active == True,
            )
        )
        return result.scalar_one_or_none()


@bond_group.command(name="set", description="Set a bond with another player's character")
@app_commands.describe(
    user="The other player",
    description="Describe the bond (e.g. 'Bonded over surviving the northern storm together')",
)
async def bond_set(interaction: discord.Interaction, user: discord.Member, description: str):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return
    if user.id == interaction.user.id:
        await interaction.response.send_message("You can't bond with yourself.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    src = await _get_active_char(interaction.user.id, interaction.guild_id)
    if not src:
        await interaction.followup.send("You need an active character. Use `/character use` first.", ephemeral=True)
        return

    tgt = await _get_active_char(user.id, interaction.guild_id)
    if not tgt:
        await interaction.followup.send(f"{user.display_name} doesn't have an active character.", ephemeral=True)
        return

    async with get_db() as db:
        # Upsert — update if exists
        existing = await db.execute(
            select(CharacterBond).where(
                CharacterBond.character_id == src.id,
                CharacterBond.target_character_id == tgt.id,
            )
        )
        bond = existing.scalar_one_or_none()
        if bond:
            bond.description = description
        else:
            db.add(CharacterBond(
                character_id=src.id,
                target_character_id=tgt.id,
                guild_id=interaction.guild_id,
                description=description,
            ))

    embed = discord.Embed(
        title="💛 Bond Set",
        description=f"**{src.name}** → **{tgt.name}**\n\n*{description}*",
        color=_BOND_COLOR,
    )
    embed.set_footer(text="This bond shows on your character sheet  •  LoreForge")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bond_group.command(name="view", description="View your character's bonds with others")
async def bond_view(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    char = await _get_active_char(interaction.user.id, interaction.guild_id)
    if not char:
        await interaction.response.send_message("You need an active character.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(
            select(CharacterBond).where(CharacterBond.character_id == char.id)
        )
        bonds = list(result.scalars().all())

        tgt_ids = [b.target_character_id for b in bonds]
        targets = {}
        if tgt_ids:
            tgt_result = await db.execute(select(Character).where(Character.id.in_(tgt_ids)))
            for c in tgt_result.scalars().all():
                targets[c.id] = c.name

        # Also get bonds that others set with this character (incoming)
        in_result = await db.execute(
            select(CharacterBond).where(CharacterBond.target_character_id == char.id)
        )
        in_bonds = list(in_result.scalars().all())
        src_ids = [b.character_id for b in in_bonds]
        sources = {}
        if src_ids:
            src_result = await db.execute(select(Character).where(Character.id.in_(src_ids)))
            for c in src_result.scalars().all():
                sources[c.id] = c.name

    embed = discord.Embed(title=f"💛 {char.name}'s Bonds", color=_BOND_COLOR)

    if bonds:
        for b in bonds[:10]:
            tgt_name = targets.get(b.target_character_id, f"Character #{b.target_character_id}")
            embed.add_field(name=f"→ {tgt_name}", value=b.description[:200], inline=False)
    else:
        embed.description = "No bonds set yet. Use `/bond set @user <description>` to form one."

    if in_bonds:
        lines = [f"• **{sources.get(b.character_id, '?')}** — {b.description[:100]}" for b in in_bonds[:5]]
        embed.add_field(name="Others' bonds with you", value="\n".join(lines), inline=False)

    embed.set_footer(text="LoreForge  •  Bonds also appear on your character sheet")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bond_group.command(name="remove", description="Remove a bond you set with another player's character")
@app_commands.describe(user="The player whose character you bonded with")
async def bond_remove(interaction: discord.Interaction, user: discord.Member):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    src = await _get_active_char(interaction.user.id, interaction.guild_id)
    if not src:
        await interaction.response.send_message("You need an active character.", ephemeral=True)
        return

    tgt = await _get_active_char(user.id, interaction.guild_id)
    if not tgt:
        await interaction.response.send_message("That player doesn't have an active character.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(
            select(CharacterBond).where(
                CharacterBond.character_id == src.id,
                CharacterBond.target_character_id == tgt.id,
            )
        )
        bond = result.scalar_one_or_none()
        if not bond:
            await interaction.response.send_message("No bond found.", ephemeral=True)
            return
        await db.delete(bond)

    await interaction.response.send_message(
        f"Bond between **{src.name}** and **{tgt.name}** removed.", ephemeral=True
    )


@bond_group.command(name="all", description="GM: View all bonds across the server")
async def bond_all(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can view all bonds.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(
            select(CharacterBond)
            .where(CharacterBond.guild_id == interaction.guild_id)
            .order_by(CharacterBond.created_at.desc())
            .limit(25)
        )
        bonds = list(result.scalars().all())

        all_char_ids = list({b.character_id for b in bonds} | {b.target_character_id for b in bonds})
        char_names = {}
        if all_char_ids:
            char_result = await db.execute(select(Character).where(Character.id.in_(all_char_ids)))
            for c in char_result.scalars().all():
                char_names[c.id] = c.name

    if not bonds:
        await interaction.response.send_message("No bonds exist on this server yet.", ephemeral=True)
        return

    embed = discord.Embed(title="💛 All Character Bonds", color=_BOND_COLOR)
    for b in bonds:
        src_name = char_names.get(b.character_id, f"#{b.character_id}")
        tgt_name = char_names.get(b.target_character_id, f"#{b.target_character_id}")
        embed.add_field(
            name=f"{src_name} → {tgt_name}",
            value=b.description[:200],
            inline=False,
        )
    embed.set_footer(text=f"{len(bonds)} bonds  •  LoreForge")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def get_character_bonds(character_id: int) -> list[dict]:
    """Utility for character sheet — returns list of {name, desc} dicts."""
    try:
        async with get_db() as db:
            result = await db.execute(
                select(CharacterBond).where(CharacterBond.character_id == character_id).limit(5)
            )
            bonds = list(result.scalars().all())
            if not bonds:
                return []
            tgt_ids = [b.target_character_id for b in bonds]
            tgt_result = await db.execute(select(Character).where(Character.id.in_(tgt_ids)))
            tgt_names = {c.id: c.name for c in tgt_result.scalars().all()}
            return [{"name": tgt_names.get(b.target_character_id, "?"), "desc": b.description} for b in bonds]
    except Exception:
        return []


class BondsCog(commands.Cog, name="Bonds"):
    def __init__(self, bot):
        self.bot = bot
        bot.tree.add_command(bond_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("bond")


async def setup(bot):
    await bot.add_cog(BondsCog(bot))
