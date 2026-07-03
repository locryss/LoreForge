import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, delete, func, or_
from database.session import get_db
from database.models import LoreEntry, GuildConfig, LoreLink, LorePlayerNote
from services.utils import is_gm
import random

lore_group = app_commands.Group(name="lore", description="Browse and manage world lore")


async def _lore_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete for lore entries visible to the user."""
    async with get_db() as db:
        query = select(LoreEntry).where(
            LoreEntry.guild_id == interaction.guild_id,
            LoreEntry.title.ilike(f"%{current}%"),
        )
        # Non-GM users only see public entries
        from services.utils import is_gm
        if not await is_gm(interaction):
            query = query.where(LoreEntry.visibility == "public")
        query = query.limit(25)
        result = await db.execute(query)
        entries = result.scalars().all()
    return [
        app_commands.Choice(name=f"{e.title} ({e.category})"[:100], value=e.title)
        for e in entries
    ][:25]


class LoreAddModal(discord.ui.Modal, title="Add Lore Entry"):
    content = discord.ui.TextInput(label="Content", style=discord.TextStyle.long, required=True)
    category = discord.ui.TextInput(label="Category (e.g., history, faction, location)", required=False, max_length=30)
    tags = discord.ui.TextInput(label="Tags (comma-separated)", required=False, max_length=200)
    image_url = discord.ui.TextInput(label="Image URL (optional)", required=False, max_length=500)

    def __init__(self, title_name: str):
        super().__init__()
        self._title = title_name

    async def on_submit(self, interaction: discord.Interaction):
        tags_list = [t.strip() for t in self.tags.value.split(",") if t.strip()] if self.tags.value else []

        async with get_db() as db:
            db.add(LoreEntry(
                guild_id=interaction.guild_id,
                title=self._title,
                content=self.content.value,
                category=self.category.value or "lore",
                tags=tags_list,
                is_rumor=False,
                visibility="public",
                image_url=self.image_url.value or None,
                created_by=interaction.user.id,
            ))

        embed = discord.Embed(
            title=f"📚 {self._title}",
            description=f"Added to **{self.category.value or 'lore'}** category.",
            color=0xA855F7,
        )
        await interaction.response.send_message(embed=embed)


class LoreEditModal(discord.ui.Modal, title="Edit Lore Entry"):
    content = discord.ui.TextInput(label="Content", style=discord.TextStyle.long, required=True)
    category = discord.ui.TextInput(label="Category", required=False, max_length=30)

    def __init__(self, existing: LoreEntry):
        super().__init__()
        self._entry_id = existing.id
        self.content.default = existing.content
        self.category.default = existing.category

    async def on_submit(self, interaction: discord.Interaction):
        async with get_db() as db:
            result = await db.execute(select(LoreEntry).where(LoreEntry.id == self._entry_id))
            entry = result.scalar_one_or_none()
            if entry:
                entry.content = self.content.value
                entry.category = self.category.value or "lore"
        await interaction.response.send_message("✅ Lore entry updated.", ephemeral=True)


@lore_group.command(name="add", description="Add a new lore entry (GM only)")
@app_commands.describe(title="Title of the lore entry")
async def lore_add(interaction: discord.Interaction, title: str):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can add lore.", ephemeral=True)
        return
    await interaction.response.send_modal(LoreAddModal(title))


@lore_group.command(name="edit", description="Edit an existing lore entry (GM only)")
@app_commands.describe(title="Title of the lore entry to edit")
@app_commands.autocomplete(title=_lore_autocomplete)
async def lore_edit(interaction: discord.Interaction, title: str):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can edit lore.", ephemeral=True)
        return
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.title.ilike(title),
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            await interaction.response.send_message("Lore entry not found.", ephemeral=True)
            return
    await interaction.response.send_modal(LoreEditModal(entry))


@lore_group.command(name="delete", description="Delete a lore entry (GM only)")
@app_commands.describe(title="Title of the lore entry to delete")
@app_commands.autocomplete(title=_lore_autocomplete)
async def lore_delete(interaction: discord.Interaction, title: str):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can delete lore.", ephemeral=True)
        return
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.title.ilike(title),
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            await interaction.response.send_message("Lore entry not found.", ephemeral=True)
            return
        await db.delete(entry)
    await interaction.response.send_message(f"🗑️ Deleted lore entry **{title}**.")


# ── Lore display helpers ──────────────────────────────────────────────────────

CATEGORY_EMOJI: dict[str, str] = {
    "history":      "📜",
    "lore":         "📖",
    "faction":      "⚔️",
    "location":     "🗺️",
    "creature":     "🐉",
    "character":    "👤",
    "item":         "💎",
    "religion":     "🙏",
    "event":        "⚡",
    "organization": "🏛️",
    "magic":        "✨",
    "secret":       "🔒",
}

CATEGORY_COLOR: dict[str, int] = {
    "history":      0xB8860B,
    "lore":         0xA855F7,
    "faction":      0xEF4444,
    "location":     0x22C55E,
    "creature":     0xF97316,
    "character":    0x6366F1,
    "item":         0xF1C40F,
    "religion":     0xE879F9,
    "event":        0x06B6D4,
    "organization": 0x64748B,
    "magic":        0x8B5CF6,
    "secret":       0x374151,
}

_DEFAULT_LORE_COLOR = 0xA855F7


def _cat_emoji(category: str) -> str:
    return CATEGORY_EMOJI.get((category or "lore").lower(), "📖")


def _cat_color(category: str) -> int:
    return CATEGORY_COLOR.get((category or "lore").lower(), _DEFAULT_LORE_COLOR)


def _tag_chips(tags: list) -> str:
    if not tags:
        return "*none*"
    return "  ".join(f"`{t}`" for t in tags[:8])


def _build_lore_card(entry: "LoreEntry", linked: list | None = None) -> discord.Embed:
    emoji = _cat_emoji(entry.category)
    color = _cat_color(entry.category)
    label = (entry.category or "lore").title()

    content = entry.content or "*No content.*"
    truncated = len(content) > 2000
    desc = content[:2000] + ("\n*…content truncated*" if truncated else "")

    embed = discord.Embed(
        title=f"{emoji}  {entry.title}",
        description=desc,
        color=color,
    )

    embed.add_field(name="Category", value=f"{emoji} {label}", inline=True)
    embed.add_field(name="Tags", value=_tag_chips(entry.tags or []), inline=True)

    status_parts = []
    if entry.is_canon:
        status_parts.append("✅ Canon")
    if entry.is_rumor:
        status_parts.append("💬 Rumor")
    if status_parts:
        embed.add_field(name="Status", value="  ·  ".join(status_parts), inline=True)

    if linked:
        linked_str = "  ·  ".join(
            f"{_cat_emoji(e.category)} {e.title}" for e in linked[:6]
        )
        embed.add_field(name="🔗 Related Entries", value=linked_str, inline=False)

    if entry.image_url:
        embed.set_image(url=entry.image_url)

    embed.set_footer(text=f"LoreForge Codex  ·  #{entry.id}")
    return embed


def _build_index_embed(
    entries: list,
    page: int,
    per_page: int,
    total: int,
    title_prefix: str = "",
) -> discord.Embed:
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = page * per_page
    page_entries = entries[start : start + per_page]

    # Group current page entries by category
    grouped: dict[str, list] = {}
    for e in page_entries:
        cat = (e.category or "lore").lower()
        grouped.setdefault(cat, []).append(e)

    embed = discord.Embed(
        title=f"📚 {title_prefix or 'Lore Codex'}",
        color=_DEFAULT_LORE_COLOR,
    )
    embed.description = f"**{total}** entries  ·  Page {page + 1}/{total_pages}\n​"

    for cat, cat_entries in grouped.items():
        emoji = _cat_emoji(cat)
        label = cat.title()
        lines = []
        for e in cat_entries:
            tags = ("  " + "  ".join(f"`{t}`" for t in (e.tags or [])[:3])) if e.tags else ""
            lines.append(f"**{e.title}**{tags}")
        embed.add_field(
            name=f"{emoji}  {label}",
            value="\n".join(lines),
            inline=False,
        )

    embed.set_footer(text=f"Use the menu below to read any entry  ·  LoreForge Codex")
    return embed


class LoreIndexView(discord.ui.View):
    PER_PAGE = 8

    def __init__(self, entries: list, page: int = 0, title_prefix: str = ""):
        super().__init__(timeout=300)
        self.entries = entries
        self.page = page
        self.total = len(entries)
        self.title_prefix = title_prefix
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        start = self.page * self.PER_PAGE
        page_entries = self.entries[start : start + self.PER_PAGE]
        total_pages = max(1, (self.total + self.PER_PAGE - 1) // self.PER_PAGE)

        # Select menu — open any entry on this page
        if page_entries:
            select = discord.ui.Select(
                placeholder="📖 Select an entry to read it…",
                options=[
                    discord.SelectOption(
                        label=e.title[:100],
                        value=str(e.id),
                        emoji=_cat_emoji(e.category),
                        description=(f"{e.category.title()}  ·  {', '.join((e.tags or [])[:2])}"[:100]) if e.tags else e.category.title()[:100],
                    )
                    for e in page_entries
                ],
            )
            select.callback = self._on_select
            self.add_item(select)

        # Nav buttons
        prev_btn = discord.ui.Button(
            label="◀  Prev",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page <= 0),
            row=1,
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)

        page_btn = discord.ui.Button(
            label=f"Page {self.page + 1} / {total_pages}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=1,
        )
        self.add_item(page_btn)

        next_btn = discord.ui.Button(
            label="Next  ▶",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page >= total_pages - 1),
            row=1,
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

    async def _on_select(self, interaction: discord.Interaction):
        entry_id = int(interaction.data["values"][0])
        async with get_db() as db:
            result = await db.execute(select(LoreEntry).where(LoreEntry.id == entry_id))
            entry = result.scalar_one_or_none()
            if not entry:
                await interaction.response.send_message("Entry not found.", ephemeral=True)
                return
            links_result = await db.execute(
                select(LoreLink).where(
                    LoreLink.guild_id == interaction.guild_id,
                    or_(LoreLink.entry_id_a == entry.id, LoreLink.entry_id_b == entry.id),
                )
            )
            links = list(links_result.scalars().all())
            linked_ids = [
                lnk.entry_id_b if lnk.entry_id_a == entry.id else lnk.entry_id_a
                for lnk in links
            ]
            linked_entries = []
            if linked_ids:
                lr = await db.execute(select(LoreEntry).where(LoreEntry.id.in_(linked_ids)))
                linked_entries = list(lr.scalars().all())
        embed = _build_lore_card(entry, linked=linked_entries)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._rebuild()
        embed = _build_index_embed(self.entries, self.page, self.PER_PAGE, self.total, self.title_prefix)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _next(self, interaction: discord.Interaction):
        max_page = max(0, (self.total + self.PER_PAGE - 1) // self.PER_PAGE - 1)
        self.page = min(max_page, self.page + 1)
        self._rebuild()
        embed = _build_index_embed(self.entries, self.page, self.PER_PAGE, self.total, self.title_prefix)
        await interaction.response.edit_message(embed=embed, view=self)


@lore_group.command(name="search", description="Search lore entries by title or content")
@app_commands.describe(query="Search term")
async def lore_search(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    user_is_gm = await is_gm(interaction)
    async with get_db() as db:
        q = f"%{query}%"
        base = select(LoreEntry).where(
            LoreEntry.guild_id == interaction.guild_id,
            or_(LoreEntry.title.ilike(q), LoreEntry.content.ilike(q)),
        )
        if not user_is_gm:
            base = base.where(LoreEntry.visibility == "public")
        result = await db.execute(base.order_by(LoreEntry.title).limit(40))
        entries = list(result.scalars().all())

    if not entries:
        await interaction.followup.send(f"No lore entries match **{query}**.", ephemeral=True)
        return

    if len(entries) == 1:
        embed = _build_lore_card(entries[0])
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    embed = _build_index_embed(entries, 0, LoreIndexView.PER_PAGE, len(entries), f"Search: {query}")
    view = LoreIndexView(entries, title_prefix=f"Search: {query}")
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@lore_group.command(name="view", description="View a lore entry in full")
@app_commands.describe(title="Title of the lore entry")
@app_commands.autocomplete(title=_lore_autocomplete)
async def lore_view(interaction: discord.Interaction, title: str):
    user_is_gm = await is_gm(interaction)
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.title.ilike(title),
            )
        )
        entry = result.scalar_one_or_none()

    if not entry:
        await interaction.response.send_message("Lore entry not found.", ephemeral=True)
        return
    if entry.visibility not in ("public",) and not user_is_gm:
        await interaction.response.send_message("Lore entry not found.", ephemeral=True)
        return

    async with get_db() as db:
        links_result = await db.execute(
            select(LoreLink).where(
                LoreLink.guild_id == interaction.guild_id,
                or_(LoreLink.entry_id_a == entry.id, LoreLink.entry_id_b == entry.id),
            )
        )
        links = list(links_result.scalars().all())
        linked_ids = [
            lnk.entry_id_b if lnk.entry_id_a == entry.id else lnk.entry_id_a
            for lnk in links
        ]
        linked_entries = []
        if linked_ids:
            lr = await db.execute(select(LoreEntry).where(LoreEntry.id.in_(linked_ids)))
            linked_entries = list(lr.scalars().all())

    embed = _build_lore_card(entry, linked=linked_entries)
    await interaction.response.send_message(embed=embed)


@lore_group.command(name="list", description="Browse the world lore codex")
@app_commands.describe(category="Filter by category (optional)")
async def lore_list(interaction: discord.Interaction, category: str = None):
    await interaction.response.defer()
    user_is_gm = await is_gm(interaction)
    async with get_db() as db:
        query = select(LoreEntry).where(LoreEntry.guild_id == interaction.guild_id)
        if not user_is_gm:
            query = query.where(LoreEntry.visibility == "public")
        if category:
            query = query.where(LoreEntry.category.ilike(f"%{category}%"))
        query = query.order_by(LoreEntry.category, LoreEntry.title)
        result = await db.execute(query)
        entries = list(result.scalars().all())

    if not entries:
        await interaction.followup.send("No lore entries found yet.", ephemeral=True)
        return

    title_prefix = f"Category: {category.title()}" if category else "Lore Codex"
    embed = _build_index_embed(entries, 0, LoreIndexView.PER_PAGE, len(entries), title_prefix)
    view = LoreIndexView(entries, title_prefix=title_prefix)
    await interaction.followup.send(embed=embed, view=view)


@lore_group.command(name="random", description="Get a random lore entry")
async def lore_random(interaction: discord.Interaction):
    async with get_db() as db:
        result = await db.execute(
            select(func.count()).select_from(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.visibility == "public",
            )
        )
        count = result.scalar()
        if count == 0:
            await interaction.response.send_message("No lore entries yet.", ephemeral=True)
            return

        result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.visibility == "public",
            )
        )
        entries = result.scalars().all()
        entry = random.choice(entries)

    embed = _build_lore_card(entry)
    embed.title = f"🎲 Random Entry  ·  {entry.title}"
    await interaction.response.send_message(embed=embed)


# ── Phase 6: Player-Specific Lore Secrets ────────────────────────────────────

@lore_group.command(name="reveal", description="Reveal a lore entry to a specific player (GM only)")
@app_commands.describe(title="Title of the lore entry", user="The player to reveal it to")
@app_commands.autocomplete(title=_lore_autocomplete)
async def lore_reveal(interaction: discord.Interaction, title: str, user: discord.Member):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can reveal lore.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.title.ilike(title),
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            await interaction.followup.send("Lore entry not found.", ephemeral=True)
            return

        from sqlalchemy.orm.attributes import flag_modified
        whitelist = list(entry.visibility_whitelist or [])
        if user.id not in whitelist:
            whitelist.append(user.id)
            entry.visibility_whitelist = whitelist
            flag_modified(entry, "visibility_whitelist")

    # DM the player
    preview = entry.content[:200] + "..." if len(entry.content) > 200 else entry.content
    dm_embed = discord.Embed(
        title="🔓 A Secret Has Been Revealed to You",
        description=f"**{entry.title}**\n\n{preview}",
        color=0xA855F7,
    )
    dm_embed.set_footer(text="This lore is now unlocked for you.")
    try:
        await user.send(embed=dm_embed)
    except discord.Forbidden:
        pass

    await interaction.followup.send(f"✅ **{entry.title}** revealed to {user.mention}.", ephemeral=True)


@lore_group.command(name="hide", description="Hide a lore entry from a specific player (GM only)")
@app_commands.describe(title="Title of the lore entry", user="The player to hide it from")
@app_commands.autocomplete(title=_lore_autocomplete)
async def lore_hide(interaction: discord.Interaction, title: str, user: discord.Member):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can hide lore.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.title.ilike(title),
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            await interaction.followup.send("Lore entry not found.", ephemeral=True)
            return

        from sqlalchemy.orm.attributes import flag_modified
        whitelist = list(entry.visibility_whitelist or [])
        if user.id in whitelist:
            whitelist.remove(user.id)
            entry.visibility_whitelist = whitelist
            flag_modified(entry, "visibility_whitelist")

    await interaction.followup.send(f"❌ **{entry.title}** hidden from {user.mention}.", ephemeral=True)


# ── Phase 6: Player-Written Lore Submissions ──────────────────────────────────

class LoreSubmitModal(discord.ui.Modal, title="Submit Lore Entry"):
    content = discord.ui.TextInput(label="Content", style=discord.TextStyle.long, required=True)
    category = discord.ui.TextInput(label="Category (e.g., history, faction, location)", required=False, max_length=30)

    def __init__(self, title_name: str):
        super().__init__()
        self._title = title_name

    async def on_submit(self, interaction: discord.Interaction):
        async with get_db() as db:
            entry = LoreEntry(
                guild_id=interaction.guild_id,
                title=self._title,
                content=self.content.value,
                category=self.category.value or "lore",
                tags=[],
                is_rumor=False,
                visibility="submitted",
                submitted_by=interaction.user.id,
                created_by=interaction.user.id,
            )
            db.add(entry)
            await db.flush()
            entry_id = entry.id

        # Notify GM channel
        gc_result = await db.execute(
            select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id)
        )
        gc = gc_result.scalar_one_or_none()

        class ApproveDenyView(discord.ui.View):
            def __init__(self, entry_id: int, title: str, submitter_id: int):
                super().__init__(timeout=86400)
                self.entry_id = entry_id
                self.title = title
                self.submitter_id = submitter_id

            @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
            async def approve(self, interaction2: discord.Interaction, btn: discord.ui.Button):
                if not await is_gm(interaction2):
                    await interaction2.response.send_message("Only GMs can approve.", ephemeral=True)
                    return
                async with get_db() as db2:
                    e = (await db2.execute(select(LoreEntry).where(LoreEntry.id == self.entry_id))).scalar_one_or_none()
                    if e:
                        e.visibility = "public"
                from services.achievements import grant_achievement
                await grant_achievement(interaction2.client, 0, interaction2.guild_id, "scribe", channel=interaction2.channel)
                try:
                    submitter = await interaction2.guild.fetch_member(self.submitter_id)
                    if submitter:
                        await submitter.send(f"✅ Your lore entry **{self.title}** has been approved! +100 XP")
                except Exception:
                    pass
                await interaction2.response.edit_message(content=f"✅ **{self.title}** approved!", view=None)

            @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger)
            async def deny(self, interaction2: discord.Interaction, btn: discord.ui.Button):
                if not await is_gm(interaction2):
                    await interaction2.response.send_message("Only GMs can deny.", ephemeral=True)
                    return
                async with get_db() as db2:
                    e = (await db2.execute(select(LoreEntry).where(LoreEntry.id == self.entry_id))).scalar_one_or_none()
                    if e:
                        await db2.delete(e)
                try:
                    submitter = await interaction2.guild.fetch_member(self.submitter_id)
                    if submitter:
                        await submitter.send(f"❌ Your lore entry **{self.title}** was not approved by the GM.")
                except Exception:
                    pass
                await interaction2.response.edit_message(content=f"❌ **{self.title}** denied and removed.", view=None)

        gm_embed = discord.Embed(
            title="📝 New Lore Submission",
            description=f"**{self._title}**\n\n{self.content.value[:500]}",
            color=0xF59E0B,
        )
        gm_embed.add_field(name="Submitted by", value=interaction.user.mention, inline=True)
        gm_embed.add_field(name="Category", value=self.category.value or "lore", inline=True)
        gm_embed.set_footer(text=f"Entry ID: {entry_id}")

        if gc and gc.gm_channel_id:
            gm_channel = interaction.guild.get_channel(gc.gm_channel_id)
            if gm_channel:
                await gm_channel.send(embed=gm_embed, view=ApproveDenyView(entry_id, self._title, interaction.user.id))

        await interaction.response.send_message(
            f"✅ Your lore entry **{self._title}** has been submitted for GM review.",
            ephemeral=True,
        )


@lore_group.command(name="submit", description="Submit a player-written lore entry for GM review")
@app_commands.describe(title="Title of your lore entry")
async def lore_submit(interaction: discord.Interaction, title: str):
    """Submit player-written lore for GM approval."""
    await interaction.response.send_modal(LoreSubmitModal(title))


# ── Template-based lore creation ─────────────────────────────────────────────

_TEMPLATE_TYPES = ["character", "item", "creature", "religion", "event", "organization", "magic"]


async def _template_type_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=t, value=t) for t in _TEMPLATE_TYPES if current.lower() in t.lower()]


@lore_group.command(name="add-template", description="Create a lore entry using a structured template")
@app_commands.describe(template_type="Template type", title="Entry title")
@app_commands.autocomplete(template_type=_template_type_autocomplete)
async def lore_add_template(interaction: discord.Interaction, template_type: str, title: str):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can add lore.", ephemeral=True)
        return

    from services.lore_templates import TEMPLATES

    template = TEMPLATES.get(template_type)
    if not template:
        await interaction.response.send_message(f"Unknown template type: {template_type}", ephemeral=True)
        return

    # Build modal with template fields
    class TemplateModal(discord.ui.Modal, title=f"Lore: {title[:45]}"):
        def __init__(self, fields: list[str]):
            super().__init__()
            self.inputs = {}
            for field in fields:
                label = field.replace("_", " ").title()[:45]
                inp = discord.ui.TextInput(
                    label=label,
                    required=True,
                    max_length=1024,
                    style=discord.TextStyle.long if field in ("backstory", "lore_text", "stats_block", "secrets") else discord.TextStyle.short,
                )
                self.inputs[field] = inp
                self.add_item(inp)

        async def on_submit(self, modal_interaction: discord.Interaction):
            field_data = {k: v.value for k, v in self.inputs.items()}
            async with get_db() as db:
                import json
                entry = LoreEntry(
                    guild_id=modal_interaction.guild_id,
                    title=title,
                    content=json.dumps(field_data),
                    category=template_type,
                    tags=[template_type],
                    is_rumor=False,
                    visibility="public",
                    created_by=modal_interaction.user.id,
                )
                db.add(entry)
                await db.commit()

            from services.lore_templates import render_template_embed
            embed = render_template_embed(title, template_type, field_data, modal_interaction.user.display_name)
            await modal_interaction.response.send_message(embed=embed)

    await interaction.response.send_modal(TemplateModal(template["fields"]))


# ── /codex command ────────────────────────────────────────────────────────────

@app_commands.command(name="codex", description="Unified world search — lore, NPCs, locations, factions, bestiary, timeline, sessions")
@app_commands.describe(query="Search term")
async def codex_cmd(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    from database.models import NPC, Location, Faction, BossTemplate, TimelineEvent, SessionLog

    q = f"%{query}%"
    async with get_db() as db:
        lore_result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.visibility == "public",
                or_(LoreEntry.title.ilike(q), LoreEntry.content.ilike(q)),
            ).limit(3)
        )
        lore_matches = list(lore_result.scalars().all())

        npc_result = await db.execute(
            select(NPC).where(
                NPC.guild_id == interaction.guild_id,
                NPC.is_dead == False,
                or_(NPC.name.ilike(q), NPC.description.ilike(q)),
            ).limit(3)
        )
        npc_matches = list(npc_result.scalars().all())

        loc_result = await db.execute(
            select(Location).where(
                Location.guild_id == interaction.guild_id,
                Location.is_hidden == False,
                or_(Location.name.ilike(q), Location.description.ilike(q)),
            ).limit(3)
        )
        loc_matches = list(loc_result.scalars().all())

        fac_result = await db.execute(
            select(Faction).where(
                Faction.guild_id == interaction.guild_id,
                or_(Faction.name.ilike(q), Faction.description.ilike(q)),
            ).limit(3)
        )
        fac_matches = list(fac_result.scalars().all())

        boss_result = await db.execute(
            select(BossTemplate).where(
                BossTemplate.guild_id == interaction.guild_id,
                or_(BossTemplate.name.ilike(q), BossTemplate.description.ilike(q)),
            ).limit(3)
        )
        boss_matches = list(boss_result.scalars().all())

        timeline_result = await db.execute(
            select(TimelineEvent).where(
                TimelineEvent.guild_id == interaction.guild_id,
                or_(TimelineEvent.title.ilike(q), TimelineEvent.description.ilike(q)),
            ).limit(3)
        )
        timeline_matches = list(timeline_result.scalars().all())

        session_result = await db.execute(
            select(SessionLog).where(
                SessionLog.guild_id == interaction.guild_id,
                or_(SessionLog.title.ilike(q), SessionLog.summary.ilike(q)),
            ).limit(3)
        )
        session_matches = list(session_result.scalars().all())

    has_any = any([lore_matches, npc_matches, loc_matches, fac_matches, boss_matches, timeline_matches, session_matches])
    if not has_any:
        await interaction.followup.send(f"No world entries found for **{query}**.", ephemeral=True)
        return

    embed = discord.Embed(title=f"📖 Codex: {query}", color=0x8B5CF6)

    if lore_matches:
        lines = [f"**{e.title}** ({e.category}) — {e.content[:70]}…" for e in lore_matches]
        embed.add_field(name="📚 Lore", value="\n".join(lines), inline=False)
    if npc_matches:
        lines = [f"**{n.name}**{'🕐' if n.temporary else ''} — {(n.description or '')[:70]}…" for n in npc_matches]
        embed.add_field(name="👤 NPCs", value="\n".join(lines), inline=False)
    if loc_matches:
        lines = [f"**{l.name}** — {(l.description or '')[:70]}…" for l in loc_matches]
        embed.add_field(name="🗺️ Locations", value="\n".join(lines), inline=False)
    if fac_matches:
        lines = [f"**{f.name}** — {(f.description or '')[:70]}…" for f in fac_matches]
        embed.add_field(name="🏛️ Factions", value="\n".join(lines), inline=False)
    if boss_matches:
        lines = [f"**{b.name}** — ❤️ {b.hp_max} HP" for b in boss_matches]
        embed.add_field(name="⚔️ Bestiary", value="\n".join(lines), inline=False)
    if timeline_matches:
        lines = [f"**{t.title}** [{t.era or '?'}] — {(t.description or '')[:70]}…" for t in timeline_matches]
        embed.add_field(name="⏳ Timeline", value="\n".join(lines), inline=False)
    if session_matches:
        lines = [f"**{s.title or f'Session #{s.id}'}** — {(s.summary or '')[:70]}…" for s in session_matches]
        embed.add_field(name="📋 Sessions", value="\n".join(lines), inline=False)

    embed.set_footer(text="Use /lore view, /npc view, /timeline list, /session view for full details")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ── /lore tag ─────────────────────────────────────────────────────────────────

@lore_group.command(name="tag", description="Add or remove a tag on a lore entry (GM only)")
@app_commands.describe(title="Lore entry title", tag="Tag to add or remove", remove="Set true to remove the tag")
@app_commands.autocomplete(title=_lore_autocomplete)
async def lore_tag(interaction: discord.Interaction, title: str, tag: str, remove: bool = False):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can tag lore.", ephemeral=True)
        return
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(LoreEntry.guild_id == interaction.guild_id, LoreEntry.title.ilike(title))
        )
        entry = result.scalar_one_or_none()
        if not entry:
            await interaction.response.send_message("Lore entry not found.", ephemeral=True)
            return
        tags = list(entry.tags or [])
        tag_lower = tag.lower()
        if remove:
            tags = [t for t in tags if t.lower() != tag_lower]
            action = "removed from"
        else:
            if tag_lower not in [t.lower() for t in tags]:
                tags.append(tag)
            action = "added to"
        entry.tags = tags
    await interaction.response.send_message(f"🏷️ Tag **{tag}** {action} **{entry.title}**.", ephemeral=True)


# ── /lore filter ──────────────────────────────────────────────────────────────

@lore_group.command(name="filter", description="List lore entries with a specific tag")
@app_commands.describe(tag="Tag to filter by")
async def lore_filter(interaction: discord.Interaction, tag: str):
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.visibility == "public",
            )
        )
        all_entries = result.scalars().all()

    tag_lower = tag.lower()
    matches = [e for e in all_entries if any(t.lower() == tag_lower for t in (e.tags or []))]

    if not matches:
        await interaction.response.send_message(f"No public lore entries tagged **{tag}**.", ephemeral=True)
        return

    embed = _build_index_embed(matches, 0, LoreIndexView.PER_PAGE, len(matches), f"Tag: {tag}")
    view = LoreIndexView(matches, title_prefix=f"Tag: {tag}")
    await interaction.response.send_message(embed=embed, view=view)


# ── /lore link ────────────────────────────────────────────────────────────────

@lore_group.command(name="link", description="Link two lore entries together (GM only)")
@app_commands.describe(title_a="First lore entry", title_b="Second lore entry to link to")
@app_commands.autocomplete(title_a=_lore_autocomplete, title_b=_lore_autocomplete)
async def lore_link(interaction: discord.Interaction, title_a: str, title_b: str):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can link lore.", ephemeral=True)
        return
    async with get_db() as db:
        ra = await db.execute(select(LoreEntry).where(LoreEntry.guild_id == interaction.guild_id, LoreEntry.title.ilike(title_a)))
        entry_a = ra.scalar_one_or_none()
        rb = await db.execute(select(LoreEntry).where(LoreEntry.guild_id == interaction.guild_id, LoreEntry.title.ilike(title_b)))
        entry_b = rb.scalar_one_or_none()
        if not entry_a or not entry_b:
            await interaction.response.send_message("One or both lore entries not found.", ephemeral=True)
            return
        if entry_a.id == entry_b.id:
            await interaction.response.send_message("Cannot link an entry to itself.", ephemeral=True)
            return
        id_a, id_b = sorted([entry_a.id, entry_b.id])
        existing = await db.execute(
            select(LoreLink).where(LoreLink.entry_id_a == id_a, LoreLink.entry_id_b == id_b)
        )
        if existing.scalar_one_or_none():
            await interaction.response.send_message("These entries are already linked.", ephemeral=True)
            return
        db.add(LoreLink(guild_id=interaction.guild_id, entry_id_a=id_a, entry_id_b=id_b, created_by=interaction.user.id))
    await interaction.response.send_message(f"🔗 **{entry_a.title}** ↔ **{entry_b.title}** linked.", ephemeral=True)


# ── /lore linked ──────────────────────────────────────────────────────────────

@lore_group.command(name="linked", description="Show all lore entries linked to a given entry")
@app_commands.describe(title="Lore entry to look up links for")
@app_commands.autocomplete(title=_lore_autocomplete)
async def lore_linked(interaction: discord.Interaction, title: str):
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(LoreEntry.guild_id == interaction.guild_id, LoreEntry.title.ilike(title))
        )
        entry = result.scalar_one_or_none()
        if not entry:
            await interaction.response.send_message("Lore entry not found.", ephemeral=True)
            return

        links_result = await db.execute(
            select(LoreLink).where(
                LoreLink.guild_id == interaction.guild_id,
                or_(LoreLink.entry_id_a == entry.id, LoreLink.entry_id_b == entry.id),
            )
        )
        links = list(links_result.scalars().all())
        if not links:
            await interaction.response.send_message(f"**{entry.title}** has no linked entries yet.", ephemeral=True)
            return

        linked_ids = [
            lnk.entry_id_b if lnk.entry_id_a == entry.id else lnk.entry_id_a
            for lnk in links
        ]
        entries_result = await db.execute(select(LoreEntry).where(LoreEntry.id.in_(linked_ids)))
        linked_entries = list(entries_result.scalars().all())

    embed = _build_index_embed(linked_entries, 0, LoreIndexView.PER_PAGE, len(linked_entries), f"Linked to: {entry.title}")
    view = LoreIndexView(linked_entries, title_prefix=f"Linked to: {entry.title}")
    await interaction.response.send_message(embed=embed, view=view)


# ── /lore note ────────────────────────────────────────────────────────────────

@lore_group.command(name="note", description="Add a personal private note to a lore entry")
@app_commands.describe(title="Lore entry to annotate", note="Your private note (only you can see it)")
@app_commands.autocomplete(title=_lore_autocomplete)
async def lore_note(interaction: discord.Interaction, title: str, note: str):
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(LoreEntry.guild_id == interaction.guild_id, LoreEntry.title.ilike(title))
        )
        entry = result.scalar_one_or_none()
        if not entry:
            await interaction.response.send_message("Lore entry not found.", ephemeral=True)
            return
        existing = await db.execute(
            select(LorePlayerNote).where(
                LorePlayerNote.user_id == interaction.user.id,
                LorePlayerNote.lore_entry_id == entry.id,
            )
        )
        player_note = existing.scalar_one_or_none()
        if player_note:
            player_note.note = note
        else:
            db.add(LorePlayerNote(
                user_id=interaction.user.id,
                lore_entry_id=entry.id,
                guild_id=interaction.guild_id,
                note=note,
            ))
    await interaction.response.send_message(
        f"📝 Your private note on **{entry.title}** saved.", ephemeral=True
    )


# ── /lore pending ─────────────────────────────────────────────────────────────

class _ApproveDenyView(discord.ui.View):
    def __init__(self, entry_id: int, title: str, submitter_id: int):
        super().__init__(timeout=86400)
        self.entry_id = entry_id
        self.title = title
        self.submitter_id = submitter_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await is_gm(interaction):
            await interaction.response.send_message("Only GMs can approve.", ephemeral=True)
            return
        async with get_db() as db:
            r = await db.execute(select(LoreEntry).where(LoreEntry.id == self.entry_id))
            e = r.scalar_one_or_none()
            if e:
                e.visibility = "public"
        try:
            submitter = await interaction.guild.fetch_member(self.submitter_id)
            if submitter:
                await submitter.send(f"✅ Your lore entry **{self.title}** was approved by the GM.")
        except Exception:
            pass
        await interaction.response.edit_message(content=f"✅ **{self.title}** approved!", view=None)
        self.stop()

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await is_gm(interaction):
            await interaction.response.send_message("Only GMs can deny.", ephemeral=True)
            return
        async with get_db() as db:
            r = await db.execute(select(LoreEntry).where(LoreEntry.id == self.entry_id))
            e = r.scalar_one_or_none()
            if e:
                await db.delete(e)
        try:
            submitter = await interaction.guild.fetch_member(self.submitter_id)
            if submitter:
                await submitter.send(f"❌ Your lore entry **{self.title}** was not approved.")
        except Exception:
            pass
        await interaction.response.edit_message(content=f"❌ **{self.title}** denied.", view=None)
        self.stop()


@lore_group.command(name="pending", description="Review pending player lore submissions (GM only)")
async def lore_pending(interaction: discord.Interaction):
    if not await is_gm(interaction):
        await interaction.response.send_message("Only GMs can review pending lore.", ephemeral=True)
        return
    async with get_db() as db:
        result = await db.execute(
            select(LoreEntry).where(
                LoreEntry.guild_id == interaction.guild_id,
                LoreEntry.visibility == "submitted",
            ).order_by(LoreEntry.created_at.asc()).limit(10)
        )
        pending = list(result.scalars().all())

    if not pending:
        await interaction.response.send_message("No pending lore submissions.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    for entry in pending:
        embed = discord.Embed(
            title=f"📝 Pending: {entry.title}",
            description=entry.content[:1500],
            color=0xF59E0B,
        )
        embed.add_field(name="Category", value=entry.category, inline=True)
        submitter_id = entry.submitted_by or entry.created_by
        embed.add_field(name="Submitted by", value=f"<@{submitter_id}>", inline=True)
        await interaction.followup.send(
            embed=embed,
            view=_ApproveDenyView(entry.id, entry.title, submitter_id),
            ephemeral=True,
        )


# ── Expand /codex to include timeline + sessions ──────────────────────────────

# Override codex_cmd — redefine it below the LoreCog setup
# (The original is registered in LoreCog; we'll patch the function body here and leave registration alone)


class LoreCog(commands.Cog, name="Lore"):
    def __init__(self, bot):
        self.bot = bot
        bot.tree.add_command(lore_group)
        bot.tree.add_command(codex_cmd)

    async def cog_unload(self):
        self.bot.tree.remove_command("lore")
        self.bot.tree.remove_command("codex")


async def setup(bot):
    await bot.add_cog(LoreCog(bot))
