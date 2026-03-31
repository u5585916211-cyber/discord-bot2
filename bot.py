import os
import json
import random
import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands
from discord import app_commands

# =========================================================
# ENV
# =========================================================
TOKEN = os.getenv("TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID") or os.getenv("GUILD_ID_RAW")

if not TOKEN:
    raise ValueError("TOKEN is missing. Add it in Railway Variables.")

if not GUILD_ID_RAW:
    raise ValueError("GUILD_ID is missing. Add it in Railway Variables.")

try:
    GUILD_ID = int(GUILD_ID_RAW)
except ValueError:
    raise ValueError("GUILD_ID must be a valid integer.")

# =========================================================
# CHANNEL CONFIG
# =========================================================
GAME_PANEL_CHANNEL_ID = 1488310736269873325
GAME_LOG_CHANNEL_ID = 1488310901537771670
STAFF_PANEL_CHANNEL_ID = 1488311013098000414

STAFF_ROLE_ID = 1487568447926829126

# =========================================================
# FILES
# =========================================================
COINS_FILE = "arcade_coins.json"
STATS_FILE = "arcade_stats.json"
DAILY_FILE = "arcade_daily.json"

# =========================================================
# COLORS
# =========================================================
COLOR_MAIN = 0x8E44AD
COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_DENY = 0xED4245
COLOR_INFO = 0x5865F2
COLOR_LOG = 0x2B2D31
COLOR_GAME = 0xF47FFF
COLOR_MINES = 0xE67E22
COLOR_ROAD = 0x2ECC71
COLOR_FLIP = 0x3498DB
COLOR_HILO = 0x9B59B6
COLOR_CRASH = 0xE74C3C
COLOR_DICE = 0x1ABC9C

# =========================================================
# BOT
# =========================================================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

coins_db = {}
stats_db = {}
daily_db = {}
active_games = {}

# =========================================================
# JSON HELPERS
# =========================================================
def load_json(path: str, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4)
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


# =========================================================
# HELPERS
# =========================================================
def premium_divider() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━━━"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    return any(role.id == STAFF_ROLE_ID for role in member.roles)


def get_user_coins(user_id: int) -> int:
    return int(coins_db.get(str(user_id), 0))


def set_user_coins(user_id: int, amount: int):
    coins_db[str(user_id)] = max(0, int(amount))
    save_json(COINS_FILE, coins_db)


def add_user_coins(user_id: int, amount: int):
    set_user_coins(user_id, get_user_coins(user_id) + int(amount))


def remove_user_coins(user_id: int, amount: int):
    set_user_coins(user_id, max(0, get_user_coins(user_id) - int(amount)))


def ensure_user_stats(user_id: int):
    uid = str(user_id)
    if uid not in stats_db:
        stats_db[uid] = {
            "coins_won": 0,
            "coins_spent": 0,
            "games_played": 0,
            "wheel_spins": 0,
            "road_runs": 0,
            "mines_runs": 0,
            "coinflip_runs": 0,
            "hilo_runs": 0,
            "crash_runs": 0,
            "dice_runs": 0,
            "wins": 0,
            "losses": 0,
            "best_road_steps": 0,
            "best_mines_safe_hits": 0,
            "best_hilo_streak": 0,
            "best_crash_multi": 0.0,
            "biggest_win": 0,
        }


def add_stat(user_id: int, key: str, amount=1):
    ensure_user_stats(user_id)
    stats_db[str(user_id)][key] += amount
    save_json(STATS_FILE, stats_db)


def set_best_stat(user_id: int, key: str, value):
    ensure_user_stats(user_id)
    if value > stats_db[str(user_id)].get(key, 0):
        stats_db[str(user_id)][key] = value
        save_json(STATS_FILE, stats_db)


def register_win(user_id: int, amount: int):
    add_stat(user_id, "wins", 1)
    add_stat(user_id, "coins_won", amount)
    set_best_stat(user_id, "biggest_win", amount)


def register_loss(user_id: int):
    add_stat(user_id, "losses", 1)


def get_active_game(user_id: int, game_type: str | None = None):
    game = active_games.get(user_id)
    if not game:
        return None
    if game_type is not None and game.get("type") != game_type:
        return None
    return game


async def send_log(guild: discord.Guild, title: str, description: str, color: int = COLOR_LOG):
    ch = guild.get_channel(GAME_LOG_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        await ch.send(embed=discord.Embed(title=title, description=description, color=color))


def fmt_coins(value: int) -> str:
    return f"`{value}`"


# =========================================================
# DAILY
# =========================================================
def can_claim_daily(user_id: int):
    uid = str(user_id)
    data = daily_db.get(uid)
    if not data:
        return True, None

    try:
        last = datetime.fromisoformat(data["last_claim"])
    except Exception:
        return True, None

    next_claim = last + timedelta(hours=24)
    if now_utc() >= next_claim:
        return True, None
    return False, next_claim


def compute_daily_reward(user_id: int):
    uid = str(user_id)
    streak = 1

    if uid in daily_db:
        try:
            last = datetime.fromisoformat(daily_db[uid]["last_claim"])
            old_streak = int(daily_db[uid].get("streak", 0))
            delta = now_utc() - last
            if delta <= timedelta(hours=48):
                streak = old_streak + 1
            else:
                streak = 1
        except Exception:
            streak = 1

    reward = random.randint(1, 100)
    return reward, streak


# =========================================================
# WHEEL CONFIG
# =========================================================
WHEEL_CONFIG = {
    5: [
        {"type": "lose", "weight": 50},
        {"type": "coins", "amount": 3, "weight": 20},
        {"type": "coins", "amount": 5, "weight": 14},
        {"type": "coins", "amount": 7, "weight": 8},
        {"type": "bonus", "amount": 5, "weight": 8},
    ],
    10: [
        {"type": "lose", "weight": 44},
        {"type": "coins", "amount": 6, "weight": 20},
        {"type": "coins", "amount": 9, "weight": 14},
        {"type": "coins", "amount": 13, "weight": 10},
        {"type": "bonus", "amount": 10, "weight": 12},
    ],
    25: [
        {"type": "lose", "weight": 28},
        {"type": "coins", "amount": 16, "weight": 20},
        {"type": "coins", "amount": 24, "weight": 18},
        {"type": "coins", "amount": 32, "weight": 12},
        {"type": "coins", "amount": 45, "weight": 8},
        {"type": "bonus", "amount": 25, "weight": 14},
    ],
}

DISPLAY_POOL = [
    "💀 LOSE",
    "💰 +3 COINS",
    "💰 +5 COINS",
    "💰 +9 COINS",
    "💰 +16 COINS",
    "💰 +24 COINS",
    "💰 +32 COINS",
    "🎁 +5 BONUS",
    "🎁 +10 BONUS",
    "🎁 +25 BONUS",
]


def roll_wheel_reward(cost: int) -> dict:
    pool = WHEEL_CONFIG[cost]
    weights = [r["weight"] for r in pool]
    return random.choices(pool, weights=weights, k=1)[0].copy()


def reward_display(reward: dict) -> str:
    if reward["type"] == "lose":
        return "💀 LOSE"
    if reward["type"] == "coins":
        return f"💰 +{reward['amount']} COINS"
    if reward["type"] == "bonus":
        return f"🎁 +{reward['amount']} BONUS"
    return "❔ UNKNOWN"


def random_wheel_rows():
    return [random.choice(DISPLAY_POOL) for _ in range(5)]


# =========================================================
# EMBEDS
# =========================================================
def build_arcade_hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎮 GEN ARCADE HUB",
        description=(
            f"{premium_divider()}\n"
            f"Welcome to the **crazy coin arcade**.\n\n"
            f"**Games**\n"
            f"🎡 Wheel\n"
            f"🐔 Chicken Road\n"
            f"💣 Mines\n"
            f"🪙 Coinflip\n"
            f"🔼 Higher / Lower\n"
            f"📈 Crash\n"
            f"🎲 Dice Duel\n\n"
            f"**Other**\n"
            f"🎁 Daily Claim\n"
            f"👤 Profile\n"
            f"🏆 Leaderboard\n"
            f"💸 Transfer\n"
            f"💰 Balance\n"
            f"{premium_divider()}"
        ),
        color=COLOR_MAIN
    )
    embed.add_field(
        name="🔥 Notes",
        value="One active game at a time • old buttons auto fail cleanly • coins only",
        inline=False
    )
    embed.set_footer(text="Internal server coins only • No real money")
    return embed


def build_staff_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛠️ ARCADE STAFF PANEL",
        description=(
            f"{premium_divider()}\n"
            f"Manage user balances and profiles.\n"
            f"{premium_divider()}"
        ),
        color=COLOR_INFO
    )
    embed.add_field(name="➕ Add Coins", value="Give coins to a user", inline=False)
    embed.add_field(name="➖ Remove Coins", value="Remove coins from a user", inline=False)
    embed.add_field(name="🧾 Set Coins", value="Set exact balance", inline=False)
    embed.add_field(name="👤 Check User", value="View balance and stats", inline=False)
    return embed


def build_balance_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="💰 YOUR BALANCE",
        description=f"{premium_divider()}",
        color=COLOR_SUCCESS
    )
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Coins", value=fmt_coins(get_user_coins(member.id)), inline=True)
    return embed


def build_profile_embed(member: discord.Member) -> discord.Embed:
    ensure_user_stats(member.id)
    data = stats_db[str(member.id)]
    embed = discord.Embed(
        title="👤 ARCADE PROFILE",
        description=f"{premium_divider()}",
        color=COLOR_INFO
    )
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Coins", value=fmt_coins(get_user_coins(member.id)), inline=True)
    embed.add_field(name="Games", value=fmt_coins(data["games_played"]), inline=True)
    embed.add_field(name="Wins", value=fmt_coins(data["wins"]), inline=True)
    embed.add_field(name="Losses", value=fmt_coins(data["losses"]), inline=True)
    embed.add_field(name="Coins Won", value=fmt_coins(data["coins_won"]), inline=True)
    embed.add_field(name="Coins Spent", value=fmt_coins(data["coins_spent"]), inline=True)
    embed.add_field(name="Biggest Win", value=fmt_coins(data["biggest_win"]), inline=True)
    embed.add_field(name="Best Road", value=fmt_coins(data["best_road_steps"]), inline=True)
    embed.add_field(name="Best Mines", value=fmt_coins(data["best_mines_safe_hits"]), inline=True)
    embed.add_field(name="Best HiLo", value=fmt_coins(data["best_hilo_streak"]), inline=True)
    embed.add_field(name="Best Crash", value=f"`{data['best_crash_multi']:.2f}x`", inline=True)
    return embed


def build_wheel_info_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎡 WHEEL",
        description=(
            f"{premium_divider()}\n"
            f"Choose your spin tier.\n"
            f"Higher tiers feel better, but stay balanced.\n"
            f"{premium_divider()}"
        ),
        color=COLOR_INFO
    )
    return embed


def build_wheel_spin_embed(member: discord.Member, cost: int, rows: list[str], title: str) -> discord.Embed:
    top = "╔" + "═" * 29 + "╗"
    mid = "╠" + "═" * 29 + "╣"
    bottom = "╚" + "═" * 29 + "╝"

    rendered = [top]
    for i, row in enumerate(rows):
        marker = "▶" if i == 2 else " "
        text = f"{marker} {row}"[:27].ljust(27)
        rendered.append(f"║ {text} ║")
        if i == 1:
            rendered.append(mid)
    rendered.append(bottom)

    embed = discord.Embed(
        title=title,
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Spin Cost:** {fmt_coins(cost)}\n\n"
            f"```fix\n" + "\n".join(rendered) + "\n```\n"
            f"{premium_divider()}"
        ),
        color=COLOR_WARN
    )
    embed.set_footer(text="The arrow points to the final reward.")
    return embed


def build_wheel_result_embed(member: discord.Member, cost: int, reward: dict, balance_after: int) -> discord.Embed:
    title = "💀 YOU LOST" if reward["type"] == "lose" else "🎉 WHEEL RESULT"
    color = COLOR_DENY if reward["type"] == "lose" else COLOR_SUCCESS
    desc = "No reward this time." if reward["type"] == "lose" else reward_display(reward)

    embed = discord.Embed(title=title, description=f"{premium_divider()}", color=color)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Spin Cost", value=fmt_coins(cost), inline=True)
    embed.add_field(name="Reward", value=desc, inline=True)
    embed.add_field(name="Balance", value=fmt_coins(balance_after), inline=True)
    return embed


def build_road_start_embed(member: discord.Member, bet: int) -> discord.Embed:
    embed = discord.Embed(
        title="🐔 CHICKEN ROAD",
        description=(
            f"{premium_divider()}\n"
            f"Cross the road step by step.\n"
            f"The more steps, the higher the cashout.\n\n"
            f"**Bet:** {fmt_coins(bet)}\n"
            f"{premium_divider()}"
        ),
        color=COLOR_ROAD
    )
    return embed


def build_road_embed(member: discord.Member, state: dict, title: str) -> discord.Embed:
    lane = []
    position = state["position"]
    length = 7
    hit_next = state["danger_next"]

    for i in range(length):
        if i == position:
            lane.append("🐔")
        elif i == position + 1 and i < length:
            lane.append("🚗" if hit_next else "🛣️")
        else:
            lane.append("🛣️")

    road = " ".join(lane)
    embed = discord.Embed(
        title=title,
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Bet:** {fmt_coins(state['bet'])}\n"
            f"**Steps:** {fmt_coins(state['steps'])}\n"
            f"**Cashout:** {fmt_coins(state['cashout'])}\n\n"
            f"```fix\n{road}\n```\n"
            f"{premium_divider()}"
        ),
        color=COLOR_ROAD
    )
    return embed


def build_mines_config_embed() -> discord.Embed:
    embed = discord.Embed(
        title="💣 MINES",
        description=(
            f"{premium_divider()}\n"
            f"First choose your **bet**.\n"
            f"Then choose how many **mines** you want.\n\n"
            f"More mines = more risk = higher cashout.\n"
            f"{premium_divider()}"
        ),
        color=COLOR_MINES
    )
    return embed


def build_mines_start_embed(member: discord.Member, bet: int, mine_count: int, safe_hits: int, payout: int) -> discord.Embed:
    embed = discord.Embed(
        title="💣 MINES",
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Bet:** {fmt_coins(bet)}\n"
            f"**Mines:** {fmt_coins(mine_count)}\n"
            f"**Safe Hits:** {fmt_coins(safe_hits)}\n"
            f"**Cashout Value:** {fmt_coins(payout)}\n"
            f"{premium_divider()}"
        ),
        color=COLOR_MINES
    )
    return embed


def build_daily_embed(amount: int, streak: int, lucky: bool) -> discord.Embed:
    extra = "🔥 Lucky daily hit!" if lucky else "🎉 Random daily reward claimed."
    embed = discord.Embed(
        title="🎁 DAILY CLAIMED",
        description=(
            f"{premium_divider()}\n"
            f"You received **{amount}** coin(s).\n"
            f"**Streak:** {fmt_coins(streak)}\n"
            f"{extra}\n"
            f"{premium_divider()}"
        ),
        color=COLOR_SUCCESS
    )
    return embed


def build_coinflip_embed(member: discord.Member, bet: int, result_text: str, choice: str, win: bool, balance: int) -> discord.Embed:
    color = COLOR_SUCCESS if win else COLOR_DENY
    title = "🪙 COINFLIP WIN" if win else "🪙 COINFLIP LOSE"
    embed = discord.Embed(title=title, description=f"{premium_divider()}", color=color)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Bet", value=fmt_coins(bet), inline=True)
    embed.add_field(name="Your Pick", value=choice, inline=True)
    embed.add_field(name="Result", value=result_text, inline=True)
    embed.add_field(name="Balance", value=fmt_coins(balance), inline=False)
    return embed


def build_hilo_embed(member: discord.Member, game: dict, title: str) -> discord.Embed:
    current = game["current"]
    payout = hilo_payout(game["bet"], game["streak"])
    embed = discord.Embed(
        title=title,
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Bet:** {fmt_coins(game['bet'])}\n"
            f"**Current Number:** `{current}`\n"
            f"**Streak:** {fmt_coins(game['streak'])}\n"
            f"**Cashout Value:** {fmt_coins(payout)}\n"
            f"{premium_divider()}"
        ),
        color=COLOR_HILO
    )
    return embed


def build_crash_embed(member: discord.Member, game: dict, title: str) -> discord.Embed:
    multiplier = game["multiplier"]
    payout = int(round(game["bet"] * multiplier))
    bar = "🟥" * min(int(multiplier), 10) + "⬛" * max(0, 10 - min(int(multiplier), 10))
    embed = discord.Embed(
        title=title,
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Bet:** {fmt_coins(game['bet'])}\n"
            f"**Multiplier:** `{multiplier:.2f}x`\n"
            f"**Cashout Value:** {fmt_coins(payout)}\n\n"
            f"```fix\n{bar}\n```\n"
            f"{premium_divider()}"
        ),
        color=COLOR_CRASH
    )
    return embed


def build_dice_result_embed(member: discord.Member, bet: int, user_roll: int, bot_roll: int, payout: int, balance: int) -> discord.Embed:
    win = payout > 0
    embed = discord.Embed(
        title="🎲 DICE WIN" if win else "🎲 DICE LOSE",
        description=f"{premium_divider()}",
        color=COLOR_SUCCESS if win else COLOR_DENY
    )
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Bet", value=fmt_coins(bet), inline=True)
    embed.add_field(name="Your Roll", value=f"`{user_roll}`", inline=True)
    embed.add_field(name="Bot Roll", value=f"`{bot_roll}`", inline=True)
    embed.add_field(name="Payout", value=fmt_coins(payout), inline=True)
    embed.add_field(name="Balance", value=fmt_coins(balance), inline=True)
    return embed


# =========================================================
# MODALS
# =========================================================
class AddCoinsModal(discord.ui.Modal, title="Add Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="Amount", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        try:
            user_id = int(str(self.user_id_input).strip())
            amount = int(str(self.amount_input).strip())
        except ValueError:
            await interaction.response.send_message("Invalid user ID or amount.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("Amount must be greater than 0.", ephemeral=True)
            return

        add_user_coins(user_id, amount)
        member = interaction.guild.get_member(user_id)
        user_text = member.mention if member else f"`{user_id}`"

        await send_log(
            interaction.guild,
            "➕ Coins Added",
            f"**Staff:** {interaction.user.mention}\n**User:** {user_text}\n**Added:** {fmt_coins(amount)}\n**New Balance:** {fmt_coins(get_user_coins(user_id))}",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message("Coins added.", ephemeral=True)


class RemoveCoinsModal(discord.ui.Modal, title="Remove Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="Amount", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        try:
            user_id = int(str(self.user_id_input).strip())
            amount = int(str(self.amount_input).strip())
        except ValueError:
            await interaction.response.send_message("Invalid user ID or amount.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("Amount must be greater than 0.", ephemeral=True)
            return

        remove_user_coins(user_id, amount)
        member = interaction.guild.get_member(user_id)
        user_text = member.mention if member else f"`{user_id}`"

        await send_log(
            interaction.guild,
            "➖ Coins Removed",
            f"**Staff:** {interaction.user.mention}\n**User:** {user_text}\n**Removed:** {fmt_coins(amount)}\n**New Balance:** {fmt_coins(get_user_coins(user_id))}",
            color=COLOR_WARN
        )
        await interaction.response.send_message("Coins removed.", ephemeral=True)


class SetCoinsModal(discord.ui.Modal, title="Set Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="New Balance", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        try:
            user_id = int(str(self.user_id_input).strip())
            amount = int(str(self.amount_input).strip())
        except ValueError:
            await interaction.response.send_message("Invalid user ID or amount.", ephemeral=True)
            return
        if amount < 0:
            await interaction.response.send_message("Balance cannot be negative.", ephemeral=True)
            return

        set_user_coins(user_id, amount)
        member = interaction.guild.get_member(user_id)
        user_text = member.mention if member else f"`{user_id}`"

        await send_log(
            interaction.guild,
            "🧾 Coins Set",
            f"**Staff:** {interaction.user.mention}\n**User:** {user_text}\n**New Balance:** {fmt_coins(amount)}",
            color=COLOR_INFO
        )
        await interaction.response.send_message("Balance updated.", ephemeral=True)


class CheckUserModal(discord.ui.Modal, title="Check User"):
    user_id_input = discord.ui.TextInput(label="User ID", required=True, max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        try:
            user_id = int(str(self.user_id_input).strip())
        except ValueError:
            await interaction.response.send_message("Invalid user ID.", ephemeral=True)
            return

        ensure_user_stats(user_id)
        member = interaction.guild.get_member(user_id)
        data = stats_db[str(user_id)]
        embed = discord.Embed(title="👤 USER CHECK", color=COLOR_INFO)
        embed.add_field(name="User", value=member.mention if member else f"`{user_id}`", inline=False)
        embed.add_field(name="Coins", value=fmt_coins(get_user_coins(user_id)), inline=True)
        embed.add_field(name="Games", value=fmt_coins(data["games_played"]), inline=True)
        embed.add_field(name="Won", value=fmt_coins(data["coins_won"]), inline=True)
        embed.add_field(name="Spent", value=fmt_coins(data["coins_spent"]), inline=True)
        embed.add_field(name="Wins", value=fmt_coins(data["wins"]), inline=True)
        embed.add_field(name="Losses", value=fmt_coins(data["losses"]), inline=True)
        embed.add_field(name="Best Road", value=fmt_coins(data["best_road_steps"]), inline=True)
        embed.add_field(name="Best Mines", value=fmt_coins(data["best_mines_safe_hits"]), inline=True)
        embed.add_field(name="Best HiLo", value=fmt_coins(data["best_hilo_streak"]), inline=True)
        embed.add_field(name="Best Crash", value=f"`{data['best_crash_multi']:.2f}x`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TransferCoinsModal(discord.ui.Modal, title="Transfer Coins"):
    user_id_input = discord.ui.TextInput(label="Target User ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="Amount", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_user_id = int(str(self.user_id_input).strip())
            amount = int(str(self.amount_input).strip())
        except ValueError:
            await interaction.response.send_message("Invalid user ID or amount.", ephemeral=True)
            return

        if target_user_id == interaction.user.id:
            await interaction.response.send_message("You cannot transfer to yourself.", ephemeral=True)
            return

        if amount <= 0:
            await interaction.response.send_message("Amount must be greater than 0.", ephemeral=True)
            return

        if get_user_coins(interaction.user.id) < amount:
            await interaction.response.send_message("Not enough coins.", ephemeral=True)
            return

        remove_user_coins(interaction.user.id, amount)
        add_user_coins(target_user_id, amount)

        target_member = interaction.guild.get_member(target_user_id)
        target_text = target_member.mention if target_member else f"`{target_user_id}`"

        await send_log(
            interaction.guild,
            "💸 Coins Transferred",
            f"**From:** {interaction.user.mention}\n**To:** {target_text}\n**Amount:** {fmt_coins(amount)}",
            color=COLOR_INFO
        )
        await interaction.response.send_message(
            f"Transferred {fmt_coins(amount)} to {target_text}.",
            ephemeral=True
        )


# =========================================================
# VIEWS
# =========================================================
class ArcadeHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Wheel", style=discord.ButtonStyle.primary, emoji="🎡", custom_id="hub_wheel")
    async def wheel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_wheel_info_embed(), view=WheelSelectView(), ephemeral=True)

    @discord.ui.button(label="Chicken Road", style=discord.ButtonStyle.success, emoji="🐔", custom_id="hub_road")
    async def road_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🐔 CHICKEN ROAD",
                description=f"{premium_divider()}\nChoose a bet to start.\n{premium_divider()}",
                color=COLOR_ROAD
            ),
            view=RoadBetView(),
            ephemeral=True
        )

    @discord.ui.button(label="Mines", style=discord.ButtonStyle.danger, emoji="💣", custom_id="hub_mines")
    async def mines_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_mines_config_embed(), view=MinesBetView(), ephemeral=True)

    @discord.ui.button(label="Coinflip", style=discord.ButtonStyle.secondary, emoji="🪙", custom_id="hub_flip")
    async def flip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🪙 COINFLIP",
                description=f"{premium_divider()}\nChoose your bet first.\n{premium_divider()}",
                color=COLOR_FLIP
            ),
            view=CoinflipBetView(),
            ephemeral=True
        )

    @discord.ui.button(label="Higher/Lower", style=discord.ButtonStyle.secondary, emoji="🔼", custom_id="hub_hilo")
    async def hilo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🔼 HIGHER / LOWER",
                description=f"{premium_divider()}\nChoose your bet first.\n{premium_divider()}",
                color=COLOR_HILO
            ),
            view=HiLoBetView(),
            ephemeral=True
        )

    @discord.ui.button(label="Crash", style=discord.ButtonStyle.danger, emoji="📈", custom_id="hub_crash")
    async def crash_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📈 CRASH",
                description=f"{premium_divider()}\nChoose your bet first.\n{premium_divider()}",
                color=COLOR_CRASH
            ),
            view=CrashBetView(),
            ephemeral=True
        )

    @discord.ui.button(label="Dice Duel", style=discord.ButtonStyle.success, emoji="🎲", custom_id="hub_dice")
    async def dice_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🎲 DICE DUEL",
                description=f"{premium_divider()}\nChoose your bet.\nBeat the bot roll.\n{premium_divider()}",
                color=COLOR_DICE
            ),
            view=DiceBetView(),
            ephemeral=True
        )

    @discord.ui.button(label="Daily", style=discord.ButtonStyle.secondary, emoji="🎁", custom_id="hub_daily")
    async def daily_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, next_claim = can_claim_daily(interaction.user.id)
        if not ok:
            await interaction.response.send_message(
                f"You already claimed daily.\nNext claim: <t:{int(next_claim.timestamp())}:R>",
                ephemeral=True
            )
            return

        reward, streak = compute_daily_reward(interaction.user.id)
        add_user_coins(interaction.user.id, reward)

        daily_db[str(interaction.user.id)] = {
            "last_claim": now_iso(),
            "streak": streak
        }
        save_json(DAILY_FILE, daily_db)

        lucky = reward >= 90

        await send_log(
            interaction.guild,
            "🎁 Daily Claimed",
            f"**User:** {interaction.user.mention}\n**Reward:** {fmt_coins(reward)}\n**Streak:** {fmt_coins(streak)}",
            color=COLOR_SUCCESS
        )
        await interaction.response.send_message(embed=build_daily_embed(reward, streak, lucky), ephemeral=True)

    @discord.ui.button(label="Profile", style=discord.ButtonStyle.secondary, emoji="👤", custom_id="hub_profile")
    async def profile_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_profile_embed(interaction.user), ephemeral=True)

    @discord.ui.button(label="Balance", style=discord.ButtonStyle.secondary, emoji="💰", custom_id="hub_balance")
    async def balance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_balance_embed(interaction.user), ephemeral=True)

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.secondary, emoji="💸", custom_id="hub_transfer")
    async def transfer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferCoinsModal())

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, emoji="🏆", custom_id="hub_leaderboard")
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        sorted_users = sorted(coins_db.items(), key=lambda x: int(x[1]), reverse=True)[:10]
        if not sorted_users:
            await interaction.response.send_message("No leaderboard data yet.", ephemeral=True)
            return

        lines = []
        for i, (uid, coins) in enumerate(sorted_users, start=1):
            member = interaction.guild.get_member(int(uid))
            name = member.mention if member else f"`{uid}`"
            lines.append(f"**{i}.** {name} — {fmt_coins(int(coins))}")

        embed = discord.Embed(
            title="🏆 COIN LEADERBOARD",
            description="\n".join(lines),
            color=COLOR_INFO
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class WheelSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Spin 5", style=discord.ButtonStyle.primary, emoji="🎡")
    async def spin5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await perform_wheel_spin(interaction, 5)

    @discord.ui.button(label="Spin 10", style=discord.ButtonStyle.success, emoji="✨")
    async def spin10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await perform_wheel_spin(interaction, 10)

    @discord.ui.button(label="Spin 25", style=discord.ButtonStyle.danger, emoji="💎")
    async def spin25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await perform_wheel_spin(interaction, 25)


class RoadBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Bet 5", style=discord.ButtonStyle.primary, emoji="🐔")
    async def bet5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_road_game(interaction, 5)

    @discord.ui.button(label="Bet 10", style=discord.ButtonStyle.success, emoji="🚗")
    async def bet10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_road_game(interaction, 10)

    @discord.ui.button(label="Bet 25", style=discord.ButtonStyle.danger, emoji="💥")
    async def bet25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_road_game(interaction, 25)


class RoadGameView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    @discord.ui.button(label="Go", style=discord.ButtonStyle.success, emoji="➡️")
    async def go_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return
        await road_step(interaction)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.primary, emoji="💰")
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return
        await road_cashout(interaction)


class MinesBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    async def _go(self, interaction: discord.Interaction, bet: int):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="💣 CHOOSE MINES",
                description=(
                    f"{premium_divider()}\n"
                    f"**Bet:** {fmt_coins(bet)}\n"
                    f"Now choose how many mines you want.\n"
                    f"{premium_divider()}"
                ),
                color=COLOR_MINES
            ),
            view=MinesCountView(bet),
            ephemeral=True
        )

    @discord.ui.button(label="Bet 5", style=discord.ButtonStyle.primary, emoji="💣")
    async def bet5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, 5)

    @discord.ui.button(label="Bet 10", style=discord.ButtonStyle.success, emoji="💥")
    async def bet10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, 10)

    @discord.ui.button(label="Bet 25", style=discord.ButtonStyle.danger, emoji="🔥")
    async def bet25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, 25)


class MinesCountView(discord.ui.View):
    def __init__(self, bet: int):
        super().__init__(timeout=120)
        self.bet = bet

    @discord.ui.button(label="1 Mine", style=discord.ButtonStyle.primary, emoji="1️⃣")
    async def one(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_mines_game(interaction, self.bet, 1)

    @discord.ui.button(label="2 Mines", style=discord.ButtonStyle.success, emoji="2️⃣")
    async def two(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_mines_game(interaction, self.bet, 2)

    @discord.ui.button(label="3 Mines", style=discord.ButtonStyle.danger, emoji="3️⃣")
    async def three(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_mines_game(interaction, self.bet, 3)

    @discord.ui.button(label="4 Mines", style=discord.ButtonStyle.secondary, emoji="4️⃣")
    async def four(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_mines_game(interaction, self.bet, 4)


class MinesGameView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id
        for i in range(16):
            self.add_item(MinesTileButton(i))

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success, emoji="💰", row=4)
    async def cash_out(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return
        await mines_cashout(interaction)


class MinesTileButton(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(label="?", style=discord.ButtonStyle.secondary, row=index // 4)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        game = get_active_game(interaction.user.id, "mines")
        if not game:
            await interaction.response.send_message("This game is already finished. Start a new one.", ephemeral=True)
            return
        if self.index in game["opened"]:
            await interaction.response.send_message("This tile is already opened.", ephemeral=True)
            return
        await mines_pick(interaction, self.index)


class CoinflipBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    async def _start(self, interaction: discord.Interaction, bet: int):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🪙 COINFLIP",
                description=f"{premium_divider()}\n**Bet:** {fmt_coins(bet)}\nChoose heads or tails.\n{premium_divider()}",
                color=COLOR_FLIP
            ),
            view=CoinflipChoiceView(bet),
            ephemeral=True
        )

    @discord.ui.button(label="Bet 5", style=discord.ButtonStyle.primary, emoji="🪙")
    async def bet5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, 5)

    @discord.ui.button(label="Bet 10", style=discord.ButtonStyle.success, emoji="💰")
    async def bet10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, 10)

    @discord.ui.button(label="Bet 25", style=discord.ButtonStyle.danger, emoji="🔥")
    async def bet25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, 25)


class CoinflipChoiceView(discord.ui.View):
    def __init__(self, bet: int):
        super().__init__(timeout=120)
        self.bet = bet

    @discord.ui.button(label="Heads", style=discord.ButtonStyle.primary, emoji="👑")
    async def heads(self, interaction: discord.Interaction, button: discord.ui.Button):
        await play_coinflip(interaction, self.bet, "Heads")

    @discord.ui.button(label="Tails", style=discord.ButtonStyle.secondary, emoji="🌙")
    async def tails(self, interaction: discord.Interaction, button: discord.ui.Button):
        await play_coinflip(interaction, self.bet, "Tails")


class HiLoBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Bet 5", style=discord.ButtonStyle.primary, emoji="🔼")
    async def bet5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_hilo_game(interaction, 5)

    @discord.ui.button(label="Bet 10", style=discord.ButtonStyle.success, emoji="🔢")
    async def bet10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_hilo_game(interaction, 10)

    @discord.ui.button(label="Bet 25", style=discord.ButtonStyle.danger, emoji="🔥")
    async def bet25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_hilo_game(interaction, 25)


class HiLoGameView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    @discord.ui.button(label="Higher", style=discord.ButtonStyle.success, emoji="🔼")
    async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return
        await hilo_guess(interaction, "higher")

    @discord.ui.button(label="Lower", style=discord.ButtonStyle.danger, emoji="🔽")
    async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return
        await hilo_guess(interaction, "lower")

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.primary, emoji="💰")
    async def cash_out(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return
        await hilo_cashout(interaction)


class CrashBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Bet 5", style=discord.ButtonStyle.primary, emoji="📈")
    async def bet5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_crash_game(interaction, 5)

    @discord.ui.button(label="Bet 10", style=discord.ButtonStyle.success, emoji="🚀")
    async def bet10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_crash_game(interaction, 10)

    @discord.ui.button(label="Bet 25", style=discord.ButtonStyle.danger, emoji="💥")
    async def bet25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_crash_game(interaction, 25)


class CrashGameView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    @discord.ui.button(label="Boost", style=discord.ButtonStyle.success, emoji="📈")
    async def boost(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return
        await crash_boost(interaction)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.primary, emoji="💰")
    async def cash_out(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return
        await crash_cashout(interaction)


class DiceBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Bet 5", style=discord.ButtonStyle.primary, emoji="🎲")
    async def bet5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await play_dice(interaction, 5)

    @discord.ui.button(label="Bet 10", style=discord.ButtonStyle.success, emoji="🎲")
    async def bet10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await play_dice(interaction, 10)

    @discord.ui.button(label="Bet 25", style=discord.ButtonStyle.danger, emoji="🎲")
    async def bet25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await play_dice(interaction, 25)


class StaffPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Add Coins", style=discord.ButtonStyle.success, emoji="➕", custom_id="staff_add")
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(AddCoinsModal())

    @discord.ui.button(label="Remove Coins", style=discord.ButtonStyle.danger, emoji="➖", custom_id="staff_remove")
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(RemoveCoinsModal())

    @discord.ui.button(label="Set Coins", style=discord.ButtonStyle.primary, emoji="🧾", custom_id="staff_set")
    async def set_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(SetCoinsModal())

    @discord.ui.button(label="Check User", style=discord.ButtonStyle.secondary, emoji="👤", custom_id="staff_check")
    async def check_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(CheckUserModal())


# =========================================================
# GAME LOGIC: WHEEL
# =========================================================
async def perform_wheel_spin(interaction: discord.Interaction, cost: int):
    member = interaction.user
    guild = interaction.guild

    if not guild or not isinstance(member, discord.Member):
        await interaction.response.send_message("Server only.", ephemeral=True)
        return

    if member.id in active_games:
        await interaction.response.send_message("Finish your current game first.", ephemeral=True)
        return

    balance = get_user_coins(member.id)
    if balance < cost:
        await interaction.response.send_message(f"You need {fmt_coins(cost)}. Balance: {fmt_coins(balance)}", ephemeral=True)
        return

    active_games[member.id] = {"type": "wheel"}
    try:
        remove_user_coins(member.id, cost)
        add_stat(member.id, "games_played", 1)
        add_stat(member.id, "wheel_spins", 1)
        add_stat(member.id, "coins_spent", cost)

        await interaction.response.send_message(
            embed=build_wheel_spin_embed(member, cost, random_wheel_rows(), "🎰 STARTING SPIN..."),
            ephemeral=True
        )
        msg = await interaction.original_response()

        final_reward = roll_wheel_reward(cost)
        final_text = reward_display(final_reward)
        delays = [0.12, 0.14, 0.16, 0.20, 0.25, 0.31, 0.40, 0.52, 0.70]

        for i, delay in enumerate(delays):
            rows = random_wheel_rows()
            if i >= len(delays) - 2:
                rows[3] = final_text
            if i == len(delays) - 1:
                rows[2] = final_text

            title = "🎰 SPINNING..." if i < len(delays) - 1 else "🎯 FINAL STOP..."
            await msg.edit(embed=build_wheel_spin_embed(member, cost, rows, title), view=None)
            await asyncio.sleep(delay)

        if final_reward["type"] == "lose":
            register_loss(member.id)
        elif final_reward["type"] in ("coins", "bonus"):
            add_user_coins(member.id, final_reward["amount"])
            register_win(member.id, final_reward["amount"])

        balance_after = get_user_coins(member.id)
        await msg.edit(embed=build_wheel_result_embed(member, cost, final_reward, balance_after), view=None)

        await send_log(
            guild,
            "🎡 Wheel Spin",
            f"**User:** {member.mention}\n**Cost:** {fmt_coins(cost)}\n**Reward:** `{reward_display(final_reward)}`\n**Balance:** {fmt_coins(balance_after)}",
            color=COLOR_DENY if final_reward["type"] == "lose" else COLOR_SUCCESS
        )
    finally:
        active_games.pop(member.id, None)


# =========================================================
# GAME LOGIC: CHICKEN ROAD
# =========================================================
def road_cashout_for_steps(bet: int, steps: int) -> int:
    multipliers = {
        0: 0,
        1: 1.15,
        2: 1.35,
        3: 1.65,
        4: 2.05,
        5: 2.60,
        6: 3.30,
    }
    return int(round(bet * multipliers.get(steps, 3.30)))


def road_crash_chance(step_number: int) -> float:
    mapping = {
        1: 0.18,
        2: 0.22,
        3: 0.28,
        4: 0.35,
        5: 0.44,
        6: 0.55,
    }
    return mapping.get(step_number, 0.65)


async def start_road_game(interaction: discord.Interaction, bet: int):
    member = interaction.user
    guild = interaction.guild

    if not guild or not isinstance(member, discord.Member):
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    if member.id in active_games:
        await interaction.response.send_message("Finish your current game first.", ephemeral=True)
        return
    if get_user_coins(member.id) < bet:
        await interaction.response.send_message("Not enough coins.", ephemeral=True)
        return

    remove_user_coins(member.id, bet)
    add_stat(member.id, "games_played", 1)
    add_stat(member.id, "road_runs", 1)
    add_stat(member.id, "coins_spent", bet)

    state = {
        "type": "road",
        "bet": bet,
        "steps": 0,
        "cashout": 0,
        "position": 0,
        "danger_next": False,
    }
    active_games[member.id] = state

    await interaction.response.send_message(
        embed=build_road_start_embed(member, bet),
        view=RoadGameView(member.id),
        ephemeral=True
    )


async def road_step(interaction: discord.Interaction):
    member = interaction.user
    game = get_active_game(member.id, "road")
    if not game:
        await interaction.response.send_message("This game is already finished. Start a new one.", ephemeral=True)
        return

    next_step = game["steps"] + 1
    crash = random.random() < road_crash_chance(next_step)

    if crash:
        game["danger_next"] = True
        await interaction.response.edit_message(
            embed=build_road_embed(member, game, "💥 YOU GOT HIT"),
            view=None
        )
        register_loss(member.id)
        await send_log(
            interaction.guild,
            "🐔 Road Lost",
            f"**User:** {member.mention}\n**Bet:** {fmt_coins(game['bet'])}\n**Steps:** {fmt_coins(game['steps'])}\n**Lost:** {fmt_coins(game['bet'])}",
            color=COLOR_DENY
        )
        active_games.pop(member.id, None)
        return

    game["steps"] = next_step
    game["position"] = min(game["position"] + 1, 6)
    game["cashout"] = road_cashout_for_steps(game["bet"], game["steps"])
    game["danger_next"] = False
    set_best_stat(member.id, "best_road_steps", game["steps"])

    if game["steps"] >= 6:
        add_user_coins(member.id, game["cashout"])
        register_win(member.id, game["cashout"])
        await interaction.response.edit_message(
            embed=build_road_embed(member, game, "🏁 ROAD CLEARED"),
            view=None
        )
        await send_log(
            interaction.guild,
            "🐔 Road Cleared",
            f"**User:** {member.mention}\n**Bet:** {fmt_coins(game['bet'])}\n**Won:** {fmt_coins(game['cashout'])}",
            color=COLOR_SUCCESS
        )
        active_games.pop(member.id, None)
        return

    await interaction.response.edit_message(
        embed=build_road_embed(member, game, "🐔 SAFE STEP"),
        view=RoadGameView(member.id)
    )


async def road_cashout(interaction: discord.Interaction):
    member = interaction.user
    game = get_active_game(member.id, "road")
    if not game:
        await interaction.response.send_message("This game is already finished. Start a new one.", ephemeral=True)
        return
    if game["steps"] == 0:
        await interaction.response.send_message("Take at least one step first.", ephemeral=True)
        return

    payout = game["cashout"]
    add_user_coins(member.id, payout)
    register_win(member.id, payout)

    await interaction.response.edit_message(
        embed=build_road_embed(member, game, "💰 CASHED OUT"),
        view=None
    )

    await send_log(
        interaction.guild,
        "🐔 Road Cashout",
        f"**User:** {member.mention}\n**Bet:** {fmt_coins(game['bet'])}\n**Steps:** {fmt_coins(game['steps'])}\n**Won:** {fmt_coins(payout)}",
        color=COLOR_SUCCESS
    )
    active_games.pop(member.id, None)


# =========================================================
# GAME LOGIC: MINES
# =========================================================
def mines_cashout_value(bet: int, safe_hits: int, mine_count: int) -> int:
    multiplier = 1.0
    remaining_tiles = 16
    remaining_safe = 16 - mine_count

    for _ in range(safe_hits):
        if remaining_safe <= 0:
            break
        chance_scale = remaining_tiles / remaining_safe
        risk_boost = 1 + (mine_count * 0.18)
        multiplier *= (chance_scale * 0.55 * risk_boost)
        remaining_tiles -= 1
        remaining_safe -= 1

    return max(0, int(round(bet * multiplier)))


async def start_mines_game(interaction: discord.Interaction, bet: int, mine_count: int):
    member = interaction.user
    guild = interaction.guild

    if not guild or not isinstance(member, discord.Member):
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    if member.id in active_games:
        await interaction.response.send_message("Finish your current game first.", ephemeral=True)
        return
    if get_user_coins(member.id) < bet:
        await interaction.response.send_message("Not enough coins.", ephemeral=True)
        return

    remove_user_coins(member.id, bet)
    add_stat(member.id, "games_played", 1)
    add_stat(member.id, "mines_runs", 1)
    add_stat(member.id, "coins_spent", bet)

    mines = random.sample(range(16), mine_count)
    state = {
        "type": "mines",
        "bet": bet,
        "mine_count": mine_count,
        "mines": mines,
        "opened": [],
        "safe_hits": 0,
    }
    active_games[member.id] = state

    await interaction.response.send_message(
        embed=build_mines_start_embed(member, bet, mine_count, 0, 0),
        view=MinesGameView(member.id),
        ephemeral=True
    )


async def mines_pick(interaction: discord.Interaction, index: int):
    member = interaction.user
    game = get_active_game(member.id, "mines")
    if not game:
        await interaction.response.send_message("This game is already finished. Start a new one.", ephemeral=True)
        return

    game["opened"].append(index)

    if index in game["mines"]:
        view = MinesGameView(member.id)
        for child in view.children:
            if isinstance(child, MinesTileButton):
                if child.index in game["mines"]:
                    child.label = "💣"
                    child.style = discord.ButtonStyle.danger
                elif child.index in game["opened"]:
                    child.label = "✅"
                    child.style = discord.ButtonStyle.success
                child.disabled = True
            elif isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="💥 BOOM",
                description=(
                    f"{premium_divider()}\n"
                    f"**User:** {member.mention}\n"
                    f"**Bet:** {fmt_coins(game['bet'])}\n"
                    f"**Mines:** {fmt_coins(game['mine_count'])}\n"
                    f"**Safe Hits:** {fmt_coins(game['safe_hits'])}\n"
                    f"**Result:** You hit a mine.\n"
                    f"{premium_divider()}"
                ),
                color=COLOR_DENY
            ),
            view=view
        )

        register_loss(member.id)
        await send_log(
            interaction.guild,
            "💣 Mines Lost",
            f"**User:** {member.mention}\n**Bet:** {fmt_coins(game['bet'])}\n**Mines:** {fmt_coins(game['mine_count'])}\n**Safe Hits:** {fmt_coins(game['safe_hits'])}",
            color=COLOR_DENY
        )
        active_games.pop(member.id, None)
        return

    game["safe_hits"] += 1
    set_best_stat(member.id, "best_mines_safe_hits", game["safe_hits"])
    payout = mines_cashout_value(game["bet"], game["safe_hits"], game["mine_count"])

    view = MinesGameView(member.id)
    for child in view.children:
        if isinstance(child, MinesTileButton):
            if child.index in game["opened"]:
                child.label = "✅"
                child.style = discord.ButtonStyle.success
                child.disabled = True

    await interaction.response.edit_message(
        embed=build_mines_start_embed(member, game["bet"], game["mine_count"], game["safe_hits"], payout),
        view=view
    )


async def mines_cashout(interaction: discord.Interaction):
    member = interaction.user
    game = get_active_game(member.id, "mines")
    if not game:
        await interaction.response.send_message("This game is already finished. Start a new one.", ephemeral=True)
        return
    if game["safe_hits"] == 0:
        await interaction.response.send_message("Open at least one safe tile first.", ephemeral=True)
        return

    payout = mines_cashout_value(game["bet"], game["safe_hits"], game["mine_count"])
    add_user_coins(member.id, payout)
    register_win(member.id, payout)

    view = MinesGameView(member.id)
    for child in view.children:
        child.disabled = True
        if isinstance(child, MinesTileButton) and child.index in game["opened"]:
            child.label = "✅"
            child.style = discord.ButtonStyle.success

    await interaction.response.edit_message(
        embed=discord.Embed(
            title="💰 CASHED OUT",
            description=(
                f"{premium_divider()}\n"
                f"**User:** {member.mention}\n"
                f"**Bet:** {fmt_coins(game['bet'])}\n"
                f"**Mines:** {fmt_coins(game['mine_count'])}\n"
                f"**Safe Hits:** {fmt_coins(game['safe_hits'])}\n"
                f"**Won:** {fmt_coins(payout)}\n"
                f"{premium_divider()}"
            ),
            color=COLOR_SUCCESS
        ),
        view=view
    )

    await send_log(
        interaction.guild,
        "💣 Mines Cashout",
        f"**User:** {member.mention}\n**Bet:** {fmt_coins(game['bet'])}\n**Mines:** {fmt_coins(game['mine_count'])}\n**Safe Hits:** {fmt_coins(game['safe_hits'])}\n**Won:** {fmt_coins(payout)}",
        color=COLOR_SUCCESS
    )
    active_games.pop(member.id, None)


# =========================================================
# GAME LOGIC: COINFLIP
# =========================================================
async def play_coinflip(interaction: discord.Interaction, bet: int, choice: str):
    member = interaction.user
    guild = interaction.guild

    if not guild or not isinstance(member, discord.Member):
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    if member.id in active_games:
        await interaction.response.send_message("Finish your current game first.", ephemeral=True)
        return
    if get_user_coins(member.id) < bet:
        await interaction.response.send_message("Not enough coins.", ephemeral=True)
        return

    active_games[member.id] = {"type": "coinflip"}
    try:
        remove_user_coins(member.id, bet)
        add_stat(member.id, "games_played", 1)
        add_stat(member.id, "coinflip_runs", 1)
        add_stat(member.id, "coins_spent", bet)

        await interaction.response.defer(ephemeral=True, thinking=True)
        await asyncio.sleep(1.0)

        result = random.choice(["Heads", "Tails"])
        win = result == choice

        if win:
            payout = int(round(bet * 1.9))
            add_user_coins(member.id, payout)
            register_win(member.id, payout)
        else:
            register_loss(member.id)

        balance = get_user_coins(member.id)
        await interaction.followup.send(
            embed=build_coinflip_embed(member, bet, result, choice, win, balance),
            ephemeral=True
        )

        await send_log(
            guild,
            "🪙 Coinflip",
            f"**User:** {member.mention}\n**Bet:** {fmt_coins(bet)}\n**Pick:** `{choice}`\n**Result:** `{result}`\n**Win:** `{win}`\n**Balance:** {fmt_coins(balance)}",
            color=COLOR_SUCCESS if win else COLOR_DENY
        )
    finally:
        active_games.pop(member.id, None)


# =========================================================
# GAME LOGIC: HIGHER / LOWER
# =========================================================
