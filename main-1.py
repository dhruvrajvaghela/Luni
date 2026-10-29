"""
LUMI — BGMI Esports Discord Bot
Python 3.12 | discord.py 2.x | SQLite | Prefix + slash (hybrid) commands

Setup:
  pip install -r requirements.txt
  Copy .env.example to .env and set DISCORD_TOKEN
  python main.py

Enable MESSAGE CONTENT INTENT in the Discord Developer Portal if using prefix commands.
Never share your bot token.
"""

import os
import sqlite3
import logging
import json
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
PREFIX = os.getenv("COMMAND_PREFIX", "!")
DB_PATH = os.getenv("DATABASE_PATH", "lumi_esports.db")
BRAND = "LUMI ESPORTS"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("lumi-esports")


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db_connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            tag TEXT NOT NULL,
            igl_id INTEGER NOT NULL,
            roster TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(guild_id, tag)
        );
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            slots INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            registered_at TEXT NOT NULL,
            UNIQUE(tournament_id, team_id),
            FOREIGN KEY(tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
            FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS scrims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            map_name TEXT NOT NULL,
            match_time TEXT NOT NULL,
            room_id TEXT DEFAULT '',
            room_password TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'scheduled',
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            event_name TEXT NOT NULL,
            team_tag TEXT NOT NULL,
            placement INTEGER NOT NULL DEFAULT 0,
            finishes INTEGER NOT NULL DEFAULT 0,
            notes TEXT DEFAULT '',
            submitted_by INTEGER NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL UNIQUE,
            opener_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY(guild_id, key)
        );
        CREATE TABLE IF NOT EXISTS scrim_lobbies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            scrim_id INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT 'Lobby 1',
            total_slots INTEGER NOT NULL DEFAULT 25,
            start_slot INTEGER NOT NULL DEFAULT 1,
            start_time TEXT NOT NULL DEFAULT '',
            end_time TEXT NOT NULL DEFAULT '',
            id_pass_time TEXT NOT NULL DEFAULT '',
            match_start TEXT NOT NULL DEFAULT '',
            clean_time TEXT NOT NULL DEFAULT '',
            map_name TEXT NOT NULL DEFAULT '',
            id_pass_channel_id INTEGER,
            assign_role_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(scrim_id) REFERENCES scrims(id) ON DELETE CASCADE
        );
        """)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: str, limit: int = 100):
    return " ".join(value.strip().split())[:limit]


def is_staff():
    async def predicate(ctx):
        return bool(ctx.guild and (
            ctx.author.guild_permissions.administrator
            or ctx.author.guild_permissions.manage_guild
        ))
    return commands.check(predicate)


def embed(title: str, description: str = "", color=discord.Color.from_rgb(42, 117, 255)):
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    e.set_footer(text=BRAND)
    return e



class TeamRegisterModal(discord.ui.Modal, title="Register BGMI Team"):
    team_name = discord.ui.TextInput(label="Team name", max_length=70, placeholder="e.g. Lumi Titans")
    tag = discord.ui.TextInput(label="Team tag (2–8 characters)", max_length=8, placeholder="LUMI")
    roster = discord.ui.TextInput(label="Roster / player names", style=discord.TextStyle.paragraph,
                                  required=False, max_length=400, placeholder="Player1, Player2, Player3, Player4")

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        team_name = clean(str(self.team_name), 70)
        tag = clean(str(self.tag), 8).upper()
        roster = clean(str(self.roster), 400)
        if len(tag) < 2:
            return await interaction.response.send_message("Team tag must be 2–8 characters.", ephemeral=True)
        try:
            with db_connect() as db:
                db.execute("INSERT INTO teams(guild_id,team_name,tag,igl_id,roster,created_at) VALUES(?,?,?,?,?,?)",
                           (interaction.guild.id, team_name, tag, interaction.user.id, roster, now_iso()))
        except sqlite3.IntegrityError:
            return await interaction.response.send_message(f"Tag **{tag}** is already in use.", ephemeral=True)
        await interaction.response.send_message(
            embed=embed("Team registered", f"**{team_name} [{tag}]**\nIGL: {interaction.user.mention}\nRoster: {roster or 'Not provided'}"),
            ephemeral=True)


class TournamentCreateModal(discord.ui.Modal, title="Create Tournament"):
    name = discord.ui.TextInput(label="Tournament name", max_length=90, placeholder="Lumi Weekly Cup")
    slots = discord.ui.TextInput(label="Team slots (2–512)", default="25", max_length=3)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        try:
            slots = int(str(self.slots))
            if not 2 <= slots <= 512:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("Slots must be a number from 2 to 512.", ephemeral=True)
        with db_connect() as db:
            cur = db.execute("INSERT INTO tournaments(guild_id,name,slots,status,created_by,created_at) VALUES(?,?,?,'open',?,?)",
                             (interaction.guild.id, clean(str(self.name), 90), slots, interaction.user.id, now_iso()))
            tid = cur.lastrowid
        await interaction.response.send_message(embed=embed("Tournament created",
            f"**{clean(str(self.name), 90)}**\nID: `{tid}` · Slots: **{slots}** · Registration: **OPEN**"), ephemeral=True)


class ScrimCreateModal(discord.ui.Modal, title="Schedule BGMI Scrim"):
    title_input = discord.ui.TextInput(label="Scrim name", max_length=90, placeholder="T2 Practice Room")
    map_name = discord.ui.TextInput(label="Map", max_length=30, placeholder="Erangel")
    match_time = discord.ui.TextInput(label="Date and time", max_length=80, placeholder="29 Oct, 8:00 PM IST")

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        title = clean(str(self.title_input), 90)
        map_name = clean(str(self.map_name), 30)
        match_time = clean(str(self.match_time), 80)
        with db_connect() as db:
            cur = db.execute("INSERT INTO scrims(guild_id,title,map_name,match_time,created_by,created_at) VALUES(?,?,?,?,?,?)",
                             (interaction.guild.id, title, map_name, match_time, interaction.user.id, now_iso()))
            sid = cur.lastrowid
        await interaction.response.send_message(embed=embed("Scrim scheduled",
            f"**{title}** · ID `{sid}`\nMap: **{map_name}**\nTime: **{match_time}**"), ephemeral=True)


class RoomPostModal(discord.ui.Modal, title="Post Scrim Room Details"):
    scrim_id = discord.ui.TextInput(label="Scrim ID", max_length=8, placeholder="1")
    room_id = discord.ui.TextInput(label="BGMI Room ID", max_length=40)
    password = discord.ui.TextInput(label="Room password", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        try:
            sid = int(str(self.scrim_id))
        except ValueError:
            return await interaction.response.send_message("Scrim ID must be a number.", ephemeral=True)
        with db_connect() as db:
            row = db.execute("SELECT * FROM scrims WHERE id=? AND guild_id=?", (sid, interaction.guild.id)).fetchone()
            if not row:
                return await interaction.response.send_message("Scrim not found. Check the ID.", ephemeral=True)
            db.execute("UPDATE scrims SET room_id=?,room_password=? WHERE id=?",
                       (clean(str(self.room_id), 40), clean(str(self.password), 40), sid))
            saved_channel = db.execute("SELECT value FROM settings WHERE guild_id=? AND key='announcements'",
                                       (interaction.guild.id,)).fetchone()
        target = interaction.guild.get_channel(int(saved_channel["value"])) if saved_channel else None
        room_embed = embed(f"Room details • {row['title']}",
            f"**Map:** {row['map_name']}\n**Room ID:** `{clean(str(self.room_id), 40)}`\n"
            f"**Password:** `{clean(str(self.password), 40)}`\nOnly share with registered participants.")
        if target:
            await target.send(embed=room_embed)
            await interaction.response.send_message(f"Room details posted in {target.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(embed=room_embed, ephemeral=True)


class ResultSubmitModal(discord.ui.Modal, title="Submit Match Result"):
    event_name = discord.ui.TextInput(label="Event name", max_length=90, placeholder="Lumi Weekly Cup")
    team_tag = discord.ui.TextInput(label="Team tag", max_length=8, placeholder="LUMI")
    placement = discord.ui.TextInput(label="Placement (1–100)", max_length=3, placeholder="1")
    finishes = discord.ui.TextInput(label="Finishes (0–100)", max_length=3, placeholder="12")

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        try:
            place, finish = int(str(self.placement)), int(str(self.finishes))
            if not 1 <= place <= 100 or not 0 <= finish <= 100:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("Placement must be 1–100 and finishes 0–100.", ephemeral=True)
        tag = clean(str(self.team_tag), 8).upper()
        with db_connect() as db:
            team = db.execute("SELECT * FROM teams WHERE guild_id=? AND tag=?", (interaction.guild.id, tag)).fetchone()
            if not team:
                return await interaction.response.send_message("Team not found. Register it first.", ephemeral=True)
            if interaction.user.id != team["igl_id"] and not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
                return await interaction.response.send_message("Only the team's IGL or server staff can submit results.", ephemeral=True)
            cur = db.execute("INSERT INTO results(guild_id,event_name,team_tag,placement,finishes,submitted_by,approved,created_at) VALUES(?,?,?,?,?,?,0,?)",
                             (interaction.guild.id, clean(str(self.event_name), 90), tag, place, finish, interaction.user.id, now_iso()))
            rid = cur.lastrowid
        await interaction.response.send_message(embed=embed("Result submitted",
            f"Result ID: `{rid}`\nEvent: **{clean(str(self.event_name), 90)}**\nTeam: **{tag}**\n"
            f"Placement: **#{place}** · Finishes: **{finish}**\nStatus: **Pending staff approval**"), ephemeral=True)


class TicketReasonModal(discord.ui.Modal, title="Open Support Ticket"):
    reason = discord.ui.TextInput(label="How can staff help?", style=discord.TextStyle.paragraph,
                                  max_length=250, placeholder="Describe your issue")

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        with db_connect() as db:
            row = db.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND opener_id=? AND status='open'",
                             (guild.id, interaction.user.id)).fetchone()
        if row and guild.get_channel(row["channel_id"]):
            return await interaction.response.send_message(f"You already have <#{row['channel_id']}> open.", ephemeral=True)
        bot_member = guild.me
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await guild.create_text_channel(name=f"ticket-{interaction.user.name.lower()[:15]}",
            overwrites=overwrites, reason=f"Support ticket opened by {interaction.user}")
        with db_connect() as db:
            db.execute("INSERT INTO tickets(guild_id,channel_id,opener_id,status,created_at) VALUES(?,?,?,'open',?)",
                       (guild.id, channel.id, interaction.user.id, now_iso()))
        await channel.send(f"{interaction.user.mention} Welcome to **{BRAND} Support**.\n**Reason:** {clean(str(self.reason), 250)}")
        await interaction.response.send_message(f"Your private ticket is {channel.mention}.", ephemeral=True)



class TournamentRegisterModal(discord.ui.Modal, title="Register Team in Tournament"):
    tournament_id = discord.ui.TextInput(label="Tournament ID", max_length=8, placeholder="1")
    team_tag = discord.ui.TextInput(label="Your team tag", max_length=8, placeholder="LUMI")

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        try:
            tid = int(str(self.tournament_id))
        except ValueError:
            return await interaction.response.send_message("Tournament ID must be a number.", ephemeral=True)
        tag = clean(str(self.team_tag), 8).upper()
        with db_connect() as db:
            tour = db.execute("SELECT * FROM tournaments WHERE id=? AND guild_id=?", (tid, interaction.guild.id)).fetchone()
            team = db.execute("SELECT * FROM teams WHERE guild_id=? AND tag=?", (interaction.guild.id, tag)).fetchone()
            if not tour or not team:
                return await interaction.response.send_message("Tournament ID or team tag not found.", ephemeral=True)
            if tour["status"] != "open":
                return await interaction.response.send_message("Registration is closed.", ephemeral=True)
            count = db.execute("SELECT COUNT(*) n FROM registrations WHERE tournament_id=?", (tid,)).fetchone()["n"]
            if count >= tour["slots"]:
                return await interaction.response.send_message("All tournament slots are full.", ephemeral=True)
            if interaction.user.id != team["igl_id"] and not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
                return await interaction.response.send_message("Only the team's IGL or server staff can register it.", ephemeral=True)
            try:
                db.execute("INSERT INTO registrations(tournament_id,team_id,registered_at) VALUES(?,?,?)", (tid, team["id"], now_iso()))
            except sqlite3.IntegrityError:
                return await interaction.response.send_message("This team is already registered.", ephemeral=True)
        await interaction.response.send_message(embed=embed("Registration successful",
            f"**{team['team_name']} [{tag}]** registered for **{tour['name']}**."), ephemeral=True)


class ResultApproveModal(discord.ui.Modal, title="Approve Match Result"):
    result_id = discord.ui.TextInput(label="Pending result ID", max_length=8, placeholder="1")

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server or Administrator permission required.", ephemeral=True)
        try:
            rid = int(str(self.result_id))
        except ValueError:
            return await interaction.response.send_message("Result ID must be a number.", ephemeral=True)
        with db_connect() as db:
            cur = db.execute("UPDATE results SET approved=1 WHERE id=? AND guild_id=? AND approved=0",
                             (rid, interaction.guild.id))
        await interaction.response.send_message(
            embed=embed("Result approval", "Result approved and added to leaderboard." if cur.rowcount else "Pending result not found.",
                        discord.Color.green() if cur.rowcount else discord.Color.red()), ephemeral=True)


class LumiCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Teams", style=discord.ButtonStyle.primary, emoji="👥", row=0)
    async def teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("Team Management",
            "Use the buttons below to register a team or view registered teams."),
            view=LumiTeamView(), ephemeral=True)

    @discord.ui.button(label="Tournaments", style=discord.ButtonStyle.primary, emoji="🏆", row=0)
    async def tournaments(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("Tournament Control",
            "Create tournaments, browse tournaments, or register a team."),
            view=LumiTournamentView(), ephemeral=True)

    @discord.ui.button(label="Scrims", style=discord.ButtonStyle.primary, emoji="🎮", row=0)
    async def scrims(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("Scrim Control",
            "Schedule scrims, view the schedule, or post BGMI room credentials."),
            view=LumiScrimView(), ephemeral=True)

    @discord.ui.button(label="Results", style=discord.ButtonStyle.success, emoji="📊", row=1)
    async def results(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("Results & Leaderboard",
            "Submit results for staff approval or view the approved leaderboard."),
            view=LumiResultView(), ephemeral=True)

    @discord.ui.button(label="Support", style=discord.ButtonStyle.secondary, emoji="🎫", row=1)
    async def support(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("Support Center",
            "Open a private support ticket or close a ticket from inside its channel."),
            view=LumiSupportView(), ephemeral=True)

    @discord.ui.button(label="Setup / Admin", style=discord.ButtonStyle.danger, emoji="⚙️", row=1)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Manage Server or Administrator permission required.", ephemeral=True)
        await interaction.response.send_message(embed=embed("Server Admin",
            "Admin actions use slash/prefix commands for typed values:\n"
            "`/set_channel` · `/tournament_close` · `/result_approve` · `/event_cancel` · `/esports_setup`\n\n"
            "Use the command list when an action needs an ID or channel selection."),
            ephemeral=True)


class LumiTeamView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Register Team", style=discord.ButtonStyle.success, emoji="➕")
    async def register(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TeamRegisterModal())

    @discord.ui.button(label="List Teams", style=discord.ButtonStyle.primary, emoji="📋")
    async def listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        with db_connect() as db:
            rows = db.execute("SELECT team_name,tag,igl_id FROM teams WHERE guild_id=? ORDER BY team_name LIMIT 25",
                              (interaction.guild_id,)).fetchall()
        desc = "\n".join(f"**{r['team_name']} [{r['tag']}]** — <@{r['igl_id']}>" for r in rows) or "No teams registered yet."
        await interaction.response.send_message(embed=embed(f"Registered Teams ({len(rows)})", desc), ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("LUMI • Esports Control Panel", "Choose a category below."),
                                                view=LumiCategoryView(), ephemeral=True)


class LumiTournamentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, emoji="➕")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TournamentCreateModal())

    @discord.ui.button(label="List", style=discord.ButtonStyle.primary, emoji="📋")
    async def listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        with db_connect() as db:
            rows = db.execute("SELECT t.*, (SELECT COUNT(*) FROM registrations r WHERE r.tournament_id=t.id) AS count FROM tournaments t WHERE guild_id=? ORDER BY id DESC LIMIT 20",
                              (interaction.guild_id,)).fetchall()
        desc = "\n".join(f"`#{r['id']}` **{r['name']}** — {r['count']}/{r['slots']} — **{r['status'].upper()}**" for r in rows) or "No tournaments created yet."
        await interaction.response.send_message(embed=embed("Tournament List", desc), ephemeral=True)

    @discord.ui.button(label="Register Team", style=discord.ButtonStyle.primary, emoji="📝")
    async def register(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TournamentRegisterModal())

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("LUMI • Esports Control Panel", "Choose a category below."),
                                                view=LumiCategoryView(), ephemeral=True)



class ScrimUpdateModal(discord.ui.Modal, title="Update Scrim"):
    title_input = discord.ui.TextInput(label="Scrim name", max_length=90)
    map_name = discord.ui.TextInput(label="Map", max_length=30)
    match_time = discord.ui.TextInput(label="Date and time", max_length=80)

    def __init__(self, scrim_id: int):
        super().__init__()
        self.scrim_id = scrim_id
        with db_connect() as db:
            row = db.execute("SELECT * FROM scrims WHERE id=?", (scrim_id,)).fetchone()
        if row:
            self.title_input.default = row["title"]
            self.map_name.default = row["map_name"]
            self.match_time.default = row["match_time"]

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        with db_connect() as db:
            cur = db.execute(
                "UPDATE scrims SET title=?,map_name=?,match_time=? WHERE id=? AND guild_id=?",
                (clean(str(self.title_input), 90), clean(str(self.map_name), 30),
                 clean(str(self.match_time), 80), self.scrim_id, interaction.guild.id)
            )
        if not cur.rowcount:
            return await interaction.response.send_message("Scrim not found.", ephemeral=True)
        await interaction.response.send_message(
            embed=embed("Scrim updated", "Scrim details have been saved."),
            view=ScrimDetailView(self.scrim_id), ephemeral=True
        )


class LobbyFieldModal(discord.ui.Modal):
    value = discord.ui.TextInput(label="New value", max_length=100, required=True)

    def __init__(self, scrim_id: int, field_key: str, field_label: str):
        super().__init__(title=f"Set {field_label}")
        self.scrim_id = scrim_id
        self.field_key = field_key
        self.value.label = field_label
        self.value.placeholder = "Enter value"
        self.value.default = ""

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        raw = clean(str(self.value), 100)
        if self.field_key in {"total_slots", "start_slot"}:
            try:
                n = int(raw)
                if self.field_key == "total_slots" and not 1 <= n <= 100:
                    raise ValueError
                if self.field_key == "start_slot" and not 1 <= n <= 100:
                    raise ValueError
            except ValueError:
                return await interaction.response.send_message("Enter a whole number from 1 to 100.", ephemeral=True)
            raw = str(n)
        with db_connect() as db:
            row = db.execute("SELECT id FROM scrim_lobbies WHERE guild_id=? AND scrim_id=? ORDER BY id LIMIT 1",
                             (interaction.guild.id, self.scrim_id)).fetchone()
            if not row:
                cur = db.execute(
                    "INSERT INTO scrim_lobbies(guild_id,scrim_id,created_at) VALUES(?,?,?)",
                    (interaction.guild.id, self.scrim_id, now_iso())
                )
                lobby_id = cur.lastrowid
            else:
                lobby_id = row["id"]
            if self.field_key in {"total_slots", "start_slot"}:
                db.execute(f"UPDATE scrim_lobbies SET {self.field_key}=? WHERE id=?", (int(raw), lobby_id))
            elif self.field_key == "name":
                db.execute("UPDATE scrim_lobbies SET name=? WHERE id=?", (raw, lobby_id))
            elif self.field_key in {"start_time", "end_time", "id_pass_time", "match_start", "clean_time", "map_name"}:
                db.execute(f"UPDATE scrim_lobbies SET {self.field_key}=? WHERE id=?", (raw, lobby_id))
            elif self.field_key in {"id_pass_channel_id", "assign_role_id"}:
                digits = raw.strip("<#@&!>")
                if not digits.isdigit():
                    return await interaction.response.send_message("Please enter a valid channel/role ID (or mention).", ephemeral=True)
                db.execute(f"UPDATE scrim_lobbies SET {self.field_key}=? WHERE id=?", (int(digits), lobby_id))
        await interaction.response.edit_message(embed=lobby_embed(interaction.guild.id, self.scrim_id), view=LobbySetupView(self.scrim_id))


def lobby_embed(guild_id: int, scrim_id: int):
    with db_connect() as db:
        row = db.execute("SELECT * FROM scrim_lobbies WHERE guild_id=? AND scrim_id=? ORDER BY id LIMIT 1",
                         (guild_id, scrim_id)).fetchone()
        scrim = db.execute("SELECT * FROM scrims WHERE guild_id=? AND id=?", (guild_id, scrim_id)).fetchone()
    if not scrim:
        return embed("Lobby Setup", "Scrim not found.", discord.Color.red())
    if not row:
        return embed("Lobby Setup", f"Configure lobby settings for **{scrim['title']}**.\n\nNo lobby settings saved yet.")
    channel = f"<#{row['id_pass_channel_id']}>" if row["id_pass_channel_id"] else "Not set"
    role = f"<@&{row['assign_role_id']}>" if row["assign_role_id"] else "Not set"
    return embed("Lobby Setup",
        f"**Scrim:** {scrim['title']}\n"
        f"**Name:** {row['name']}\n**Total Slots:** {row['total_slots']}\n**Start Slot:** {row['start_slot']}\n"
        f"**Start Time:** {row['start_time'] or 'Not set'}\n**End Time:** {row['end_time'] or 'Not set'}\n"
        f"**ID Pass Time:** {row['id_pass_time'] or 'Not set'}\n**Match Start:** {row['match_start'] or 'Not set'}\n"
        f"**Cleaning Time:** {row['clean_time'] or 'Not set'}\n**Map:** {row['map_name'] or scrim['map_name']}\n"
        f"**ID Pass Channel:** {channel}\n**Assign Role:** {role}")


class LobbySetupView(discord.ui.View):
    def __init__(self, scrim_id: int):
        super().__init__(timeout=300)
        self.scrim_id = scrim_id

    async def set_field(self, interaction, key, label):
        await interaction.response.send_modal(LobbyFieldModal(self.scrim_id, key, label))

    @discord.ui.button(label="Name", style=discord.ButtonStyle.primary, emoji="📝", row=0)
    async def name_btn(self, i, b): await self.set_field(i, "name", "Lobby name")

    @discord.ui.button(label="Total Slots", style=discord.ButtonStyle.primary, emoji="🔢", row=0)
    async def slots_btn(self, i, b): await self.set_field(i, "total_slots", "Total slots (1–100)")

    @discord.ui.button(label="Start Slot", style=discord.ButtonStyle.secondary, emoji="🔢", row=0)
    async def slot_btn(self, i, b): await self.set_field(i, "start_slot", "Start slot (1–100)")

    @discord.ui.button(label="Start Time", style=discord.ButtonStyle.primary, emoji="🕒", row=1)
    async def start_btn(self, i, b): await self.set_field(i, "start_time", "Start time")

    @discord.ui.button(label="End Time", style=discord.ButtonStyle.primary, emoji="🕒", row=1)
    async def end_btn(self, i, b): await self.set_field(i, "end_time", "End time")

    @discord.ui.button(label="ID Pass Time", style=discord.ButtonStyle.primary, emoji="🕒", row=2)
    async def pass_btn(self, i, b): await self.set_field(i, "id_pass_time", "ID pass time")

    @discord.ui.button(label="Match Start", style=discord.ButtonStyle.primary, emoji="🕒", row=2)
    async def match_btn(self, i, b): await self.set_field(i, "match_start", "Match start time")

    @discord.ui.button(label="Clean Time", style=discord.ButtonStyle.primary, emoji="🧹", row=2)
    async def clean_btn(self, i, b): await self.set_field(i, "clean_time", "Cleaning time")

    @discord.ui.button(label="ID Pass Channel", style=discord.ButtonStyle.primary, emoji="💬", row=3)
    async def channel_btn(self, i, b): await self.set_field(i, "id_pass_channel_id", "ID pass channel ID/mention")

    @discord.ui.button(label="Assign Role", style=discord.ButtonStyle.primary, emoji="🎭", row=3)
    async def role_btn(self, i, b): await self.set_field(i, "assign_role_id", "Role ID/mention")

    @discord.ui.button(label="Map", style=discord.ButtonStyle.primary, emoji="🗺️", row=3)
    async def map_btn(self, i, b): await self.set_field(i, "map_name", "Map name")

    @discord.ui.button(label="Save Lobby", style=discord.ButtonStyle.success, emoji="💾", row=4)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        with db_connect() as db:
            row = db.execute("SELECT id FROM scrim_lobbies WHERE guild_id=? AND scrim_id=?",
                             (interaction.guild.id, self.scrim_id)).fetchone()
            if not row:
                db.execute("INSERT INTO scrim_lobbies(guild_id,scrim_id,created_at) VALUES(?,?,?)",
                           (interaction.guild.id, self.scrim_id, now_iso()))
        await interaction.response.edit_message(embed=lobby_embed(interaction.guild.id, self.scrim_id), view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌", row=4)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=embed("Lobby Setup", "Lobby setup cancelled."), view=None)


class ScrimDetailView(discord.ui.View):
    def __init__(self, scrim_id: int):
        super().__init__(timeout=300)
        self.scrim_id = scrim_id

    async def refresh(self, interaction):
        with db_connect() as db:
            row = db.execute("SELECT * FROM scrims WHERE id=? AND guild_id=?",
                             (self.scrim_id, interaction.guild_id)).fetchone()
        if not row:
            return await interaction.response.edit_message(embed=embed("Scrim not found", "This scrim may have been deleted.", discord.Color.red()), view=None)
        status = row["status"].upper()
        e = embed("Manage Scrim",
            f"**Scrim Name**\n{row['title']}\n\n**Map**\n{row['map_name']}\n\n**Match Time**\n{row['match_time']}\n\n"
            f"**Scrim ID**\n`{row['id']}`\n\n**Status**\n{'🟢 Enabled' if row['status'] == 'scheduled' else '🔴 Disabled / Cancelled'}")
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(label="Update", style=discord.ButtonStyle.primary, emoji="📝", row=0)
    async def update_btn(self, interaction, button):
        await interaction.response.send_modal(ScrimUpdateModal(self.scrim_id))

    @discord.ui.button(label="Enable / Disable", style=discord.ButtonStyle.success, emoji="🟢", row=0)
    async def toggle_btn(self, interaction, button):
        if not interaction.guild or not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        with db_connect() as db:
            row = db.execute("SELECT status FROM scrims WHERE id=? AND guild_id=?", (self.scrim_id, interaction.guild.id)).fetchone()
            if not row:
                return await interaction.response.send_message("Scrim not found.", ephemeral=True)
            new_status = "cancelled" if row["status"] == "scheduled" else "scheduled"
            db.execute("UPDATE scrims SET status=? WHERE id=?", (new_status, self.scrim_id))
        await self.refresh(interaction)

    @discord.ui.button(label="Manage Lobbies", style=discord.ButtonStyle.secondary, emoji="⚙️", row=1)
    async def lobby_btn(self, interaction, button):
        if not interaction.guild or not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        await interaction.response.send_message(embed=lobby_embed(interaction.guild.id, self.scrim_id),
                                                view=LobbySetupView(self.scrim_id), ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def delete_btn(self, interaction, button):
        if not interaction.guild or not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        with db_connect() as db:
            db.execute("DELETE FROM scrim_lobbies WHERE guild_id=? AND scrim_id=?", (interaction.guild.id, self.scrim_id))
            cur = db.execute("DELETE FROM scrims WHERE guild_id=? AND id=?", (interaction.guild.id, self.scrim_id))
        await interaction.response.edit_message(embed=embed("Scrim deleted", "The scrim and its lobby settings were deleted." if cur.rowcount else "Scrim not found."), view=None)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", row=2)
    async def refresh_btn(self, interaction, button): await self.refresh(interaction)

    @discord.ui.button(label="Back to List", style=discord.ButtonStyle.secondary, emoji="↩️", row=2)
    async def back_btn(self, interaction, button):
        await interaction.response.edit_message(embed=embed("Manage Scrims", "Select a scrim from the dropdown below, or create a new one."),
                                                view=ScrimManageView())


class ScrimSelect(discord.ui.Select):
    def __init__(self, guild_id: int):
        with db_connect() as db:
            rows = db.execute("SELECT id,title,map_name,match_time FROM scrims WHERE guild_id=? ORDER BY id DESC LIMIT 25", (guild_id,)).fetchall()
        options = [discord.SelectOption(label=f"#{r['id']} • {r['title']}"[:100],
                    description=f"{r['map_name']} — {r['match_time']}"[:100], value=str(r["id"])) for r in rows]
        if not options:
            options = [discord.SelectOption(label="No scrims yet", value="none", description="Create a new scrim first")]
        super().__init__(placeholder="Select a scrim to manage...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("No scrims found. Use **Create New Scrim**.", ephemeral=True)
        await interaction.response.send_message(embed=embed("Manage Scrim", "Loading scrim settings..."),
                                                view=ScrimDetailView(int(self.values[0])), ephemeral=True)
        # Replace loading embed with the selected scrim details.
        with db_connect() as db:
            row = db.execute("SELECT * FROM scrims WHERE id=? AND guild_id=?", (int(self.values[0]), interaction.guild_id)).fetchone()
        if row:
            e = embed("Manage Scrim",
                f"**Scrim Name**\n{row['title']}\n\n**Map**\n{row['map_name']}\n\n**Match Time**\n{row['match_time']}\n\n"
                f"**Scrim ID**\n`{row['id']}`\n\n**Status**\n{'🟢 Enabled' if row['status'] == 'scheduled' else '🔴 Disabled / Cancelled'}")
            await interaction.edit_original_response(embed=e, view=ScrimDetailView(row["id"]))


class ScrimManageView(discord.ui.View):
    def __init__(self, guild_id: Optional[int] = None):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        if guild_id:
            self.add_item(ScrimSelect(guild_id))

    @discord.ui.button(label="Create New Scrim", style=discord.ButtonStyle.success, emoji="➕", row=1)
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        await interaction.response.send_modal(ScrimCreateModal())


class LumiScrimView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Schedule Scrim", style=discord.ButtonStyle.success, emoji="➕")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScrimCreateModal())

    @discord.ui.button(label="View Scrims", style=discord.ButtonStyle.primary, emoji="📋")
    async def listing(self, interaction: discord.Interaction, button: discord.ui.Button):
        with db_connect() as db:
            rows = db.execute("SELECT * FROM scrims WHERE guild_id=? AND status='scheduled' ORDER BY id DESC LIMIT 15",
                              (interaction.guild_id,)).fetchall()
        desc = "\n".join(f"`#{r['id']}` **{r['title']}** — {r['map_name']} — {r['match_time']}" for r in rows) or "No scheduled scrims."
        await interaction.response.send_message(embed=embed("Scheduled Scrims", desc), ephemeral=True)

    @discord.ui.button(label="Post Room ID / Password", style=discord.ButtonStyle.danger, emoji="🔐")
    async def room(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Manage Server or Administrator permission required.", ephemeral=True)
        await interaction.response.send_modal(RoomPostModal())

    @discord.ui.button(label="Manage Scrims", style=discord.ButtonStyle.primary, emoji="⚙️", row=1)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("Use this inside a server.", ephemeral=True)
        await interaction.response.send_message(
            embed=embed("Manage Scrims", "Select a scrim from the dropdown below to update its settings and lobbies, or create a new one."),
            view=ScrimManageView(interaction.guild.id), ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("LUMI • Esports Control Panel", "Choose a category below."),
                                                view=LumiCategoryView(), ephemeral=True)


class LumiResultView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Submit Result", style=discord.ButtonStyle.success, emoji="📝")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResultSubmitModal())

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.primary, emoji="🏅")
    async def leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        placement_points = "CASE placement WHEN 1 THEN 10 WHEN 2 THEN 6 WHEN 3 THEN 5 WHEN 4 THEN 4 WHEN 5 THEN 3 WHEN 6 THEN 2 WHEN 7 THEN 1 WHEN 8 THEN 1 ELSE 0 END"
        with db_connect() as db:
            rows = db.execute(f"SELECT team_tag, SUM(({placement_points})+finishes) AS points, SUM(finishes) AS finishes, COUNT(*) AS matches FROM results WHERE guild_id=? AND approved=1 GROUP BY team_tag ORDER BY points DESC, finishes DESC LIMIT 25",
                              (interaction.guild_id,)).fetchall()
        desc = "\n".join(f"**{i}. [{r['team_tag']}]** — `{r['points']}` pts · {r['finishes']} finishes · {r['matches']} matches" for i, r in enumerate(rows, 1)) or "No approved results yet."
        await interaction.response.send_message(embed=embed("LUMI Leaderboard", desc), ephemeral=True)

    @discord.ui.button(label="Pending Results", style=discord.ButtonStyle.secondary, emoji="⏳")
    async def pending(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Manage Server or Administrator permission required.", ephemeral=True)
        with db_connect() as db:
            rows = db.execute("SELECT id,event_name,team_tag,placement,finishes FROM results WHERE guild_id=? AND approved=0 ORDER BY id DESC LIMIT 15",
                              (interaction.guild_id,)).fetchall()
        desc = "\n".join(f"`#{r['id']}` {r['event_name']} · **{r['team_tag']}** · #{r['placement']} · {r['finishes']} finishes" for r in rows) or "No pending results."
        await interaction.response.send_message(embed=embed("Pending Results", desc + "\n\nApprove using `/result_approve result_id`."), ephemeral=True)

    @discord.ui.button(label="Approve Result", style=discord.ButtonStyle.success, emoji="✅", row=1)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ResultApproveModal())

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("LUMI • Esports Control Panel", "Choose a category below."),
                                                view=LumiCategoryView(), ephemeral=True)


class LumiSupportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.success, emoji="🎫")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketReasonModal())

    @discord.ui.button(label="Close This Ticket", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Use this in a server ticket channel.", ephemeral=True)
        with db_connect() as db:
            row = db.execute("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (interaction.channel.id,)).fetchone()
        if not row:
            return await interaction.response.send_message("This is not an open LUMI ticket.", ephemeral=True)
        if interaction.user.id != row["opener_id"] and not (interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Only the ticket opener or staff can close it.", ephemeral=True)
        with db_connect() as db:
            db.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (interaction.channel.id,))
        await interaction.response.send_message("Ticket closed. This channel will be deleted in 5 seconds.")
        import asyncio
        await asyncio.sleep(5)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=embed("LUMI • Esports Control Panel", "Choose a category below."),
                                                view=LumiCategoryView(), ephemeral=True)



class LumiBot(commands.Bot):
    async def setup_hook(self):
        init_db()
        await self.add_cog(Esports(self))
        try:
            synced = await self.tree.sync()
            log.info("Synced %s global application commands", len(synced))
        except Exception:
            log.exception("Slash command sync failed")


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = LumiBot(command_prefix=commands.when_mentioned_or(PREFIX), intents=intents, help_command=None)


class Esports(commands.Cog):
    def __init__(self, client: LumiBot):
        self.bot = client

    async def send_ctx(self, ctx, e):
        if ctx.interaction:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(embed=e)
            else:
                await ctx.interaction.followup.send(embed=e)
        else:
            await ctx.send(embed=e)

    async def staff_check(self, ctx):
        if not (ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.manage_guild):
            await self.send_ctx(ctx, embed("Permission denied", "You need **Manage Server** or **Administrator** permission.", discord.Color.red()))
            return False
        return True

    @commands.hybrid_command(name="help", description="Show LUMI command guide")
    async def help_cmd(self, ctx):
        e = embed("LUMI • Interactive Help Panel",
            "**Teams**\n`/team_register` ` /team_info` ` /team_list`\n\n"
            "**Tournaments**\n`/tournament_create` `/tournament_register` `/tournament_list` `/tournament_close`\n\n"
            "**Scrims**\n`/scrim_create` `/scrim_list` `/scrim_manage` `/room_post`\n\n"
            "**Results**\n`/result_submit` `/result_approve` `/leaderboard`\n\n"
            "**Support & setup**\n`/ticket` `/ticket_close` `/set_channel` `/esports_setup`\n\n"
            f"Prefix commands use `{PREFIX}` too. Example: `{PREFIX}team_list`.")
        e.add_field(name="Permission note", value="Administrative commands require Manage Server or Administrator.", inline=False)
        await self.send_ctx(ctx, e)

    @commands.hybrid_command(name="panel", description="Open the interactive LUMI control panel")
    async def panel(self, ctx):
        e = embed("LUMI • Esports Control Panel",
                  "Choose a category. Most common actions are available through buttons; admin-only actions check permissions.")
        view = LumiCategoryView()
        if ctx.interaction:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(embed=e, view=view)
            else:
                await ctx.interaction.followup.send(embed=e, view=view)
        else:
            await ctx.send(embed=e, view=view)

    @commands.hybrid_command(name="esports_setup", description="Show setup checklist for this server")
    @is_staff()
    async def esports_setup(self, ctx):
        e = embed("Server setup checklist",
            "1. Create roles such as Owner, Admin, Tournament Manager, Scrim Manager and Point Table Maker.\n"
            "2. Give the bot only the permissions it needs; place its role above roles it must manage.\n"
            "3. Use `/set_channel` to configure announcements or results channels.\n"
            "4. Use `/tournament_create` and `/scrim_create` to start managing events.\n\n"
            "This command does not create or change roles automatically.")
        await self.send_ctx(ctx, e)

    @commands.hybrid_command(name="set_channel", description="Set a channel for announcements or results")
    @is_staff()
    @app_commands.describe(kind="Channel purpose", channel="Channel to use")
    async def set_channel(self, ctx, kind: str, channel: discord.TextChannel):
        kind = clean(kind, 30).lower()
        if kind not in {"announcements", "results"}:
            return await self.send_ctx(ctx, embed("Invalid channel type", "Choose `announcements` or `results`.", discord.Color.red()))
        with db_connect() as db:
            db.execute("INSERT INTO settings(guild_id,key,value) VALUES(?,?,?) ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value",
                       (ctx.guild.id, kind, str(channel.id)))
        await self.send_ctx(ctx, embed("Channel saved", f"{kind.title()} channel set to {channel.mention}."))

    @commands.hybrid_command(name="team_register", description="Register a BGMI esports team")
    @app_commands.describe(team_name="Full team name", tag="Short unique team tag", roster="Player names or Discord IDs, comma-separated")
    async def team_register(self, ctx, team_name: str, tag: str, roster: str = ""):
        if not ctx.guild:
            return await self.send_ctx(ctx, embed("Server only", "Use this command inside your Discord server.", discord.Color.red()))
        team_name, tag, roster = clean(team_name, 70), clean(tag, 8).upper(), clean(roster, 400)
        if len(tag) < 2:
            return await self.send_ctx(ctx, embed("Invalid tag", "Team tag must be 2–8 characters.", discord.Color.red()))
        try:
            with db_connect() as db:
                db.execute("INSERT INTO teams(guild_id,team_name,tag,igl_id,roster,created_at) VALUES(?,?,?,?,?,?)",
                           (ctx.guild.id, team_name, tag, ctx.author.id, roster, now_iso()))
        except sqlite3.IntegrityError:
            return await self.send_ctx(ctx, embed("Tag already used", f"The tag **{tag}** is already registered in this server.", discord.Color.red()))
        await self.send_ctx(ctx, embed("Team registered", f"**{team_name} [{tag}]**\nIGL: {ctx.author.mention}\nRoster: {roster or 'Not provided'}"))

    @commands.hybrid_command(name="team_info", description="Look up a registered team by tag")
    async def team_info(self, ctx, tag: str):
        with db_connect() as db:
            row = db.execute("SELECT * FROM teams WHERE guild_id=? AND tag=?", (ctx.guild.id, clean(tag, 8).upper())).fetchone()
        if not row:
            return await self.send_ctx(ctx, embed("Team not found", "No team with that tag is registered.", discord.Color.red()))
        igl = ctx.guild.get_member(row["igl_id"])
        igl_display = igl.mention if igl else f"<@{row['igl_id']}>"
        await self.send_ctx(ctx, embed(f"{row['team_name']} [{row['tag']}]", f"**IGL:** {igl_display}\n**Roster:** {row['roster'] or 'Not provided'}\n**Registered:** {row['created_at']}"))

    @commands.hybrid_command(name="team_list", description="List registered teams")
    async def team_list(self, ctx):
        with db_connect() as db:
            rows = db.execute("SELECT team_name,tag,igl_id FROM teams WHERE guild_id=? ORDER BY team_name LIMIT 25", (ctx.guild.id,)).fetchall()
        if not rows:
            return await self.send_ctx(ctx, embed("Teams", "No teams registered yet. Use `/team_register`."))
        lines = [f"**{r['team_name']} [{r['tag']}]** — <@{r['igl_id']}>" for r in rows]
        await self.send_ctx(ctx, embed(f"Registered teams ({len(rows)})", "\n".join(lines)))

    @commands.hybrid_command(name="tournament_create", description="Create a tournament")
    @is_staff()
    async def tournament_create(self, ctx, name: str, slots: app_commands.Range[int, 2, 512] = 25):
        with db_connect() as db:
            cur = db.execute("INSERT INTO tournaments(guild_id,name,slots,status,created_by,created_at) VALUES(?,?,?,'open',?,?)",
                             (ctx.guild.id, clean(name, 90), int(slots), ctx.author.id, now_iso()))
            tid = cur.lastrowid
        await self.send_ctx(ctx, embed("Tournament created", f"**{name}**\nTournament ID: `{tid}`\nSlots: **{slots} teams**\nRegistration: **OPEN**"))

    @commands.hybrid_command(name="tournament_list", description="List open tournaments")
    async def tournament_list(self, ctx):
        with db_connect() as db:
            rows = db.execute("SELECT t.*, (SELECT COUNT(*) FROM registrations r WHERE r.tournament_id=t.id) AS count FROM tournaments t WHERE guild_id=? ORDER BY id DESC LIMIT 20", (ctx.guild.id,)).fetchall()
        if not rows:
            return await self.send_ctx(ctx, embed("Tournaments", "No tournaments created yet."))
        lines = [f"`#{r['id']}` **{r['name']}** — {r['count']}/{r['slots']} teams — **{r['status'].upper()}**" for r in rows]
        await self.send_ctx(ctx, embed("Tournament list", "\n".join(lines)))

    @commands.hybrid_command(name="tournament_register", description="Register your team in a tournament")
    async def tournament_register(self, ctx, tournament_id: int, team_tag: str):
        with db_connect() as db:
            tour = db.execute("SELECT * FROM tournaments WHERE id=? AND guild_id=?", (tournament_id, ctx.guild.id)).fetchone()
            team = db.execute("SELECT * FROM teams WHERE guild_id=? AND tag=?", (ctx.guild.id, clean(team_tag, 8).upper())).fetchone()
            if not tour or not team:
                return await self.send_ctx(ctx, embed("Not found", "Check the tournament ID and team tag.", discord.Color.red()))
            if tour["status"] != "open":
                return await self.send_ctx(ctx, embed("Registration closed", "This tournament is not accepting registrations.", discord.Color.red()))
            count = db.execute("SELECT COUNT(*) n FROM registrations WHERE tournament_id=?", (tournament_id,)).fetchone()["n"]
            if count >= tour["slots"]:
                return await self.send_ctx(ctx, embed("Slots full", "This tournament has no open slots.", discord.Color.red()))
            if ctx.author.id != team["igl_id"] and not (ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):
                return await self.send_ctx(ctx, embed("IGL only", "Only the team's registered IGL or server staff can register it.", discord.Color.red()))
            try:
                db.execute("INSERT INTO registrations(tournament_id,team_id,registered_at) VALUES(?,?,?)", (tournament_id, team["id"], now_iso()))
            except sqlite3.IntegrityError:
                return await self.send_ctx(ctx, embed("Already registered", "That team is already registered.", discord.Color.red()))
        await self.send_ctx(ctx, embed("Registration successful", f"**{team['team_name']} [{team['tag']}]** registered for **{tour['name']}**."))

    @commands.hybrid_command(name="tournament_close", description="Close tournament registrations")
    @is_staff()
    async def tournament_close(self, ctx, tournament_id: int):
        with db_connect() as db:
            cur = db.execute("UPDATE tournaments SET status='closed' WHERE id=? AND guild_id=? AND status='open'", (tournament_id, ctx.guild.id))
        await self.send_ctx(ctx, embed("Tournament updated", "Registration closed." if cur.rowcount else "Open tournament not found.", discord.Color.green() if cur.rowcount else discord.Color.red()))

    @commands.hybrid_command(name="scrim_create", description="Schedule a BGMI scrim")
    @is_staff()
    async def scrim_create(self, ctx, title: str, map_name: str, match_time: str):
        with db_connect() as db:
            cur = db.execute("INSERT INTO scrims(guild_id,title,map_name,match_time,created_by,created_at) VALUES(?,?,?,?,?,?)",
                             (ctx.guild.id, clean(title, 90), clean(map_name, 30), clean(match_time, 80), ctx.author.id, now_iso()))
            sid = cur.lastrowid
        await self.send_ctx(ctx, embed("Scrim scheduled", f"**{title}**\nID: `{sid}`\nMap: **{map_name}**\nTime: **{match_time}**"))

    @commands.hybrid_command(name="scrim_list", description="List upcoming scrims")
    async def scrim_list(self, ctx):
        with db_connect() as db:
            rows = db.execute("SELECT * FROM scrims WHERE guild_id=? AND status='scheduled' ORDER BY id DESC LIMIT 15", (ctx.guild.id,)).fetchall()
        if not rows:
            return await self.send_ctx(ctx, embed("Scrims", "No scheduled scrims."))
        await self.send_ctx(ctx, embed("Scheduled scrims", "\n".join(f"`#{r['id']}` **{r['title']}** — {r['map_name']} — {r['match_time']}" for r in rows)))

    @commands.hybrid_command(name="scrim_manage", description="Open interactive scrim manager")
    @is_staff()
    async def scrim_manage(self, ctx):
        if not ctx.guild:
            return await self.send_ctx(ctx, embed("Server only", "Use this command inside a server.", discord.Color.red()))
        e = embed("Manage Scrims", "Select a scrim from the dropdown below to update its settings and lobbies, or create a new one.")
        view = ScrimManageView(ctx.guild.id)
        if ctx.interaction:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(embed=e, view=view, ephemeral=True)
            else:
                await ctx.interaction.followup.send(embed=e, view=view, ephemeral=True)
        else:
            await ctx.send(embed=e, view=view)

    @commands.hybrid_command(name="room_post", description="Post room ID and password for a scrim")
    @is_staff()
    async def room_post(self, ctx, scrim_id: int, room_id: str, password: str):
        with db_connect() as db:
            row = db.execute("SELECT * FROM scrims WHERE id=? AND guild_id=?", (scrim_id, ctx.guild.id)).fetchone()
            if not row:
                return await self.send_ctx(ctx, embed("Scrim not found", "Check the scrim ID.", discord.Color.red()))
            db.execute("UPDATE scrims SET room_id=?,room_password=? WHERE id=?", (clean(room_id, 40), clean(password, 40), scrim_id))
            channel_id = db.execute("SELECT value FROM settings WHERE guild_id=? AND key='announcements'", (ctx.guild.id,)).fetchone()
        target = ctx.guild.get_channel(int(channel_id["value"])) if channel_id else None
        message = embed(f"Room details • {row['title']}", f"**Map:** {row['map_name']}\n**Room ID:** `{room_id}`\n**Password:** `{password}`\nOnly share these details with registered participants.")
        if target:
            await target.send(embed=message)
            await self.send_ctx(ctx, embed("Room posted", f"Room details sent to {target.mention}."))
        else:
            await self.send_ctx(ctx, message)

    @commands.hybrid_command(name="result_submit", description="Submit a match result for staff approval")
    async def result_submit(self, ctx, event_name: str, team_tag: str, placement: app_commands.Range[int, 1, 100], finishes: app_commands.Range[int, 0, 100]):
        with db_connect() as db:
            team = db.execute("SELECT * FROM teams WHERE guild_id=? AND tag=?", (ctx.guild.id, clean(team_tag, 8).upper())).fetchone()
            if not team:
                return await self.send_ctx(ctx, embed("Team not found", "Register the team first.", discord.Color.red()))
            if ctx.author.id != team["igl_id"] and not (ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):
                return await self.send_ctx(ctx, embed("IGL only", "Only the team's IGL or staff can submit results.", discord.Color.red()))
            cur = db.execute("INSERT INTO results(guild_id,event_name,team_tag,placement,finishes,submitted_by,approved,created_at) VALUES(?,?,?,?,?,?,0,?)",
                             (ctx.guild.id, clean(event_name, 90), team["tag"], int(placement), int(finishes), ctx.author.id, now_iso()))
            rid = cur.lastrowid
        await self.send_ctx(ctx, embed("Result submitted", f"Result ID: `{rid}`\nEvent: **{event_name}**\nTeam: **{team['tag']}**\nPlacement: **#{placement}**\nFinishes: **{finishes}**\nStatus: **Pending staff approval**"))

    @commands.hybrid_command(name="result_approve", description="Approve a submitted result")
    @is_staff()
    async def result_approve(self, ctx, result_id: int):
        with db_connect() as db:
            cur = db.execute("UPDATE results SET approved=1 WHERE id=? AND guild_id=? AND approved=0", (result_id, ctx.guild.id))
        await self.send_ctx(ctx, embed("Result approval", "Result approved and included in the leaderboard." if cur.rowcount else "Pending result not found.", discord.Color.green() if cur.rowcount else discord.Color.red()))

    @commands.hybrid_command(name="leaderboard", description="Show approved result points")
    async def leaderboard(self, ctx, event_name: Optional[str] = None):
        # Default BGMI placement points: 1st=10, 2nd=6, 3rd=5, 4th=4,
        # 5th=3, 6th=2, 7th=1, 8th=1; finishes are 1 point each.
        placement_points = "CASE placement WHEN 1 THEN 10 WHEN 2 THEN 6 WHEN 3 THEN 5 WHEN 4 THEN 4 WHEN 5 THEN 3 WHEN 6 THEN 2 WHEN 7 THEN 1 WHEN 8 THEN 1 ELSE 0 END"
        with db_connect() as db:
            if event_name:
                rows = db.execute(f"SELECT team_tag, SUM(({placement_points})+finishes) AS points, SUM(finishes) AS finishes, COUNT(*) AS matches FROM results WHERE guild_id=? AND approved=1 AND event_name=? GROUP BY team_tag ORDER BY points DESC, finishes DESC LIMIT 25", (ctx.guild.id, clean(event_name, 90))).fetchall()
            else:
                rows = db.execute(f"SELECT team_tag, SUM(({placement_points})+finishes) AS points, SUM(finishes) AS finishes, COUNT(*) AS matches FROM results WHERE guild_id=? AND approved=1 GROUP BY team_tag ORDER BY points DESC, finishes DESC LIMIT 25", (ctx.guild.id,)).fetchall()
        if not rows:
            return await self.send_ctx(ctx, embed("Leaderboard", "No approved results yet."))
        lines = [f"**{i}. [{r['team_tag']}]** — `{r['points']}` pts · {r['finishes']} finishes · {r['matches']} matches" for i, r in enumerate(rows, 1)]
        await self.send_ctx(ctx, embed(f"Leaderboard{f' • {event_name}' if event_name else ''}", "\n".join(lines)))

    @commands.hybrid_command(name="ticket", description="Create a private support ticket")
    async def ticket(self, ctx, reason: str = "General support"):
        guild = ctx.guild
        if not guild:
            return await self.send_ctx(ctx, embed("Server only", "Use this in a server.", discord.Color.red()))
        existing = None
        with db_connect() as db:
            row = db.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND opener_id=? AND status='open'", (guild.id, ctx.author.id)).fetchone()
            if row:
                existing = guild.get_channel(row["channel_id"])
        if existing:
            return await self.send_ctx(ctx, embed("Ticket already open", f"You already have {existing.mention}."))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ctx.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await guild.create_text_channel(
            name=f"ticket-{ctx.author.name.lower()[:15]}",
            overwrites=overwrites,
            reason=f"Support ticket opened by {ctx.author}"
        )
        with db_connect() as db:
            db.execute("INSERT INTO tickets(guild_id,channel_id,opener_id,status,created_at) VALUES(?,?,?,'open',?)",
                       (guild.id, channel.id, ctx.author.id, now_iso()))
        await channel.send(f"{ctx.author.mention} Welcome to **{BRAND} Support**.\n**Reason:** {clean(reason, 250)}\nStaff can assist here. Use `/ticket_close` when resolved.")
        await self.send_ctx(ctx, embed("Ticket created", f"Your private ticket is {channel.mention}."))

    @commands.hybrid_command(name="ticket_close", description="Close the current support ticket")
    async def ticket_close(self, ctx):
        if not ctx.guild or not isinstance(ctx.channel, discord.TextChannel):
            return await self.send_ctx(ctx, embed("Unavailable", "Use this inside a ticket channel.", discord.Color.red()))
        with db_connect() as db:
            row = db.execute("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (ctx.channel.id,)).fetchone()
        if not row:
            return await self.send_ctx(ctx, embed("Not a ticket", "This channel is not an open bot ticket.", discord.Color.red()))
        if ctx.author.id != row["opener_id"] and not (ctx.author.guild_permissions.manage_channels or ctx.author.guild_permissions.administrator):
            return await self.send_ctx(ctx, embed("Permission denied", "Only the ticket opener or staff can close this ticket.", discord.Color.red()))
        with db_connect() as db:
            db.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (ctx.channel.id,))
        await self.send_ctx(ctx, embed("Ticket closed", "This channel will be deleted in 5 seconds. Save anything you need first."))
        import asyncio
        await asyncio.sleep(5)
        await ctx.channel.delete(reason=f"Ticket closed by {ctx.author}")

    @commands.hybrid_command(name="staff_report", description="Submit a staff or player report")
    async def staff_report(self, ctx, member: discord.Member, *, reason: str):
        if member.id == ctx.author.id:
            return await self.send_ctx(ctx, embed("Invalid report", "You cannot report yourself.", discord.Color.red()))
        e = embed("New player/staff report", f"**Reporter:** {ctx.author.mention}\n**Reported:** {member.mention}\n**Reason:** {clean(reason, 800)}")
        e.add_field(name="Guild", value=f"{ctx.guild.name} (`{ctx.guild.id}`)", inline=False)
        with db_connect() as db:
            row = db.execute("SELECT value FROM settings WHERE guild_id=? AND key='announcements'", (ctx.guild.id,)).fetchone()
        channel = ctx.guild.get_channel(int(row["value"])) if row else None
        if channel:
            await channel.send(embed=e)
            await self.send_ctx(ctx, embed("Report submitted", f"Your report was sent to {channel.mention}."))
        else:
            await self.send_ctx(ctx, embed("Report submitted", "No announcements channel configured; please contact a server admin."))

    @commands.hybrid_command(name="event_cancel", description="Cancel a scrim")
    @is_staff()
    async def event_cancel(self, ctx, scrim_id: int):
        with db_connect() as db:
            cur = db.execute("UPDATE scrims SET status='cancelled' WHERE id=? AND guild_id=? AND status='scheduled'", (scrim_id, ctx.guild.id))
        await self.send_ctx(ctx, embed("Scrim cancellation", "Scrim cancelled." if cur.rowcount else "Scheduled scrim not found.", discord.Color.orange() if cur.rowcount else discord.Color.red()))


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="BGMI Esports | LUMI"))


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        msg = "You do not have permission to use that command."
    elif isinstance(error, commands.MissingRequiredArgument):
        msg = f"Missing argument: `{error.param.name}`. Use `{PREFIX}help`."
    elif isinstance(error, commands.BadArgument):
        msg = "One or more arguments are invalid. Check the command format."
    else:
        log.exception("Command error", exc_info=error)
        msg = "Something went wrong. The error was logged for the bot owner."
    try:
        e = embed("Command error", msg, discord.Color.red())
        if ctx.interaction:
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(embed=e, ephemeral=True)
            else:
                await ctx.interaction.followup.send(embed=e, ephemeral=True)
        else:
            await ctx.send(embed=e, delete_after=12)
    except Exception:
        log.debug("Could not send command error message", exc_info=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.error("Slash command error: %s", error, exc_info=error)
    msg = "You do not have permission to use that command." if isinstance(error, app_commands.CheckFailure) else "The command failed. Check bot logs or command arguments."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Add it to your .env file.")
    bot.run(TOKEN, log_handler=None)
