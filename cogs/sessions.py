import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, desc
from datetime import datetime
from database.session import get_db
from database.models import SessionLog, Character, GuildConfig, SessionNote, TimelineEvent, TimelineLoreLink
from services.utils import gm_only, is_gm
from sqlalchemy.orm.attributes import flag_modified

session_group = app_commands.Group(name="session", description="Session management (GM only)")


async def _get_characters_at_location(guild_id: int) -> list[str]:
    """Get names of active characters in the guild."""
    async with get_db() as db:
        result = await db.execute(
            select(Character).where(
                Character.guild_id == guild_id,
                Character.is_active == True,
                Character.is_dead == False,
            )
        )
        return [c.name for c in result.scalars().all()]


@session_group.command(name="start", description="Mark the start of a play session (GM only)")
@app_commands.describe(title="Optional title for this session")
async def session_start(interaction: discord.Interaction, title: str | None = None):
    if not await gm_only(interaction):
        return

    await interaction.response.defer()
    chars = await _get_characters_at_location(interaction.guild_id)
    async with get_db() as db:
        log = SessionLog(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            title=title or f"Session {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
            started_at=datetime.utcnow(),
            characters_present=chars,
            created_by=interaction.user.id,
        )
        db.add(log)
        await db.flush()
        session_id = log.id

    embed = discord.Embed(
        title="📜 Session Started",
        description=f"**{title or 'Untitled Session'}** has begun!\n\n"
                    f"**Characters present:** {', '.join(chars) if chars else 'None yet'}\n"
                    f"Use `/session end` when the session is over.",
        color=0x22C55E,
    )
    embed.set_footer(text=f"Session ID: {session_id} • LoreForge")
    msg = await interaction.followup.send(embed=embed)
    try:
        await msg.pin()
    except Exception:
        pass


@session_group.command(name="end", description="End the active session and generate a summary (GM only)")
async def session_end(interaction: discord.Interaction):
    if not await gm_only(interaction):
        return

    await interaction.response.defer()
    async with get_db() as db:
        result = await db.execute(
            select(SessionLog).where(
                SessionLog.guild_id == interaction.guild_id,
                SessionLog.channel_id == interaction.channel_id,
                SessionLog.ended_at.is_(None),
            ).order_by(desc(SessionLog.started_at)).limit(1)
        )
        log = result.scalar_one_or_none()

        if not log:
            await interaction.followup.send(
                "No active session found in this channel. Use `/session start` first.",
                ephemeral=True,
            )
            return

        log.ended_at = datetime.utcnow()
        # Stamp in-world date at end
        try:
            from cogs.calendar import get_current_in_world_date
            iwd = await get_current_in_world_date(interaction.guild_id)
            if iwd:
                log.in_world_date = iwd
        except Exception:
            pass
        session_id_closed = log.id

    async with get_db() as db:
        result = await db.execute(select(SessionLog).where(SessionLog.id == session_id_closed))
        log = result.scalar_one_or_none()
    summary_text = log.summary_text if log else None

    embed = discord.Embed(
        title=f"📜 Session Ended — {log.title if log else 'Session'}",
        color=0x6366F1,
    )
    if getattr(log, "in_world_date", None):
        embed.add_field(name="📅 In-World Date", value=log.in_world_date, inline=True)
    embed.add_field(name="⏱️ Duration", value="Active", inline=True)
    embed.add_field(name="⚔️ Combats", value=str(log.combat_count or 0), inline=True)
    embed.add_field(name="📋 Quests", value=str(log.quest_completions or 0), inline=True)
    embed.add_field(name="✨ XP Earned", value=str(log.total_xp or 0), inline=True)
    embed.add_field(name="🎭 Characters", value=", ".join(log.characters_present or []) or "None", inline=False)

    if summary_text:
        embed.add_field(name="📖 Summary", value=summary_text, inline=False)
    elif log.summary_text:
        embed.add_field(name="📖 Summary", value=log.summary_text, inline=False)
    else:
        embed.add_field(name="📖 Summary", value="*No AI summary available — DeepSeek may be offline, or summaries are disabled (`/ai toggle summary`).*", inline=False)

    embed.set_footer(text="LoreForge Session Log")
    await interaction.followup.send(embed=embed)



@session_group.command(name="summary", description="View the summary and notes for the most recent session")
async def session_summary(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    async with get_db() as db:
        result = await db.execute(
            select(SessionLog).where(
                SessionLog.guild_id == interaction.guild_id,
                SessionLog.channel_id == interaction.channel_id,
            ).order_by(desc(SessionLog.started_at)).limit(1)
        )
        log = result.scalar_one_or_none()
        if not log:
            await interaction.followup.send("No sessions found in this channel.", ephemeral=True)
            return
        notes_result = await db.execute(
            select(SessionNote).where(SessionNote.session_id == log.id).order_by(SessionNote.timestamp.asc())
        )
        notes = list(notes_result.scalars().all())

    embed = discord.Embed(title=f"📜 {log.title or f'Session #{log.id}'}", color=0x6366F1)
    embed.add_field(name="Characters", value=", ".join(log.characters_present or []) or "None", inline=False)
    if log.summary_text:
        embed.add_field(name="Summary", value=log.summary_text[:800], inline=False)
    if notes:
        note_lines = [f"• {n.text[:120]}" for n in notes[:10]]
        embed.add_field(name="Notes", value="\n".join(note_lines), inline=False)
    embed.set_footer(text=f"Session #{log.id}  •  LoreForge")
    await interaction.followup.send(embed=embed, ephemeral=True)


@session_group.command(name="log", description="View all past sessions (paginated)")
async def session_log(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    async with get_db() as db:
        result = await db.execute(
            select(SessionLog).where(
                SessionLog.guild_id == interaction.guild_id,
            ).order_by(desc(SessionLog.started_at)).limit(50)
        )
        sessions = list(result.scalars().all())

    if not sessions:
        await interaction.followup.send("No sessions recorded yet.", ephemeral=True)
        return

    class SessionLogView(discord.ui.View):
        def __init__(self, pages, page=0):
            super().__init__(timeout=300)
            self.page = page
            self.pages = pages
            self._update_buttons()

        def _update_buttons(self):
            self.prev_btn.disabled = self.page == 0
            self.next_btn.disabled = self.page >= len(self.pages) - 1

        def _build_embed(self):
            s = self.pages[self.page]
            embed = discord.Embed(
                title=f"📜 Session Log — Page {self.page + 1}/{len(self.pages)}",
                color=0x6366F1,
            )
            embed.add_field(name="Title", value=s.title or "Untitled", inline=True)
            embed.add_field(name="Started", value=f"<t:{int(s.started_at.timestamp())}:f>" if s.started_at else "Unknown", inline=True)
            if s.ended_at:
                embed.add_field(name="Ended", value=f"<t:{int(s.ended_at.timestamp())}:f>", inline=True)
                duration_seconds = (s.ended_at - s.started_at).total_seconds()
                hours = int(duration_seconds // 3600)
                minutes = int((duration_seconds % 3600) // 60)
                embed.add_field(name="Duration", value=f"{hours}h {minutes}m", inline=True)
            embed.add_field(name="Characters", value=", ".join(s.characters_present or []) or "None", inline=False)
            embed.add_field(name="⚔️", value=str(s.combat_count or 0), inline=True)
            embed.add_field(name="📋", value=str(s.quest_completions or 0), inline=True)
            embed.add_field(name="✨ XP", value=str(s.total_xp or 0), inline=True)
            if s.summary_text:
                embed.add_field(name="Summary", value=s.summary_text[:500], inline=False)
            return embed

        @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
        async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self._build_embed(), view=self)

        @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
        async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self._build_embed(), view=self)

    view = SessionLogView(pages=sessions[:20], page=0)
    await interaction.followup.send(embed=view._build_embed(), view=view, ephemeral=True)


# ── Session autocomplete ──────────────────────────────────────────────────────

async def _session_autocomplete(interaction: discord.Interaction, current: str):
    async with get_db() as db:
        result = await db.execute(
            select(SessionLog).where(
                SessionLog.guild_id == interaction.guild_id,
                SessionLog.title.ilike(f"%{current}%"),
            ).order_by(desc(SessionLog.started_at)).limit(25)
        )
        sessions = result.scalars().all()
    return [
        app_commands.Choice(name=f"#{s.id} {s.title or 'Untitled'}", value=s.id)
        for s in sessions
    ]


# ── /session note ──────────────────────────────────────────────────────────────

@session_group.command(name="note", description="Add a note to the current or a specific session")
@app_commands.describe(text="Note text", session_id="Session ID (omit for current session)")
async def session_note(interaction: discord.Interaction, text: str, session_id: int = None):
    async with get_db() as db:
        if session_id:
            result = await db.execute(
                select(SessionLog).where(
                    SessionLog.id == session_id,
                    SessionLog.guild_id == interaction.guild_id,
                )
            )
            log = result.scalar_one_or_none()
        else:
            result = await db.execute(
                select(SessionLog).where(
                    SessionLog.guild_id == interaction.guild_id,
                    SessionLog.ended_at.is_(None),
                ).order_by(desc(SessionLog.started_at)).limit(1)
            )
            log = result.scalar_one_or_none()

        if not log:
            await interaction.response.send_message("No active session found. Start one with `/session start` or specify a session ID.", ephemeral=True)
            return

        db.add(SessionNote(
            session_id=log.id,
            guild_id=interaction.guild_id,
            author_user_id=interaction.user.id,
            text=text,
        ))

    await interaction.response.send_message(
        f"📝 Note added to **{log.title or f'Session #{log.id}'}**.", ephemeral=True
    )


# ── /session recap ─────────────────────────────────────────────────────────────

@session_group.command(name="recap", description="View the full recap for a session")
@app_commands.describe(session_id="Session ID")
@app_commands.autocomplete(session_id=_session_autocomplete)
async def session_recap(interaction: discord.Interaction, session_id: int):
    async with get_db() as db:
        result = await db.execute(
            select(SessionLog).where(
                SessionLog.id == session_id,
                SessionLog.guild_id == interaction.guild_id,
            )
        )
        log = result.scalar_one_or_none()
        if not log:
            await interaction.response.send_message("Session not found.", ephemeral=True)
            return

        notes_result = await db.execute(
            select(SessionNote)
            .where(SessionNote.session_id == log.id)
            .order_by(SessionNote.timestamp.asc())
        )
        notes = list(notes_result.scalars().all())

    embed = discord.Embed(
        title=f"📜 Recap — {log.title or f'Session #{log.id}'}",
        description=log.summary_text or "*No summary written. Add one with `/session summary`.*",
        color=0x6366F1,
    )
    if log.started_at:
        embed.add_field(name="Date", value=f"<t:{int(log.started_at.timestamp())}:D>", inline=True)
    embed.add_field(name="Characters", value=", ".join(log.characters_present or []) or "None", inline=False)
    if notes:
        note_lines = [f"`<t:{int(n.timestamp.timestamp())}:t>` {n.text[:120]}" for n in notes[:15]]
        embed.add_field(name="Notes", value="\n".join(note_lines), inline=False)
    embed.set_footer(text=f"Session #{log.id}  •  LoreForge")
    await interaction.response.send_message(embed=embed)


# ── /session characters ────────────────────────────────────────────────────────

@session_group.command(name="characters", description="View which characters were present in a session")
@app_commands.describe(session_id="Session ID")
@app_commands.autocomplete(session_id=_session_autocomplete)
async def session_characters(interaction: discord.Interaction, session_id: int):
    async with get_db() as db:
        result = await db.execute(
            select(SessionLog).where(
                SessionLog.id == session_id,
                SessionLog.guild_id == interaction.guild_id,
            )
        )
        log = result.scalar_one_or_none()

    if not log:
        await interaction.response.send_message("Session not found.", ephemeral=True)
        return

    chars = log.characters_present or []
    embed = discord.Embed(
        title=f"🎭 {log.title or f'Session #{log.id}'} — Characters",
        description="\n".join(f"• {c}" for c in chars) if chars else "*No characters recorded.*",
        color=0x6366F1,
    )
    await interaction.response.send_message(embed=embed)


# ── /session pin ──────────────────────────────────────────────────────────────

@session_group.command(name="pin", description="Post and pin a session recap embed in this channel (GM only)")
@app_commands.describe(session_id="Session ID")
@app_commands.autocomplete(session_id=_session_autocomplete)
async def session_pin(interaction: discord.Interaction, session_id: int):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can pin sessions.", ephemeral=True)
        return

    async with get_db() as db:
        result = await db.execute(
            select(SessionLog).where(
                SessionLog.id == session_id,
                SessionLog.guild_id == interaction.guild_id,
            )
        )
        log = result.scalar_one_or_none()
        if not log:
            await interaction.response.send_message("Session not found.", ephemeral=True)
            return
        notes_result = await db.execute(
            select(SessionNote).where(SessionNote.session_id == log.id).order_by(SessionNote.timestamp.asc())
        )
        notes = list(notes_result.scalars().all())

    embed = discord.Embed(
        title=f"📌 {log.title or f'Session #{log.id}'}",
        description=log.summary_text or "*No summary written.*",
        color=0x8B5CF6,
    )
    if log.started_at:
        embed.add_field(name="Date", value=f"<t:{int(log.started_at.timestamp())}:D>", inline=True)
    embed.add_field(name="Characters", value=", ".join(log.characters_present or []) or "None", inline=False)
    if notes:
        note_lines = [f"• {n.text[:100]}" for n in notes[:8]]
        embed.add_field(name="Notes", value="\n".join(note_lines), inline=False)
    embed.set_footer(text=f"Session #{log.id}  •  LoreForge")

    await interaction.response.send_message("📌 Pinning session recap…", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    try:
        await msg.pin()
    except Exception:
        pass


# ── /session link-timeline ────────────────────────────────────────────────────

@session_group.command(name="link-timeline", description="Link a session to a timeline event (GM only)")
@app_commands.describe(session_id="Session ID", timeline_event_id="Timeline event ID")
@app_commands.autocomplete(session_id=_session_autocomplete)
async def session_link_timeline(
    interaction: discord.Interaction,
    session_id: int,
    timeline_event_id: int,
):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can link sessions to timeline.", ephemeral=True)
        return

    async with get_db() as db:
        session_r = await db.execute(
            select(SessionLog).where(SessionLog.id == session_id, SessionLog.guild_id == interaction.guild_id)
        )
        log = session_r.scalar_one_or_none()
        timeline_r = await db.execute(
            select(TimelineEvent).where(TimelineEvent.id == timeline_event_id, TimelineEvent.guild_id == interaction.guild_id)
        )
        event = timeline_r.scalar_one_or_none()

        if not log:
            await interaction.response.send_message("Session not found.", ephemeral=True)
            return
        if not event:
            await interaction.response.send_message("Timeline event not found.", ephemeral=True)
            return

        # Store the link in the SessionLog's notes (no dedicated table for session-timeline, use a session note)
        db.add(SessionNote(
            session_id=log.id,
            guild_id=interaction.guild_id,
            author_user_id=interaction.user.id,
            text=f"[Timeline Link] → {event.title} (Event #{event.id})",
        ))

    await interaction.response.send_message(
        f"🔗 Session **{log.title or f'#{log.id}'}** linked to timeline event **{event.title}**.",
        ephemeral=True,
    )


class SessionsCog(commands.Cog, name="Sessions"):
    def __init__(self, bot):
        self.bot = bot
        bot.tree.add_command(session_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("session")


async def setup(bot):
    await bot.add_cog(SessionsCog(bot))
