"""
Combat scoreboard — lightweight HP tracker for GM-run scenes.
No automation. GMs open a session, add combatants, adjust HP/conditions manually.
Avrae handles dice; this bot tracks state and displays the board.
Temp NPCs are deleted from the NPC table when the session closes.
"""

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, delete, func
import datetime

from database.session import get_db
from database.models import (
    CombatSession, Combatant, CombatLog,
    Character, NPC, NPCInteractionLog,
)
from services.utils import is_gm

combat_group = app_commands.Group(name="combat", description="HP scoreboard for combat scenes")

# ── Helpers ────────────────────────────────────────────────────────────────────

_CONDITION_EMOJIS = {
    "poisoned": "🟢",
    "stunned": "⚡",
    "blinded": "🙈",
    "prone": "⬇️",
    "restrained": "🕸️",
    "frightened": "😨",
    "charmed": "💗",
    "paralyzed": "❄️",
    "unconscious": "💤",
    "exhausted": "😰",
    "bleeding": "🩸",
    "burning": "🔥",
    "silenced": "🔇",
    "cursed": "💀",
}


def _hp_bar(current: int, maximum: int, length: int = 10) -> str:
    if maximum <= 0:
        return "░" * length
    # Clamp to [0, 1]. Without the upper clamp an over-healed combatant
    # (current > maximum) produced a bar `length * ratio` characters wide --
    # 30 blocks for 30/10 -- because the "░" * (length - filled) padding goes
    # negative and silently contributes nothing. That overflows the combat
    # board embed and reads as "300%".
    ratio = min(1.0, max(0, current) / maximum)
    filled = round(ratio * length)
    bar = "█" * filled + "░" * (length - filled)
    pct = int(ratio * 100)
    return f"`{bar}` {pct}%"


def _condition_tags(conditions: list[str]) -> str:
    if not conditions:
        return ""
    parts = []
    for c in conditions:
        emoji = _CONDITION_EMOJIS.get(c.lower(), "⚠️")
        parts.append(f"{emoji}{c}")
    return "  " + "  ".join(parts)


async def _get_open_session(guild_id: int, db) -> CombatSession | None:
    result = await db.execute(
        select(CombatSession).where(
            CombatSession.guild_id == guild_id,
            CombatSession.closed_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def _build_board_embed(session: CombatSession, db) -> discord.Embed:
    embed = discord.Embed(
        title=f"⚔️ {session.title or 'Combat'}",
        color=0xEF4444,
    )
    result = await db.execute(
        select(Combatant).where(
            Combatant.combat_session_id == session.id,
            Combatant.is_removed == False,
        ).order_by(Combatant.combatant_type, Combatant.display_name)
    )
    combatants = result.scalars().all()

    players = [c for c in combatants if c.combatant_type == "player"]
    npcs = [c for c in combatants if c.combatant_type == "npc"]

    def _format_row(c: Combatant) -> str:
        status = ""
        if c.current_hp <= 0:
            status = " 💀 **DOWNED**"
        elif c.current_hp <= (c.max_hp * 0.25):
            status = " ⚠️ *critical*"
        cond = _condition_tags(c.conditions or [])
        ac_text = f"  •  AC {c.ac}" if c.ac else ""
        return (
            f"**{c.display_name}**{ac_text}{status}\n"
            f"{_hp_bar(c.current_hp, c.max_hp)}  `{c.current_hp}/{c.max_hp}`{cond}"
        )

    if players:
        embed.add_field(
            name="🛡️ Players",
            value="\n\n".join(_format_row(c) for c in players),
            inline=False,
        )
    if npcs:
        embed.add_field(
            name="💀 NPCs",
            value="\n\n".join(_format_row(c) for c in npcs),
            inline=False,
        )
    if not combatants:
        embed.description = "*No combatants yet. Use `/combat add` or `/combat add-npc`.*"

    embed.set_footer(text=f"Session #{session.id}  •  LoreForge")
    return embed


async def _log_event(session_id: int, guild_id: int, text: str, user_id: int, db):
    db.add(CombatLog(
        combat_session_id=session_id,
        text=text,
        logged_by_user_id=user_id,
    ))


# ── /combat open ──────────────────────────────────────────────────────────────

@combat_group.command(name="open", description="Open a new combat session (GM only)")
@app_commands.describe(title="Name for this encounter (optional)")
async def combat_open(interaction: discord.Interaction, title: str = None):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can open combat.", ephemeral=True)
        return

    async with get_db() as db:
        existing = await _get_open_session(interaction.guild_id, db)
        if existing:
            await interaction.response.send_message(
                f"There's already an open session: **{existing.title or 'Combat'}** (#{existing.id}). Close it first with `/combat close`.",
                ephemeral=True,
            )
            return

        session = CombatSession(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            title=title or "Combat",
            opened_by_user_id=interaction.user.id,
        )
        db.add(session)
        await db.flush()

        embed = await _build_board_embed(session, db)
        board_msg = await interaction.channel.send(embed=embed)
        session.board_message_id = board_msg.id

    await interaction.response.send_message(
        f"⚔️ Combat **{title or 'Combat'}** opened! Add combatants with `/combat add` and `/combat add-npc`.",
        ephemeral=True,
    )


# ── /combat add @user ─────────────────────────────────────────────────────────

@combat_group.command(name="add", description="Add a player character to the board (GM only)")
@app_commands.describe(user="Player to add", hp="Starting HP", max_hp="Max HP", ac="Armour class")
async def combat_add(
    interaction: discord.Interaction,
    user: discord.Member,
    hp: int,
    max_hp: int,
    ac: int = None,
):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can add combatants.", ephemeral=True)
        return

    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No open combat session.", ephemeral=True)
            return

        # Try to find their active character for display name
        char_result = await db.execute(
            select(Character).where(
                Character.user_id == user.id,
                Character.guild_id == interaction.guild_id,
                Character.is_active == True,
            )
        )
        char = char_result.scalar_one_or_none()
        display = char.name if char else user.display_name

        existing_check = await db.execute(
            select(Combatant).where(
                Combatant.combat_session_id == session.id,
                Combatant.combatant_type == "player",
                Combatant.character_id == (char.id if char else None),
            )
        )
        if existing_check.scalar_one_or_none():
            await interaction.response.send_message(f"**{display}** is already in the session.", ephemeral=True)
            return

        combatant = Combatant(
            combat_session_id=session.id,
            combatant_type="player",
            character_id=char.id if char else None,
            display_name=display,
            current_hp=hp,
            max_hp=max_hp,
            ac=ac,
            conditions=[],
        )
        db.add(combatant)
        await _log_event(session.id, interaction.guild_id, f"➕ {display} joined (HP {hp}/{max_hp})", interaction.user.id, db)

        embed = await _build_board_embed(session, db)

    await interaction.response.send_message(f"➕ **{display}** added to combat.", ephemeral=True)
    await _update_board(interaction.client, session, embed)


# ── /combat add-npc ───────────────────────────────────────────────────────────

async def _npc_autocomplete(interaction: discord.Interaction, current: str):
    async with get_db() as db:
        result = await db.execute(
            select(NPC).where(
                NPC.guild_id == interaction.guild_id,
                NPC.name.ilike(f"%{current}%"),
                NPC.is_dead == False,
            ).limit(25)
        )
        npcs = result.scalars().all()
    return [app_commands.Choice(name=n.name + (" [TEMP]" if n.temporary else ""), value=n.name) for n in npcs]


@combat_group.command(name="add-npc", description="Add an NPC to the combat board (GM only)")
@app_commands.describe(name="NPC name", hp="Starting HP", max_hp="Max HP", ac="Armour class")
@app_commands.autocomplete(name=_npc_autocomplete)
async def combat_add_npc(
    interaction: discord.Interaction,
    name: str,
    hp: int,
    max_hp: int,
    ac: int = None,
):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can add combatants.", ephemeral=True)
        return

    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No open combat session.", ephemeral=True)
            return

        npc_result = await db.execute(
            select(NPC).where(NPC.guild_id == interaction.guild_id, NPC.name.ilike(name))
        )
        npc = npc_result.scalar_one_or_none()
        if not npc:
            await interaction.response.send_message(f"NPC **{name}** not found. Create them first with `/npc create` or `/npc temp`.", ephemeral=True)
            return

        combatant = Combatant(
            combat_session_id=session.id,
            combatant_type="npc",
            npc_id=npc.id,
            display_name=npc.name,
            current_hp=hp,
            max_hp=max_hp,
            ac=ac,
            conditions=[],
        )
        db.add(combatant)
        await _log_event(session.id, interaction.guild_id, f"➕ {npc.name} (NPC) joined (HP {hp}/{max_hp})", interaction.user.id, db)

        embed = await _build_board_embed(session, db)

    await interaction.response.send_message(f"➕ NPC **{npc.name}** added to combat.", ephemeral=True)
    await _update_board(interaction.client, session, embed)


# ── /combat hp ────────────────────────────────────────────────────────────────

async def _combatant_autocomplete(interaction: discord.Interaction, current: str):
    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            return []
        result = await db.execute(
            select(Combatant).where(
                Combatant.combat_session_id == session.id,
                Combatant.is_removed == False,
                Combatant.display_name.ilike(f"%{current}%"),
            ).limit(25)
        )
        combatants = result.scalars().all()
    return [app_commands.Choice(name=c.display_name, value=c.display_name) for c in combatants]


@combat_group.command(name="hp", description="Adjust a combatant's HP (GM only)")
@app_commands.describe(
    name="Combatant name",
    change="HP change: -10 for damage, +5 for healing, or set:50 to set exactly",
    note="Optional note (shown in log)",
)
@app_commands.autocomplete(name=_combatant_autocomplete)
async def combat_hp(
    interaction: discord.Interaction,
    name: str,
    change: str,
    note: str = None,
):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can change HP.", ephemeral=True)
        return

    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No open combat session.", ephemeral=True)
            return

        result = await db.execute(
            select(Combatant).where(
                Combatant.combat_session_id == session.id,
                Combatant.is_removed == False,
                Combatant.display_name.ilike(name),
            )
        )
        combatant = result.scalar_one_or_none()
        if not combatant:
            await interaction.response.send_message(f"Combatant **{name}** not found.", ephemeral=True)
            return

        old_hp = combatant.current_hp
        change = change.strip()
        if change.startswith("set:"):
            try:
                combatant.current_hp = int(change[4:])
            except ValueError:
                await interaction.response.send_message("Use `set:50` to set HP to a specific value.", ephemeral=True)
                return
        else:
            try:
                combatant.current_hp = max(0, combatant.current_hp + int(change))
            except ValueError:
                await interaction.response.send_message("Use a number like `-10` for damage or `+5` for healing.", ephemeral=True)
                return

        direction = "healed" if combatant.current_hp > old_hp else "took damage"
        diff = abs(combatant.current_hp - old_hp)
        log_text = f"💉 {combatant.display_name} {direction} ({old_hp} → {combatant.current_hp})"
        if note:
            log_text += f" — {note}"
        await _log_event(session.id, interaction.guild_id, log_text, interaction.user.id, db)

        embed = await _build_board_embed(session, db)

    downed = " 💀 **Downed!**" if combatant.current_hp <= 0 else ""
    await interaction.response.send_message(
        f"💉 **{combatant.display_name}** HP: `{old_hp}` → `{combatant.current_hp}`{downed}",
        ephemeral=False,
    )
    await _update_board(interaction.client, session, embed)


# ── /combat condition ─────────────────────────────────────────────────────────

@combat_group.command(name="condition", description="Add or remove a condition from a combatant (GM only)")
@app_commands.describe(
    name="Combatant name",
    condition="Condition to toggle (e.g. poisoned, stunned)",
    remove="Set to true to remove the condition instead",
)
@app_commands.autocomplete(name=_combatant_autocomplete)
async def combat_condition(
    interaction: discord.Interaction,
    name: str,
    condition: str,
    remove: bool = False,
):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can set conditions.", ephemeral=True)
        return

    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No open combat session.", ephemeral=True)
            return

        result = await db.execute(
            select(Combatant).where(
                Combatant.combat_session_id == session.id,
                Combatant.is_removed == False,
                Combatant.display_name.ilike(name),
            )
        )
        combatant = result.scalar_one_or_none()
        if not combatant:
            await interaction.response.send_message(f"Combatant **{name}** not found.", ephemeral=True)
            return

        conditions = list(combatant.conditions or [])
        cond_lower = condition.lower()
        if remove:
            conditions = [c for c in conditions if c.lower() != cond_lower]
            action = "removed from"
        else:
            if cond_lower not in [c.lower() for c in conditions]:
                conditions.append(condition)
            action = "added to"
        combatant.conditions = conditions

        emoji = _CONDITION_EMOJIS.get(cond_lower, "⚠️")
        await _log_event(
            session.id, interaction.guild_id,
            f"{emoji} {condition} {action} {combatant.display_name}",
            interaction.user.id, db,
        )
        embed = await _build_board_embed(session, db)

    await interaction.response.send_message(
        f"{emoji} **{condition}** {action} **{combatant.display_name}**.", ephemeral=False
    )
    await _update_board(interaction.client, session, embed)


# ── /combat board ─────────────────────────────────────────────────────────────

@combat_group.command(name="board", description="Post the current HP board in this channel")
async def combat_board(interaction: discord.Interaction):
    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No open combat session.", ephemeral=True)
            return
        embed = await _build_board_embed(session, db)

    await interaction.response.send_message(embed=embed)


# ── /combat log ───────────────────────────────────────────────────────────────

@combat_group.command(name="log", description="Add a manual note to the combat log (GM only)")
@app_commands.describe(note="What happened (e.g. 'Zareth used Dragon Breath')")
async def combat_log(interaction: discord.Interaction, note: str):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can add combat log entries.", ephemeral=True)
        return

    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No open combat session.", ephemeral=True)
            return
        await _log_event(session.id, interaction.guild_id, f"📝 {note}", interaction.user.id, db)

    await interaction.response.send_message(f"📝 Logged: {note[:200]}", ephemeral=True)


# ── /combat history ───────────────────────────────────────────────────────────

@combat_group.command(name="history", description="View the full log for the current or a past session")
@app_commands.describe(session_id="Session ID (omit for current session)")
async def combat_history(interaction: discord.Interaction, session_id: int = None):
    async with get_db() as db:
        if session_id:
            result = await db.execute(
                select(CombatSession).where(
                    CombatSession.id == session_id,
                    CombatSession.guild_id == interaction.guild_id,
                )
            )
            session = result.scalar_one_or_none()
        else:
            session = await _get_open_session(interaction.guild_id, db)

        if not session:
            await interaction.response.send_message("Session not found.", ephemeral=True)
            return

        log_result = await db.execute(
            select(CombatLog)
            .where(CombatLog.combat_session_id == session.id)
            .order_by(CombatLog.timestamp.asc())
        )
        logs = list(log_result.scalars().all())

    if not logs:
        await interaction.response.send_message("No log entries yet.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📋 Combat Log — {session.title or 'Combat'} (#{session.id})",
        color=0xEF4444,
    )
    chunks = []
    current_chunk = []
    for entry in logs:
        ts = int(entry.timestamp.timestamp())
        line = f"`<t:{ts}:t>` {entry.text}"
        if sum(len(x) for x in current_chunk) + len(line) > 1000:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
        else:
            current_chunk.append(line)
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    for i, chunk in enumerate(chunks[:5]):
        embed.add_field(name="​" if i > 0 else "Events", value=chunk, inline=False)

    embed.set_footer(text=f"{len(logs)} entries  •  LoreForge")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── /combat remove ────────────────────────────────────────────────────────────

@combat_group.command(name="remove", description="Remove a combatant from the board (GM only)")
@app_commands.describe(name="Combatant to remove")
@app_commands.autocomplete(name=_combatant_autocomplete)
async def combat_remove(interaction: discord.Interaction, name: str):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can remove combatants.", ephemeral=True)
        return

    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No open combat session.", ephemeral=True)
            return

        result = await db.execute(
            select(Combatant).where(
                Combatant.combat_session_id == session.id,
                Combatant.is_removed == False,
                Combatant.display_name.ilike(name),
            )
        )
        combatant = result.scalar_one_or_none()
        if not combatant:
            await interaction.response.send_message(f"Combatant **{name}** not found.", ephemeral=True)
            return

        combatant.is_removed = True
        await _log_event(session.id, interaction.guild_id, f"🚫 {combatant.display_name} removed from combat", interaction.user.id, db)
        embed = await _build_board_embed(session, db)

    await interaction.response.send_message(f"🚫 **{combatant.display_name}** removed.", ephemeral=True)
    await _update_board(interaction.client, session, embed)


# ── /combat close ─────────────────────────────────────────────────────────────

@combat_group.command(name="close", description="Close the current combat session (GM only)")
async def combat_close(interaction: discord.Interaction):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can close combat.", ephemeral=True)
        return

    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No open combat session.", ephemeral=True)
            return

        # Collect temp NPC IDs to delete
        temp_result = await db.execute(
            select(Combatant).where(
                Combatant.combat_session_id == session.id,
                Combatant.combatant_type == "npc",
                Combatant.npc_id.isnot(None),
            )
        )
        npc_combatants = temp_result.scalars().all()

        deleted_temps = []
        for c in npc_combatants:
            npc_r = await db.execute(select(NPC).where(NPC.id == c.npc_id))
            npc = npc_r.scalar_one_or_none()
            if npc and npc.temporary:
                await db.execute(delete(NPCInteractionLog).where(NPCInteractionLog.npc_id == npc.id))
                await db.delete(npc)
                deleted_temps.append(npc.name)

        session.closed_at = datetime.datetime.utcnow()
        await _log_event(session.id, interaction.guild_id, "🏁 Combat closed", interaction.user.id, db)

        # Update board one final time
        embed = await _build_board_embed(session, db)
        embed.title = f"🏁 {session.title or 'Combat'} — Ended"
        embed.color = 0x6B7280

    await _update_board(interaction.client, session, embed)

    temp_note = ""
    if deleted_temps:
        temp_note = f"\n🗑️ Temp NPCs deleted: {', '.join(deleted_temps)}"
    await interaction.response.send_message(
        f"🏁 **{session.title or 'Combat'}** closed.{temp_note}", ephemeral=False
    )


# ── /combat status ────────────────────────────────────────────────────────────

@combat_group.command(name="status", description="Check if a combat session is open")
async def combat_status(interaction: discord.Interaction):
    async with get_db() as db:
        session = await _get_open_session(interaction.guild_id, db)
        if not session:
            await interaction.response.send_message("No combat session is currently open.", ephemeral=True)
            return
        count_result = await db.execute(
            select(func.count()).where(
                Combatant.combat_session_id == session.id,
                Combatant.is_removed == False,
            )
        )
        combatant_count = count_result.scalar()

    opened_ts = int(session.opened_at.timestamp())
    await interaction.response.send_message(
        f"⚔️ **{session.title or 'Combat'}** is open since <t:{opened_ts}:R>  •  {combatant_count} combatants",
        ephemeral=True,
    )


# ── Board refresh helper ───────────────────────────────────────────────────────

async def _update_board(bot: commands.Bot, session: CombatSession, embed: discord.Embed):
    """Edit the pinned board message if it exists."""
    if not session.board_message_id or not session.channel_id:
        return
    try:
        channel = bot.get_channel(session.channel_id)
        if not channel:
            return
        msg = channel.get_partial_message(session.board_message_id)
        await msg.edit(embed=embed)
    except Exception:
        pass


# ── Cog ────────────────────────────────────────────────────────────────────────

class CombatCog(commands.Cog, name="Combat"):
    def __init__(self, bot):
        self.bot = bot
        bot.tree.add_command(combat_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("combat")


async def setup(bot):
    await bot.add_cog(CombatCog(bot))
