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
COLOR_WHEEL = 0x9B59B6
COLOR_ROAD = 0x2ECC71
COLOR_MINES = 0xE67E22
COLOR_FLIP = 0x3498DB
COLOR_HILO = 0xE91E63

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
# JSON
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


def fmt_coins(value: int) -> str:
    return f"`{value}`"


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
            "games_played": 0,
            "wins": 0,
            "losses": 0,
            "coins_won": 0,
            "coins_spent": 0,
            "biggest_win": 0,
            "wheel_spins": 0,
            "road_runs": 0,
            "mines_runs": 0,
            "coinflip_runs": 0,
            "hilo_runs": 0,
            "best_road_steps": 0,
            "best_mines_safe_hits": 0,
            "best_hilo_streak": 0,
        }


def add_stat(user_id: int, key: str, amount: int = 1):
    ensure_user_stats(user_id)
    stats_db[str(user_id)][key] += amount
    save_json(STATS_FILE, stats_db)


def set_best_stat(user_id: int, key: str, value: int):
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
    if game_type and game.get("type") != game_type:
        return None
    return game


async def safe_reply(
    interaction: discord.Interaction,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=ephemeral)
    except Exception:
        pass


async def send_log(guild: discord.Guild | None, title: str, description: str, color: int = COLOR_LOG):
    if guild is None:
        return
    ch = guild.get_channel(GAME_LOG_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        try:
            await ch.send(embed=discord.Embed(title=title, description=description, color=color))
        except Exception:
            pass

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
            if now_utc() - last <= timedelta(hours=48):
                streak = old_streak + 1
        except Exception:
            streak = 1

    reward = random.randint(1, 100)
    return reward, streak

# =========================================================
# WHEEL
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


def reward_display(reward: dict) -> str:
    if reward["type"] == "lose":
        return "💀 LOSE"
    if reward["type"] == "coins":
        return f"💰 +{reward['amount']} COINS"
    if reward["type"] == "bonus":
        return f"🎁 +{reward['amount']} BONUS"
    return "❔ UNKNOWN"


def roll_wheel_reward(cost: int) -> dict:
    pool = WHEEL_CONFIG[cost]
    weights = [x["weight"] for x in pool]
    return random.choices(pool, weights=weights, k=1)[0].copy()


def random_wheel_rows():
    return [random.choice(DISPLAY_POOL) for _ in range(5)]

# =========================================================
# GAME HELPERS
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
    multipliers = {
        0: 0,
        1: 1.35,
        2: 1.90,
        3: 2.80,
        4: 4.10,
        5: 6.00,
    }
    return int(round(bet * multipliers.get(streak, 6.00)))

# =========================================================
# EMBEDS
# =========================================================
def build_arcade_hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎮 GEN ARCADE HUB",
        description=(
            f"{premium_divider()}\n"
            f"Welcome to the coin arcade.\n\n"
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
    embed.add_field(
        name="🔥 Info",
        value="One active game at a time • old buttons are auto-blocked cleanly",
        inline=False
    )
    return embed


def build_staff_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛠️ ARCADE STAFF PANEL",
        description=(
            f"{premium_divider()}\n"
            f"Manage balances and check users.\n"
            f"{premium_divider()}"
        ),
        color=COLOR_INFO
    )
    return embed


def build_balance_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(title="💰 YOUR BALANCE", color=COLOR_SUCCESS)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Coins", value=fmt_coins(get_user_coins(member.id)), inline=True)
    return embed


def build_profile_embed(member: discord.Member) -> discord.Embed:
    ensure_user_stats(member.id)
    data = stats_db[str(member.id)]
    embed = discord.Embed(title="👤 ARCADE PROFILE", color=COLOR_INFO)
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


def build_wheel_info_embed() -> discord.Embed:
    return discord.Embed(
        title="🎡 WHEEL",
        description=f"{premium_divider()}\nChoose your spin tier.\n{premium_divider()}",
        color=COLOR_WHEEL
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
            f"**User:** {member.mention}\n"
            f"**Spin Cost:** {fmt_coins(cost)}\n\n"
            f"```fix\n" + "\n".join(rendered) + "\n```"
        ),
        color=COLOR_WHEEL
    )
    return embed


def build_wheel_result_embed(member: discord.Member, cost: int, reward: dict, balance_after: int) -> discord.Embed:
    title = "💀 YOU LOST" if reward["type"] == "lose" else "🎉 WHEEL RESULT"
    color = COLOR_DENY if reward["type"] == "lose" else COLOR_SUCCESS
    desc = "No reward this time." if reward["type"] == "lose" else reward_display(reward)

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Spin Cost", value=fmt_coins(cost), inline=True)
    embed.add_field(name="Reward", value=desc, inline=True)
    embed.add_field(name="Balance", value=fmt_coins(balance_after), inline=True)
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
    embed = discord.Embed(title=title, color=COLOR_ROAD)
    embed.description = (
        f"**User:** {member.mention}\n"
        f"**Bet:** {fmt_coins(state['bet'])}\n"
        f"**Steps:** {fmt_coins(state['steps'])}\n"
        f"**Cashout:** {fmt_coins(state['cashout'])}\n\n"
        f"```fix\n{road}\n```"
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
    embed = discord.Embed(title="💣 MINES", color=COLOR_MINES)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Bet", value=fmt_coins(bet), inline=True)
    embed.add_field(name="Mines", value=fmt_coins(mine_count), inline=True)
    embed.add_field(name="Safe Hits", value=fmt_coins(safe_hits), inline=True)
    embed.add_field(name="Cashout", value=fmt_coins(payout), inline=False)
    return embed


def build_coinflip_embed(member: discord.Member, bet: int, result_text: str, choice: str, win: bool, balance: int) -> discord.Embed:
    color = COLOR_SUCCESS if win else COLOR_DENY
    title = "🪙 COINFLIP WIN" if win else "🪙 COINFLIP LOSE"
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Bet", value=fmt_coins(bet), inline=True)
    embed.add_field(name="Your Pick", value=choice, inline=True)
    embed.add_field(name="Result", value=result_text, inline=True)
    embed.add_field(name="Balance", value=fmt_coins(balance), inline=False)
    return embed


def build_hilo_embed(member: discord.Member, game: dict, title: str) -> discord.Embed:
    current = game["current"]
    payout = hilo_payout(game["bet"], game["streak"])
    embed = discord.Embed(title=title, color=COLOR_HILO)
    embed.add_field(name="User", value=member.mention, inline=False)
    embed.add_field(name="Bet", value=fmt_coins(game["bet"]), inline=True)
    embed.add_field(name="Current Number", value=f"`{current}`", inline=True)
    embed.add_field(name="Streak", value=fmt_coins(game["streak"]), inline=True)
    embed.add_field(name="Cashout", value=fmt_coins(payout), inline=False)
    return embed

# =========================================================
# GAME VIEWS
# =========================================================
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
            await safe_reply(interaction, "This is not your game.")
            return
        await road_step(interaction)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.primary, emoji="💰")
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await safe_reply(interaction, "This is not your game.")
            return
        await road_cashout(interaction)


class MinesBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    async def _go(self, interaction: discord.Interaction, bet: int):
        await safe_reply(
            interaction,
            embed=discord.Embed(
                title="💣 CHOOSE MINES",
                description=f"**Bet:** {fmt_coins(bet)}\nChoose how many mines you want.",
                color=COLOR_MINES
            ),
            view=MinesCountView(bet)
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


class MinesTileButton(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(label="?", style=discord.ButtonStyle.secondary, row=index // 4)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        game = get_active_game(interaction.user.id, "mines")
        if not game:
            await safe_reply(interaction, "This game is already finished. Start a new one.")
            return
        if self.index in game["opened"]:
            await safe_reply(interaction, "This tile is already opened.")
            return
        await mines_pick(interaction, self.index)


class MinesGameView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

        for i in range(16):
            self.add_item(MinesTileButton(i))

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success, emoji="💰", row=4)
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await safe_reply(interaction, "This is not your game.")
            return
        await mines_cashout(interaction)


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


class CoinflipBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    async def _start(self, interaction: discord.Interaction, bet: int):
        await safe_reply(
            interaction,
            embed=discord.Embed(
                title="🪙 COINFLIP",
                description=f"**Bet:** {fmt_coins(bet)}\nChoose heads or tails.",
                color=COLOR_FLIP
            ),
            view=CoinflipChoiceView(bet)
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


class HiLoGameView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id

    @discord.ui.button(label="Higher", style=discord.ButtonStyle.success, emoji="🔼")
    async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await safe_reply(interaction, "This is not your game.")
            return
        await hilo_guess(interaction, "higher")

    @discord.ui.button(label="Lower", style=discord.ButtonStyle.danger, emoji="🔽")
    async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await safe_reply(interaction, "This is not your game.")
            return
        await hilo_guess(interaction, "lower")

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.primary, emoji="💰")
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await safe_reply(interaction, "This is not your game.")
            return
        await hilo_cashout(interaction)


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


class StaffPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Add Coins", style=discord.ButtonStyle.success, emoji="➕")
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await safe_reply(interaction, "Staff only.")
            return
        await interaction.response.send_modal(AddCoinsModal())

    @discord.ui.button(label="Remove Coins", style=discord.ButtonStyle.danger, emoji="➖")
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await safe_reply(interaction, "Staff only.")
            return
        await interaction.response.send_modal(RemoveCoinsModal())

    @discord.ui.button(label="Set Coins", style=discord.ButtonStyle.primary, emoji="🧾")
    async def set_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await safe_reply(interaction, "Staff only.")
            return
        await interaction.response.send_modal(SetCoinsModal())

    @discord.ui.button(label="Check User", style=discord.ButtonStyle.secondary, emoji="👤")
    async def check_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await safe_reply(interaction, "Staff only.")
            return
        await interaction.response.send_modal(CheckUserModal())


class ArcadeHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Wheel", style=discord.ButtonStyle.primary, emoji="🎡")
    async def wheel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_reply(interaction, embed=build_wheel_info_embed(), view=WheelSelectView())

    @discord.ui.button(label="Chicken Road", style=discord.ButtonStyle.success, emoji="🐔")
    async def road_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_reply(
            interaction,
            embed=discord.Embed(
                title="🐔 CHICKEN ROAD",
                description=f"{premium_divider()}\nChoose your bet.\n{premium_divider()}",
                color=COLOR_ROAD
            ),
            view=RoadBetView()
        )

    @discord.ui.button(label="Mines", style=discord.ButtonStyle.danger, emoji="💣")
    async def mines_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_reply(interaction, embed=build_mines_config_embed(), view=MinesBetView())

    @discord.ui.button(label="Coinflip", style=discord.ButtonStyle.secondary, emoji="🪙")
    async def flip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_reply(
            interaction,
            embed=discord.Embed(
                title="🪙 COINFLIP",
                description=f"{premium_divider()}\nChoose your bet.\n{premium_divider()}",
                color=COLOR_FLIP
            ),
            view=CoinflipBetView()
        )

    @discord.ui.button(label="Higher/Lower", style=discord.ButtonStyle.secondary, emoji="🔼")
    async def hilo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_reply(
            interaction,
            embed=discord.Embed(
                title="🔼 HIGHER / LOWER",
                description=f"{premium_divider()}\nChoose your bet.\n{premium_divider()}",
                color=COLOR_HILO
            ),
            view=HiLoBetView()
        )

    @discord.ui.button(label="Daily", style=discord.ButtonStyle.secondary, emoji="🎁")
    async def daily_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, next_claim = can_claim_daily(interaction.user.id)
        if not ok:
            await safe_reply(interaction, f"You already claimed daily.\nNext claim: <t:{int(next_claim.timestamp())}:R>")
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
        await safe_reply(interaction, embed=build_daily_embed(reward, streak, lucky))

    @discord.ui.button(label="Profile", style=discord.ButtonStyle.secondary, emoji="👤")
    async def profile_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await safe_reply(interaction, "Server only.")
            return
        await safe_reply(interaction, embed=build_profile_embed(interaction.user))

    @discord.ui.button(label="Balance", style=discord.ButtonStyle.secondary, emoji="💰")
    async def balance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await safe_reply(interaction, "Server only.")
            return
        await safe_reply(interaction, embed=build_balance_embed(interaction.user))

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.secondary, emoji="💸")
    async def transfer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TransferCoinsModal())

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, emoji="🏆")
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        sorted_users = sorted(coins_db.items(), key=lambda x: int(x[1]), reverse=True)[:10]
        if not sorted_users:
            await safe_reply(interaction, "No leaderboard data yet.")
            return

        lines = []
        for i, (uid, coins) in enumerate(sorted_users, start=1):
            member = interaction.guild.get_member(int(uid)) if interaction.guild else None
            name = member.mention if member else f"`{uid}`"
            lines.append(f"**{i}.** {name} — {fmt_coins(int(coins))}")

        embed = discord.Embed(
            title="🏆 COIN LEADERBOARD",
            description="\n".join(lines),
            color=COLOR_INFO
        )
        await safe_reply(interaction, embed=embed)

# =========================================================
# GAME LOGIC FUNCTIONS
# =========================================================
async def perform_wheel_spin(interaction: discord.Interaction, cost: int):
    member = interaction.user
    guild = interaction.guild

    if not isinstance(member, discord.Member):
        await safe_reply(interaction, "Server only.")
        return

    if member.id in active_games:
        await safe_reply(interaction, "Finish your current game first.")
        return

    if get_user_coins(member.id) < cost:
        await safe_reply(interaction, f"You need {fmt_coins(cost)}. Balance: {fmt_coins(get_user_coins(member.id))}")
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
        delays = [0.12, 0.14, 0.16, 0.20, 0.26, 0.34, 0.45, 0.60]

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
        else:
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
    except Exception as e:
        print("perform_wheel_spin error:", e)
        traceback.print_exc()
        await safe_reply(interaction, f"Wheel error: `{e}`")
    finally:
        active_games.pop(member.id, None)


async def start_road_game(interaction: discord.Interaction, bet: int):
    member = interaction.user
    if not isinstance(member, discord.Member):
        await safe_reply(interaction, "Server only.")
        return

    if member.id in active_games:
        await safe_reply(interaction, "Finish your current game first.")
        return

    if get_user_coins(member.id) < bet:
        await safe_reply(interaction, "Not enough coins.")
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

    await safe_reply(interaction, embed=build_road_embed(member, state, "🐔 CHICKEN ROAD"), view=RoadGameView(member.id))


async def road_step(interaction: discord.Interaction):
    member = interaction.user
    game = get_active_game(member.id, "road")
    if not game:
        await safe_reply(interaction, "This game is already finished. Start a new one.")
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
        await safe_reply(interaction, "This game is already finished. Start a new one.")
        return

    if game["steps"] == 0:
        await safe_reply(interaction, "Take at least one step first.")
        return

    payout = game["cashout"]
    add_user_coins(member.id, payout)
    register_win(member.id, payout)
    await interaction.response.edit_message(embed=build_road_embed(member, game, "💰 CASHED OUT"), view=None)
    active_games.pop(member.id, None)


async def start_mines_game(interaction: discord.Interaction, bet: int, mine_count: int):
    member = interaction.user
    if not isinstance(member, discord.Member):
        await safe_reply(interaction, "Server only.")
        return

    if member.id in active_games:
        await safe_reply(interaction, "Finish your current game first.")
        return

    if get_user_coins(member.id) < bet:
        await safe_reply(interaction, "Not enough coins.")
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

    await safe_reply(
        interaction,
        embed=build_mines_start_embed(member, bet, mine_count, 0, 0),
        view=MinesGameView(member.id)
    )


async def mines_pick(interaction: discord.Interaction, index: int):
    member = interaction.user
    game = get_active_game(member.id, "mines")
    if not game:
        await safe_reply(interaction, "This game is already finished. Start a new one.")
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
            else:
                child.disabled = True

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="💥 BOOM",
                description=(
                    f"{premium_divider()}\n"
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
        embed=build_mines_start_embed(member, game["bet"], game["mine_count"], game["safe
