"""
/world recap — catch-up embed: last sessions, timeline events, quests, in-world date.
"""

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, func
from database.session import get_db
from database.models import SessionLog, TimelineEvent, Quest, PlayerQuest, Character

world_group = app_commands.Group(name="world", description="World overview commands")


@world_group.command(name="recap", description="Catch up on everything: sessions, timeline, quests, current date")
async def world_recap(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    await interaction.response.defer()

    guild_id = interaction.guild_id

    # Gather all data in one session context
    async with get_db() as db:
        # Last 3 sessions
        session_result = await db.execute(
            select(SessionLog)
            .where(SessionLog.guild_id == guild_id, SessionLog.ended_at.isnot(None))
            .order_by(SessionLog.ended_at.desc())
            .limit(3)
        )
        sessions = list(session_result.scalars().all())

        # Last 5 curated timeline events
        timeline_result = await db.execute(
            select(TimelineEvent)
            .where(TimelineEvent.guild_id == guild_id)
            .order_by(TimelineEvent.created_at.desc())
            .limit(5)
        )
        timeline_events = list(timeline_result.scalars().all())

        # Active quest count
        active_quests_count = (await db.execute(
            select(func.count()).select_from(Quest).where(
                Quest.guild_id == guild_id, Quest.is_active == True
            )
        )).scalar() or 0

    # In-world date
    iwd = None
    try:
        from cogs.calendar import get_current_in_world_date
        iwd = await get_current_in_world_date(guild_id)
    except Exception:
        pass

    embed = discord.Embed(
        title="🌍 World Recap",
        description="Everything you need to catch up — sessions, history, quests, and the current date.",
        color=0x6366F1,
    )

    # In-world date
    embed.add_field(
        name="📅 Current In-World Date",
        value=iwd or "*Not set — GMs can use `/calendar set` to configure it*",
        inline=False,
    )

    # Last sessions
    if sessions:
        lines = []
        for s in sessions:
            ts = int(s.ended_at.timestamp()) if s.ended_at else 0
            title = s.title or f"Session #{s.id}"
            iwd_tag = f" — *{s.in_world_date}*" if getattr(s, "in_world_date", None) else ""
            summary = (s.summary_text or "No summary.").replace("\n", " ")[:150]
            lines.append(f"**{title}** (<t:{ts}:D>{iwd_tag})\n> {summary}")
        embed.add_field(
            name="📋 Recent Sessions",
            value="\n\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(name="📋 Recent Sessions", value="*No sessions logged yet.*", inline=False)

    # Timeline events
    if timeline_events:
        lines = []
        for e in reversed(timeline_events):
            era_tag = f" `[{e.era}]`" if e.era else ""
            desc = (e.description or "").replace("\n", " ")[:100]
            lines.append(f"**{e.title}**{era_tag}\n> {desc}")
        embed.add_field(
            name="⏳ Recent Timeline Events",
            value="\n\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(name="⏳ Recent Timeline Events", value="*No timeline events yet.*", inline=False)

    # Active quests
    embed.add_field(
        name="📜 Active Quests",
        value=f"**{active_quests_count}** quest{'s' if active_quests_count != 1 else ''} available  •  Use `/quest list` to browse them.",
        inline=False,
    )

    embed.set_footer(text="LoreForge World Recap  •  Use /session log, /timeline list, or /quest list for full details")
    await interaction.followup.send(embed=embed)


class WorldRecapCog(commands.Cog, name="WorldRecap"):
    def __init__(self, bot):
        self.bot = bot
        bot.tree.add_command(world_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("world")


async def setup(bot):
    await bot.add_cog(WorldRecapCog(bot))
