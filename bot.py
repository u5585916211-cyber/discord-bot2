import os
import json
import random
import asyncio
import traceback
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
            "wins": 0,
            "losses": 0,
            "best_road_steps": 0,
            "best_mines_safe_hits": 0,
            "best_hilo_streak": 0,
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


def fmt_coins(value: int) -> str:
    return f"`{value}`"


async def safe_interaction_reply(
    interaction: discord.Interaction,
    content: str | None = None,
    embed: discord.Embed | None = None,
    ephemeral: bool = True
):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
    except Exception:
        pass


async def send_log(guild: discord.Guild, title: str, description: str, color: int = COLOR_LOG):
    ch = guild.get_channel(GAME_LOG_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        await ch.send(embed=discord.Embed(title=title, description=description, color=color))


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
            f"Pick a game and use your **server coins**.\n\n"
            f"**Games**\n"
            f"🎡 Wheel\n"
            f"🐔 Chicken Road\n"
            f"💣 Mines\n"
            f"🪙 Coinflip\n"
            f"🔼 Higher / Lower\n\n"
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
    embed = discord.Embed(title="💰 YOUR BALANCE", description=premium_divider(), color=COLOR_SUCCESS)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Coins", value=fmt_coins(get_user_coins(member.id)), inline=True)
    return embed


def build_profile_embed(member: discord.Member) -> discord.Embed:
    ensure_user_stats(member.id)
    data = stats_db[str(member.id)]
    embed = discord.Embed(title="👤 ARCADE PROFILE", description=premium_divider(), color=COLOR_INFO)
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
    return embed


def build_wheel_info_embed() -> discord.Embed:
    return discord.Embed(
        title="🎡 WHEEL",
        description=f"{premium_divider()}\nChoose your spin tier.\n{premium_divider()}",
        color=COLOR_INFO
    )


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
    return embed


def build_wheel_result_embed(member: discord.Member, cost: int, reward: dict, balance_after: int) -> discord.Embed:
    title = "💀 YOU LOST" if reward["type"] == "lose" else "🎉 WHEEL RESULT"
    color = COLOR_DENY if reward["type"] == "lose" else COLOR_SUCCESS
    desc = "No reward this time." if reward["type"] == "lose" else reward_display(reward)

    embed = discord.Embed(title=title, description=premium_divider(), color=color)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Spin Cost", value=fmt_coins(cost), inline=True)
    embed.add_field(name="Reward", value=desc, inline=True)
    embed.add_field(name="Balance", value=fmt_coins(balance_after), inline=True)
    return embed


def build_road_start_embed(member: discord.Member, bet: int) -> discord.Embed:
    return discord.Embed(
        title="🐔 CHICKEN ROAD",
        description=(
            f"{premium_divider()}\n"
            f"Cross the road step by step.\n\n"
            f"**Bet:** {fmt_coins(bet)}\n"
            f"{premium_divider()}"
        ),
        color=COLOR_ROAD
    )


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
    return discord.Embed(
        title="💣 MINES",
        description=(
            f"{premium_divider()}\n"
            f"Choose bet, then mine count.\n"
            f"More mines = more payout.\n"
            f"{premium_divider()}"
        ),
        color=COLOR_MINES
    )


def build_mines_start_embed(member: discord.Member, bet: int, mine_count: int, safe_hits: int, payout: int) -> discord.Embed:
    embed = discord.Embed(title="💣 MINES", description=premium_divider(), color=COLOR_MINES)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Bet", value=fmt_coins(bet), inline=True)
    embed.add_field(name="Mines", value=fmt_coins(mine_count), inline=True)
    embed.add_field(name="Safe Hits", value=fmt_coins(safe_hits), inline=True)
    embed.add_field(name="Cashout", value=fmt_coins(payout), inline=False)
    return embed


def build_daily_embed(amount: int, streak: int, lucky: bool) -> discord.Embed:
    extra = "🔥 Lucky daily hit!" if lucky else "🎉 Random daily reward claimed."
    return discord.Embed(
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


def build_coinflip_embed(member: discord.Member, bet: int, result_text: str, choice: str, win: bool, balance: int) -> discord.Embed:
    color = COLOR_SUCCESS if win else COLOR_DENY
    title = "🪙 COINFLIP WIN" if win else "🪙 COINFLIP LOSE"
    embed = discord.Embed(title=title, description=premium_divider(), color=color)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Bet", value=fmt_coins(bet), inline=True)
    embed.add_field(name="Your Pick", value=choice, inline=True)
    embed.add_field(name="Result", value=result_text, inline=True)
    embed.add_field(name="Balance", value=fmt_coins(balance), inline=False)
    return embed


def build_hilo_embed(member: discord.Member, game: dict, title: str) -> discord.Embed:
    current = game["current"]
    payout = hilo_payout(game["bet"], game["streak"])
    embed = discord.Embed(title=title, description=premium_divider(), color=COLOR_HILO)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Bet", value=fmt_coins(game["bet"]), inline=True)
    embed.add_field(name="Current Number", value=f"`{current}`", inline=True)
    embed.add_field(name="Streak", value=fmt_coins(game["streak"]), inline=True)
    embed.add_field(name="Cashout", value=fmt_coins(payout), inline=False)
    return embed


# =========================================================
# MODALS
# =========================================================
class AddCoinsModal(discord.ui.Modal, title="Add Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="Amount", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not is_staff(interaction.user):
                await interaction.response.send_message("Staff only.", ephemeral=True)
                return
            user_id = int(str(self.user_id_input).strip())
            amount = int(str(self.amount_input).strip())
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
        except Exception as e:
            print("AddCoinsModal error:", e)
            traceback.print_exc()
            await safe_interaction_reply(interaction, f"Error: `{e}`")


class RemoveCoinsModal(discord.ui.Modal, title="Remove Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="Amount", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not is_staff(interaction.user):
                await interaction.response.send_message("Staff only.", ephemeral=True)
                return
            user_id = int(str(self.user_id_input).strip())
            amount = int(str(self.amount_input).strip())
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
        except Exception as e:
            print("RemoveCoinsModal error:", e)
            traceback.print_exc()
            await safe_interaction_reply(interaction, f"Error: `{e}`")


class SetCoinsModal(discord.ui.Modal, title="Set Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="New Balance", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not is_staff(interaction.user):
                await interaction.response.send_message("Staff only.", ephemeral=True)
                return
            user_id = int(str(self.user_id_input).strip())
            amount = int(str(self.amount_input).strip())
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
        except Exception as e:
            print("SetCoinsModal error:", e)
            traceback.print_exc()
            await safe_interaction_reply(interaction, f"Error: `{e}`")


class CheckUserModal(discord.ui.Modal, title="Check User"):
    user_id_input = discord.ui.TextInput(label="User ID", required=True, max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if not is_staff(interaction.user):
                await interaction.response.send_message("Staff only.", ephemeral=True)
                return
            user_id = int(str(self.user_id_input).strip())

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
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print("CheckUserModal error:", e)
            traceback.print_exc()
            await safe_interaction_reply(interaction, f"Error: `{e}`")


class TransferCoinsModal(discord.ui.Modal, title="Transfer Coins"):
    user_id_input = discord.ui.TextInput(label="Target User ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="Amount", required=True, max_length=10)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_user_id = int(str(self.user_id_input).strip())
            amount = int(str(self.amount_input).strip())

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
            await interaction.response.send_message(f"Transferred {fmt_coins(amount)} to {target_text}.", ephemeral=True)
        except Exception as e:
            print("TransferCoinsModal error:", e)
            traceback.print_exc()
            await safe_interaction_reply(interaction, f"Error: `{e}`")


# =========================================================
# GAME LOGIC
# =========================================================
def road_cashout_for_steps(bet: int, steps: int) -> int:
    multipliers = {0: 0, 1: 1.15, 2: 1.35, 3: 1.65, 4: 2.05, 5: 2.60, 6: 3.30}
    return int(round(bet * multipliers.get(steps, 3.30)))


def road_crash_chance(step_number: int) -> float:
    mapping = {1: 0.18, 2: 0.22, 3: 0.28, 4: 0.35, 5: 0.44, 6: 0.55}
    return mapping.get(step_number, 0.65)


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


def hilo_payout(bet: int, streak: int) -> int:
    multipliers = {0: 0, 1: 1.35, 2: 1.90, 3: 2.80, 4: 4.10, 5: 6.00}
    return int(round(bet * multipliers.get(streak, 6.00)))


async def start_road_game(interaction: discord.Interaction, bet: int):
    member = interaction.user
    if member.id in active_games:
        await safe_interaction_reply(interaction, "Finish your current game first.")
        return
    if get_user_coins(member.id) < bet:
        await safe_interaction_reply(interaction, "Not enough coins.")
        return

    remove_user_coins(member.id, bet)
    add_stat(member.id, "games_played", 1)
    add_stat(member.id, "road_runs", 1)
    add_stat(member.id, "coins_spent", bet)

    active_games[member.id] = {
        "type": "road",
        "bet": bet,
        "steps": 0,
        "cashout": 0,
        "position": 0,
        "danger_next": False,
    }

    await safe_interaction_reply(
        interaction,
        embed=build_road_start_embed(member, bet),
    )
    if interaction.response.is_done():
        try:
            msg = await interaction.original_response()
            await msg.edit(view=RoadGameView(member.id))
        except Exception:
            pass


async def road_step(interaction: discord.Interaction):
    member = interaction.user
    game = get_active_game(member.id, "road")
    if not game:
        await safe_interaction_reply(interaction, "This game is already finished. Start a new one.")
        return

    next_step = game["steps"] + 1
    crash = random.random() < road_crash_chance(next_step)

    if crash:
        game["danger_next"] = True
        await interaction.response.edit_message(embed=build_road_embed(member, game, "💥 YOU GOT HIT"), view=None)
        register_loss(member.id)
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
        await interaction.response.edit_message(embed=build_road_embed(member, game, "🏁 ROAD CLEARED"), view=None)
        active_games.pop(member.id, None)
        return

    await interaction.response.edit_message(embed=build_road_embed(member, game, "🐔 SAFE STEP"), view=RoadGameView(member.id))


async def road_cashout(interaction: discord.Interaction):
    member = interaction.user
    game = get_active_game(member.id, "road")
    if not game:
        await safe_interaction_reply(interaction, "This game is already finished. Start a new one.")
        return
    if game["steps"] == 0:
        await safe_interaction_reply(interaction, "Take at least one step first.")
        return

    payout = game["cashout"]
    add_user_coins(member.id, payout)
    register_win(member.id, payout)
    await interaction.response.edit_message(embed=build_road_embed(member, game, "💰 CASHED OUT"), view=None)
    active_games.pop(member.id, None)


async def start_mines_game(interaction: discord.Interaction, bet: int, mine_count: int):
    member = interaction.user
    if member.id in active_games:
        await safe_interaction_reply(interaction, "Finish your current game first.")
        return
    if get_user_coins(member.id) < bet:
        await safe_interaction_reply(interaction, "Not enough coins.")
        return

    remove_user_coins(member.id, bet)
    add_stat(member.id, "games_played", 1)
    add_stat(member.id, "mines_runs", 1)
    add_stat(member.id, "coins_spent", bet)

    active_games[member.id] = {
        "type": "mines",
        "bet": bet,
        "mine_count": mine_count,
        "mines": random.sample(range(16), mine_count),
        "opened": [],
        "safe_hits": 0,
    }

    await safe_interaction_reply(interaction, embed=build_mines_start_embed(member, bet, mine_count, 0, 0))
    if interaction.response.is_done():
        try:
            msg = await interaction.original_response()
            await msg.edit(view=MinesGameView(member.id))
        except Exception:
            pass


async def mines_pick(interaction: discord.Interaction, index: int):
    member = interaction.user
    game = get_active_game(member.id, "mines")
    if not game:
        await safe_interaction_reply(interaction, "This game is already finished. Start a new one.")
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
                    f"{premium_divider()}"
                ),
                color=COLOR_DENY
            ),
            view=view
        )
        register_loss(member.id)
        active_games.pop(member.id, None)
        return

    game["safe_hits"] += 1
    set_best_stat(member.id, "best_mines_safe_hits", game["safe_hits"])
    payout = mines_cashout_value(game["bet"], game["safe_hits"], game["mine_count"])

    view = MinesGameView(member.id)
    for child in view.children:
        if isinstance(child, MinesTileButton) and child.index in game["opened"]:
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
        await safe_interaction_reply(interaction, "This game is already finished. Start a new one.")
        return
    if game["safe_hits"] == 0:
        await safe_interaction_reply(interaction, "Open at least one safe tile first.")
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
                f"**Won:** {fmt_coins(payout)}\n"
                f"{premium_divider()}"
            ),
            color=COLOR_SUCCESS
        ),
        view=view
    )
    active_games.pop(member.id, None)


async def play_coinflip(interaction: discord.Interaction, bet: int, choice: str):
    member = interaction.user
    if member.id in active_games:
        await safe_interaction_reply(interaction, "Finish your current game first.")
        return
    if get_user_coins(member.id) < bet:
        await safe_interaction_reply(interaction, "Not enough coins.")
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
    finally:
        active_games.pop(member.id, None)


async def start_hilo_game(interaction: discord.Interaction, bet: int):
    member = interaction.user
    if member.id in active_games:
        await safe_interaction_reply(interaction, "Finish your current game first.")
        return
    if get_user_coins(member.id) < bet:
        await safe_interaction_reply(interaction, "Not enough coins.")
        return

    remove_user_coins(member.id, bet)
    add_stat(member.id, "games_played", 1)
    add_stat(member.id, "hilo_runs", 1)
    add_stat(member.id, "coins_spent", bet)

    active_games[member.id] = {
        "type": "hilo",
        "bet": bet,
        "current": random.randint(2, 13),
        "streak": 0,
    }

    await safe_interaction_reply(interaction, embed=build_hilo_embed(member, active_games[member.id], "🔼 HIGHER / LOWER"))
    if interaction.response.is_done():
        try:
            msg = await interaction.original_response()
            await msg.edit(view=HiLoGameView(member.id))
        except Exception:
            pass


async def hilo_guess(interaction: discord.Interaction, guess: str):
    member = interaction.user
    game = get_active_game(member.id, "hilo")
    if not game:
        await safe_interaction_reply(interaction, "This game is already finished. Start a new one.")
        return

    current = game["current"]
    nxt = random.randint(1, 14)
    while nxt == current:
        nxt = random.randint(1, 14)

    win = (guess == "higher" and nxt > current) or (guess == "lower" and nxt < current)

    if not win:
        game["current"] = nxt
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="💀 WRONG GUESS",
                description=(
                    f"{premium_divider()}\n"
                    f"**Old Number:** `{current}`\n"
                    f"**New Number:** `{nxt}`\n"
                    f"{premium_divider()}"
                ),
                color=COLOR_DENY
            ),
            view=None
        )
        register_loss(member.id)
        active_games.pop(member.id, None)
        return

    game["streak"] += 1
    game["current"] = nxt
    set_best_stat(member.id, "best_hilo_streak", game["streak"])

    if game["streak"] >= 5:
        payout = hilo_payout(game["bet"], game["streak"])
        add_user_coins(member.id, payout)
        register_win(member.id, payout)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🏆 MAX STREAK REACHED",
                description=(
                    f"{premium_divider()}\n"
                    f"**Streak:** {fmt_coins(game['streak'])}\n"
                    f"**Won:** {fmt_coins(payout)}\n"
                    f"{premium_divider()}"
                ),
                color=COLOR_SUCCESS
            ),
            view=None
        )
        active_games.pop(member.id, None)
        return

    await interaction.response.edit_message(
        embed=build_hilo_embed(member, game, "✅ CORRECT GUESS"),
        view=HiLoGameView(member.id)
    )


async def hilo_cashout(interaction: discord.Interaction):
    member = interaction.user
    game = get_active_game(member.id, "hilo")
    if not game:
        await safe_interaction_reply(interaction, "This game is already finished. Start a new one.")
        return
    if game["streak"] == 0:
        await safe_interaction_reply(interaction, "Get at least one correct guess first.")
        return

    payout = hilo_payout(game["bet"], game["streak"])
    add_user_coins(member.id, payout)
    register_win(member.id, payout)

    await interaction.response.edit_message(
        embed=discord.Embed(
            title="💰 HILO CASHOUT",
            description=(
                f"{premium_divider()}\n"
                f"**Won:** {fmt_coins(payout)}\n"
                f"{premium_divider()}"
            ),
            color=COLOR_SUCCESS
        ),
        view=None
    )
    active_games.pop(member.id, None)


# =========================================================
# COMMANDS
# =========================================================
@bot.tree.command(name="deploy_arcade_panels", description="Deploy public arcade panel and staff panel")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def deploy_arcade_panels(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)

        if not is_staff(interaction.user):
            await interaction.followup.send("Staff only.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Guild not found.", ephemeral=True)
            return

        public_channel = guild.get_channel(GAME_PANEL_CHANNEL_ID)
        staff_channel = guild.get_channel(STAFF_PANEL_CHANNEL_ID)

        if not isinstance(public_channel, discord.TextChannel):
            await interaction.followup.send("Public game channel not found.", ephemeral=True)
            return

        if not isinstance(staff_channel, discord.TextChannel):
            await interaction.followup.send("Staff panel channel not found.", ephemeral=True)
            return

        await public_channel.send(embed=build_arcade_hub_embed(), view=ArcadeHubView())
        await staff_channel.send(embed=build_staff_panel_embed(), view=StaffPanelView())

        await interaction.followup.send("Arcade panels deployed.", ephemeral=True)

    except Exception as e:
        print("deploy_arcade_panels error:", e)
        traceback.print_exc()
        try:
            await interaction.followup.send(f"Deploy error: `{e}`", ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="balance", description="Check your balance")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def balance(interaction: discord.Interaction):
    try:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_balance_embed(interaction.user), ephemeral=True)
    except Exception as e:
        print("balance error:", e)
        traceback.print_exc()
        await safe_interaction_reply(interaction, f"Error: `{e}`")


@bot.tree.command(name="arcade_stats", description="Check your arcade stats")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def arcade_stats(interaction: discord.Interaction):
    try:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_profile_embed(interaction.user), ephemeral=True)
    except Exception as e:
        print("arcade_stats error:", e)
        traceback.print_exc()
        await safe_interaction_reply(interaction, f"Error: `{e}`")


# =========================================================
# GLOBAL APP COMMAND ERROR HANDLER
# =========================================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print("APP COMMAND ERROR:", repr(error))
    traceback.print_exc()

    message = f"Command error: `{error}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        pass


# =========================================================
# READY
# =========================================================
@bot.event
async def on_ready():
    global coins_db, stats_db, daily_db

    coins_db = load_json(COINS_FILE, {})
    stats_db = load_json(STATS_FILE, {})
    daily_db = load_json(DAILY_FILE, {})

    print("Arcade bot is starting...")
    print(f"Logged in as: {bot.user} ({bot.user.id})")
    print(f"Guild ID loaded: {GUILD_ID}")

    bot.add_view(ArcadeHubView())
    bot.add_view(StaffPanelView())

    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}.")
    except Exception as e:
        print(f"Slash command sync error: {e}")
        traceback.print_exc()

    print("Arcade bot is ready.")


bot.run(TOKEN)
