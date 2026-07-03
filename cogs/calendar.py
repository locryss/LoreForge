"""
In-World Calendar — GMs track in-world dates.
/calendar view | /calendar advance <days> [note] | /calendar set | /calendar config
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from sqlalchemy import select
from database.session import get_db
from database.models import GuildCalendar, TimelineEvent, SessionLog
from services.utils import is_gm

calendar_group = app_commands.Group(name="calendar", description="In-world calendar")

DEFAULT_MONTHS = [
    "Firstmoon", "Frostfall", "Snowmelt", "Bloomrise",
    "Greentide", "Solarpeak", "Embermoon", "Harvestfall",
    "Duskwind", "Ironrain", "Ashveil", "Yearsend",
]


async def _get_or_create_calendar(db, guild_id: int) -> GuildCalendar:
    result = await db.execute(select(GuildCalendar).where(GuildCalendar.guild_id == guild_id))
    cal = result.scalar_one_or_none()
    if not cal:
        cal = GuildCalendar(guild_id=guild_id)
        db.add(cal)
        await db.flush()
    return cal


def _format_date(cal: GuildCalendar) -> str:
    """Return a human-readable in-world date string."""
    month_names = cal.month_names or DEFAULT_MONTHS
    month_idx = max(0, min(cal.month - 1, len(month_names) - 1))
    month_name = month_names[month_idx] if month_names else f"Month {cal.month}"
    era = f" — {cal.era_name}" if cal.era_name else ""
    return f"Day {cal.day}, {month_name}, Year {cal.year}{era}"


async def get_current_in_world_date(guild_id: int) -> str | None:
    """Utility for other cogs to get the current in-world date string."""
    try:
        async with get_db() as db:
            result = await db.execute(select(GuildCalendar).where(GuildCalendar.guild_id == guild_id))
            cal = result.scalar_one_or_none()
            if not cal:
                return None
            return _format_date(cal)
    except Exception:
        return None


@calendar_group.command(name="view", description="View the current in-world date")
async def calendar_view(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return

    async with get_db() as db:
        cal = await _get_or_create_calendar(db, interaction.guild_id)
        date_str = _format_date(cal)
        month_names = cal.month_names or DEFAULT_MONTHS

    embed = discord.Embed(
        title="📅 In-World Date",
        description=f"**{date_str}**",
        color=0xB8860B,
    )
    embed.add_field(
        name="Calendar Details",
        value=(
            f"📆 {cal.days_per_month} days per month  ·  {cal.months_per_year} months per year\n"
            f"🌍 Total days elapsed: **{cal.total_days_elapsed}**"
        ),
        inline=False,
    )
    if cal.era_name:
        embed.add_field(name="Current Era", value=cal.era_name, inline=True)
    embed.set_footer(text="Use /calendar advance to move time forward  •  LoreForge")
    await interaction.response.send_message(embed=embed)


@calendar_group.command(name="advance", description="GM: Advance the in-world date by a number of days")
@app_commands.describe(days="Number of days to advance", note="Optional note — logged to the timeline")
async def calendar_advance(interaction: discord.Interaction, days: int, note: str | None = None):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can advance the calendar.", ephemeral=True)
        return
    if days < 1 or days > 3650:
        await interaction.response.send_message("Days must be between 1 and 3650.", ephemeral=True)
        return

    await interaction.response.defer()

    async with get_db() as db:
        cal = await _get_or_create_calendar(db, interaction.guild_id)
        old_date = _format_date(cal)

        cal.total_days_elapsed += days
        remaining = days
        while remaining > 0:
            days_left_in_month = cal.days_per_month - cal.day
            if remaining <= days_left_in_month:
                cal.day += remaining
                remaining = 0
            else:
                remaining -= (days_left_in_month + 1)
                cal.day = 1
                cal.month += 1
                if cal.month > cal.months_per_year:
                    cal.month = 1
                    cal.year += 1

        cal.updated_by = interaction.user.id
        cal.updated_at = datetime.utcnow()
        new_date = _format_date(cal)

        # Auto-log to timeline if a note was given
        if note:
            timeline_entry = TimelineEvent(
                guild_id=interaction.guild_id,
                title=f"📅 Time Passes — {new_date}",
                description=f"{days} day{'s' if days != 1 else ''} pass. {note}",
                era=cal.era_name,
                tags=["time", "calendar"],
                created_by=interaction.user.id,
            )
            db.add(timeline_entry)

    embed = discord.Embed(
        title=f"📅 Time Passes — {days} day{'s' if days != 1 else ''}",
        color=0xB8860B,
    )
    embed.add_field(name="Was", value=old_date, inline=True)
    embed.add_field(name="Now", value=f"**{new_date}**", inline=True)
    if note:
        embed.add_field(name="Note (logged to timeline)", value=note, inline=False)
    embed.set_footer(text=f"Advanced by {interaction.user.display_name}  •  LoreForge")
    await interaction.followup.send(embed=embed)


@calendar_group.command(name="set", description="GM: Set the in-world date directly")
@app_commands.describe(year="Year", month="Month (1–12)", day="Day (1–30)")
async def calendar_set(interaction: discord.Interaction, year: int, month: int, day: int):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can set the calendar.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    async with get_db() as db:
        cal = await _get_or_create_calendar(db, interaction.guild_id)
        dpm = cal.days_per_month
        mpy = cal.months_per_year
        if not (1 <= month <= mpy):
            await interaction.followup.send(f"Month must be 1–{mpy}.", ephemeral=True)
            return
        if not (1 <= day <= dpm):
            await interaction.followup.send(f"Day must be 1–{dpm}.", ephemeral=True)
            return
        cal.year = year
        cal.month = month
        cal.day = day
        cal.updated_by = interaction.user.id
        cal.updated_at = datetime.utcnow()
        date_str = _format_date(cal)

    await interaction.followup.send(f"📅 In-world date set to **{date_str}**.", ephemeral=True)


@calendar_group.command(name="config", description="GM: Configure the calendar (era name, month names, days per month)")
async def calendar_config(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("LoreForge only works in a server.", ephemeral=True)
        return
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can configure the calendar.", ephemeral=True)
        return

    class CalendarConfigModal(discord.ui.Modal, title="Calendar Configuration"):
        era_name = discord.ui.TextInput(
            label="Era Name (optional)",
            placeholder="e.g. Age of Ashes, Third Era",
            required=False,
            max_length=100,
        )
        days_per_month = discord.ui.TextInput(
            label="Days Per Month",
            placeholder="Default: 30",
            required=False,
            max_length=3,
        )
        months_per_year = discord.ui.TextInput(
            label="Months Per Year",
            placeholder="Default: 12",
            required=False,
            max_length=2,
        )
        month_names = discord.ui.TextInput(
            label="Month Names (comma-separated, optional)",
            style=discord.TextStyle.paragraph,
            placeholder="Firstmoon, Frostfall, Snowmelt, ... (leave blank for defaults)",
            required=False,
            max_length=500,
        )

        async def on_submit(self, interaction2: discord.Interaction):
            async with get_db() as db:
                cal = await _get_or_create_calendar(db, interaction2.guild_id)
                if self.era_name.value.strip():
                    cal.era_name = self.era_name.value.strip()
                try:
                    dpm = int(self.days_per_month.value.strip())
                    if 1 <= dpm <= 365:
                        cal.days_per_month = dpm
                except (ValueError, AttributeError):
                    pass
                try:
                    mpy = int(self.months_per_year.value.strip())
                    if 1 <= mpy <= 24:
                        cal.months_per_year = mpy
                except (ValueError, AttributeError):
                    pass
                if self.month_names.value.strip():
                    names = [n.strip() for n in self.month_names.value.split(",") if n.strip()]
                    if names:
                        cal.month_names = names

            await interaction2.response.send_message("✅ Calendar configured!", ephemeral=True)

    await interaction.response.send_modal(CalendarConfigModal())


class CalendarCog(commands.Cog, name="Calendar"):
    def __init__(self, bot):
        self.bot = bot
        bot.tree.add_command(calendar_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("calendar")


async def setup(bot):
    await bot.add_cog(CalendarCog(bot))
