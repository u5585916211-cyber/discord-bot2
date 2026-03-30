import os
import json
import random
import asyncio
from datetime import datetime, timezone

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
WHEEL_PANEL_CHANNEL_ID = 1488310736269873325
WHEEL_LOG_CHANNEL_ID = 1488310901537771670
WHEEL_STAFF_PANEL_CHANNEL_ID = 1488311013098000414

STAFF_ROLE_ID = 1487568447926829126

# =========================================================
# FILES
# =========================================================
COINS_FILE = "wheel_coins.json"
STATS_FILE = "wheel_stats.json"
PENDING_KEYS_FILE = "wheel_pending_keys.json"

# =========================================================
# COLORS
# =========================================================
COLOR_MAIN = 0x8E44AD
COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_DENY = 0xED4245
COLOR_INFO = 0x5865F2
COLOR_LOG = 0x2B2D31

# =========================================================
# BOT
# =========================================================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

coins_db = {}
stats_db = {}
pending_keys_db = {}

user_spin_locks = set()

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
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def premium_divider() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━━━"


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


def ensure_stats_user(user_id: int):
    uid = str(user_id)
    if uid not in stats_db:
        stats_db[uid] = {
            "total_spins": 0,
            "total_coins_spent": 0,
            "total_coins_won": 0,
            "wins": 0,
            "losses": 0,
            "pending_key_wins": 0,
            "last_spin_at": None,
        }


def add_pending_key(user_id: int, product_type: str):
    uid = str(user_id)
    if uid not in pending_keys_db:
        pending_keys_db[uid] = []

    pending_keys_db[uid].append({
        "product_type": product_type,
        "created_at": now_iso(),
        "fulfilled": False,
        "fulfilled_at": None,
        "fulfilled_by": None,
    })
    save_json(PENDING_KEYS_FILE, pending_keys_db)


def get_unfulfilled_key_rewards(user_id: int):
    uid = str(user_id)
    entries = pending_keys_db.get(uid, [])
    return [x for x in entries if not x.get("fulfilled", False)]


def fulfill_next_pending_key(user_id: int, staff_id: int):
    uid = str(user_id)
    entries = pending_keys_db.get(uid, [])
    for entry in entries:
        if not entry.get("fulfilled", False):
            entry["fulfilled"] = True
            entry["fulfilled_at"] = now_iso()
            entry["fulfilled_by"] = str(staff_id)
            save_json(PENDING_KEYS_FILE, pending_keys_db)
            return entry
    return None


def reward_label(reward: dict) -> str:
    if reward["type"] == "lose":
        return "Lose"
    if reward["type"] == "coins":
        return f"+{reward['amount']} Coins"
    if reward["type"] == "key":
        return f"{reward['product']} Key"
    if reward["type"] == "bonus":
        return f"+{reward['amount']} Bonus Coins"
    return "Unknown"


def reward_display(reward: dict) -> str:
    if reward["type"] == "lose":
        return "💀 LOSE"
    if reward["type"] == "coins":
        return f"💰 +{reward['amount']} COINS"
    if reward["type"] == "key":
        if reward["product"] == "1 Day":
            return "🔑 1 DAY KEY"
        if reward["product"] == "1 Week":
            return "🔑 1 WEEK KEY"
        return "💎 LIFETIME KEY"
    if reward["type"] == "bonus":
        return f"🎁 +{reward['amount']} BONUS"
    return "❔ UNKNOWN"


async def send_log(guild: discord.Guild, title: str, description: str, color: int = COLOR_LOG):
    ch = guild.get_channel(WHEEL_LOG_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        embed = discord.Embed(title=title, description=description, color=color)
        await ch.send(embed=embed)


# =========================================================
# REWARD CONFIG
# =========================================================
WHEEL_CONFIG = {
    5: [
        {"type": "lose", "weight": 40},
        {"type": "coins", "amount": 4, "weight": 24},
        {"type": "coins", "amount": 6, "weight": 18},
        {"type": "coins", "amount": 9, "weight": 9},
        {"type": "key", "product": "1 Day", "weight": 6},
        {"type": "bonus", "amount": 5, "weight": 3},
    ],
    10: [
        {"type": "lose", "weight": 28},
        {"type": "coins", "amount": 8, "weight": 22},
        {"type": "coins", "amount": 12, "weight": 18},
        {"type": "coins", "amount": 18, "weight": 10},
        {"type": "key", "product": "1 Day", "weight": 10},
        {"type": "key", "product": "1 Week", "weight": 8},
        {"type": "bonus", "amount": 10, "weight": 4},
    ],
    25: [
        {"type": "lose", "weight": 18},
        {"type": "coins", "amount": 18, "weight": 20},
        {"type": "coins", "amount": 28, "weight": 18},
        {"type": "coins", "amount": 45, "weight": 12},
        {"type": "key", "product": "1 Day", "weight": 8},
        {"type": "key", "product": "1 Week", "weight": 10},
        {"type": "key", "product": "Lifetime", "weight": 9},
        {"type": "bonus", "amount": 25, "weight": 5},
    ],
}


def roll_reward(spin_cost: int) -> dict:
    pool = WHEEL_CONFIG[spin_cost]
    weights = [r["weight"] for r in pool]
    return random.choices(pool, weights=weights, k=1)[0].copy()


# =========================================================
# UI BUILDERS
# =========================================================
def build_main_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎡 GEN LUCK WHEEL",
        description=(
            f"{premium_divider()}\n"
            f"Use your **server coins** and spin the big wheel.\n\n"
            f"**Spins**\n"
            f"• 5 Coins\n"
            f"• 10 Coins\n"
            f"• 25 Coins\n\n"
            f"**Possible Rewards**\n"
            f"• Coin wins\n"
            f"• Bonus coins\n"
            f"• 1 Day key rewards\n"
            f"• 1 Week key rewards\n"
            f"• Lifetime key rewards\n\n"
            f"Press a button below to spin.\n"
            f"{premium_divider()}"
        ),
        color=COLOR_MAIN
    )
    embed.set_footer(text="Internal server coins only • No real money")
    return embed


def build_rewards_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎁 REWARD OVERVIEW",
        description=(
            f"{premium_divider()}\n"
            f"**5 Coins**\n"
            f"• safer beginner spin\n"
            f"• smaller coin wins\n"
            f"• small 1 Day key chance\n\n"
            f"**10 Coins**\n"
            f"• balanced rewards\n"
            f"• better 1 Day / 1 Week chances\n\n"
            f"**25 Coins**\n"
            f"• highest reward tier\n"
            f"• best chance for big coin wins\n"
            f"• possible Lifetime reward\n"
            f"{premium_divider()}"
        ),
        color=COLOR_INFO
    )
    return embed


def build_balance_embed(member: discord.Member) -> discord.Embed:
    balance = get_user_coins(member.id)
    pending = len(get_unfulfilled_key_rewards(member.id))

    embed = discord.Embed(
        title="💰 YOUR BALANCE",
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Coins:** `{balance}`\n"
            f"**Pending Key Rewards:** `{pending}`\n"
            f"{premium_divider()}"
        ),
        color=COLOR_SUCCESS
    )
    return embed


def build_staff_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛠️ WHEEL STAFF PANEL",
        description=(
            f"{premium_divider()}\n"
            f"Manage balances and pending key rewards.\n\n"
            f"**Actions**\n"
            f"• Add Coins\n"
            f"• Remove Coins\n"
            f"• Set Coins\n"
            f"• Check User\n"
            f"• Fulfill Key Reward\n"
            f"{premium_divider()}"
        ),
        color=COLOR_INFO
    )
    return embed


def build_big_spin_embed(member: discord.Member, cost: int, visible_items: list[str], title: str) -> discord.Embed:
    lines = []
    top_border = "╔" + "═" * 31 + "╗"
    mid_border = "╠" + "═" * 31 + "╣"
    bottom_border = "╚" + "═" * 31 + "╝"

    lines.append(top_border)
    for i, item in enumerate(visible_items):
        if i == 3:
            text = f"▶ {item}".ljust(29)
            lines.append(f"║{text}║")
        else:
            text = f"  {item}".ljust(29)
            lines.append(f"║{text}║")
        if i == 2:
            lines.append(mid_border)
    lines.append(bottom_border)

    embed = discord.Embed(
        title=title,
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Spin Cost:** `{cost}` coins\n\n"
            f"```fix\n" + "\n".join(lines) + "\n```\n"
            f"{premium_divider()}"
        ),
        color=COLOR_WARN
    )
    embed.set_footer(text="The pointer stops on the center reward.")
    return embed


def build_result_embed(member: discord.Member, cost: int, reward: dict, balance_after: int) -> discord.Embed:
    if reward["type"] == "lose":
        title = "💀 BAD LUCK"
        color = COLOR_DENY
        desc = "You lost this spin."
    elif reward["type"] == "coins":
        title = "🎉 COIN WIN"
        color = COLOR_SUCCESS
        desc = f"You won **{reward['amount']} coins**."
    elif reward["type"] == "key":
        title = "🔑 KEY REWARD WON"
        color = COLOR_SUCCESS
        desc = (
            f"You won a **{reward['product']} key reward**.\n"
            f"Staff can fulfill it from the staff panel."
        )
    else:
        title = "🎁 BONUS WIN"
        color = COLOR_SUCCESS
        desc = f"You received **{reward['amount']} bonus coins**."

    embed = discord.Embed(
        title=title,
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Spin Cost:** `{cost}` coins\n"
            f"**Reward:** {reward_display(reward)}\n"
            f"**Result:** {desc}\n"
            f"**New Balance:** `{balance_after}` coins\n"
            f"{premium_divider()}"
        ),
        color=color
    )
    return embed


# =========================================================
# SPIN ANIMATION
# =========================================================
DISPLAY_POOL = [
    "💀 LOSE",
    "💰 +4 COINS",
    "💰 +6 COINS",
    "💰 +8 COINS",
    "💰 +12 COINS",
    "💰 +18 COINS",
    "🎁 BONUS +5",
    "🎁 BONUS +10",
    "🔑 1 DAY KEY",
    "🔑 1 WEEK KEY",
    "💎 LIFETIME KEY",
    "💥 JACKPOT",
]


def random_window(size: int = 7) -> list[str]:
    return [random.choice(DISPLAY_POOL) for _ in range(size)]


async def perform_spin(interaction: discord.Interaction, cost: int):
    member = interaction.user
    guild = interaction.guild

    if not guild or not isinstance(member, discord.Member):
        await interaction.response.send_message("This only works in a server.", ephemeral=True)
        return

    if member.id in user_spin_locks:
        await interaction.response.send_message("You already have a spin running.", ephemeral=True)
        return

    balance = get_user_coins(member.id)
    if balance < cost:
        await interaction.response.send_message(
            f"You need `{cost}` coins for this spin.\nYour balance: `{balance}`",
            ephemeral=True
        )
        return

    user_spin_locks.add(member.id)
    try:
        remove_user_coins(member.id, cost)

        ensure_stats_user(member.id)
        stats_db[str(member.id)]["total_spins"] += 1
        stats_db[str(member.id)]["total_coins_spent"] += cost
        stats_db[str(member.id)]["last_spin_at"] = now_iso()
        save_json(STATS_FILE, stats_db)

        await interaction.response.send_message(
            embed=build_big_spin_embed(member, cost, random_window(), "🎡 BIG WHEEL STARTING..."),
            ephemeral=True
        )

        msg = await interaction.original_response()
        final_reward = roll_reward(cost)
        final_text = reward_display(final_reward)

        delays = [0.15, 0.18, 0.21, 0.25, 0.29, 0.34, 0.40, 0.48, 0.58, 0.70, 0.85]

        for index, delay in enumerate(delays):
            window = random_window()
            if index >= len(delays) - 3:
                # near end, show the real reward in the center area more often
                window[3] = random.choice(DISPLAY_POOL)
                if index == len(delays) - 2:
                    window[4] = final_text
                if index == len(delays) - 1:
                    window[3] = final_text

            title = "🎡 WHEEL SPINNING..." if index < len(delays) - 1 else "🎯 WHEEL STOPPING..."
            await msg.edit(embed=build_big_spin_embed(member, cost, window, title), view=None)
            await asyncio.sleep(delay)

        if final_reward["type"] == "lose":
            stats_db[str(member.id)]["losses"] += 1

        elif final_reward["type"] == "coins":
            add_user_coins(member.id, final_reward["amount"])
            stats_db[str(member.id)]["wins"] += 1
            stats_db[str(member.id)]["total_coins_won"] += final_reward["amount"]

        elif final_reward["type"] == "bonus":
            add_user_coins(member.id, final_reward["amount"])
            stats_db[str(member.id)]["wins"] += 1
            stats_db[str(member.id)]["total_coins_won"] += final_reward["amount"]

        elif final_reward["type"] == "key":
            add_pending_key(member.id, final_reward["product"])
            stats_db[str(member.id)]["wins"] += 1
            stats_db[str(member.id)]["pending_key_wins"] += 1

        save_json(STATS_FILE, stats_db)

        balance_after = get_user_coins(member.id)
        await msg.edit(embed=build_result_embed(member, cost, final_reward, balance_after), view=None)

        await send_log(
            guild,
            "🎡 Wheel Spin",
            (
                f"**User:** {member.mention}\n"
                f"**Spin Cost:** `{cost}`\n"
                f"**Reward:** `{reward_label(final_reward)}`\n"
                f"**Balance After:** `{balance_after}`"
            ),
            color=COLOR_DENY if final_reward["type"] == "lose" else COLOR_SUCCESS
        )

    finally:
        user_spin_locks.discard(member.id)


# =========================================================
# MODALS
# =========================================================
class AddCoinsModal(discord.ui.Modal, title="Add Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", placeholder="Enter user ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="Amount", placeholder="Enter amount to add", required=True, max_length=10)

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
        new_balance = get_user_coins(user_id)

        await send_log(
            interaction.guild,
            "➕ Coins Added",
            f"**Staff:** {interaction.user.mention}\n**User:** {user_text}\n**Added:** `{amount}`\n**New Balance:** `{new_balance}`",
            color=COLOR_SUCCESS
        )

        await interaction.response.send_message(
            f"Added `{amount}` coins to {user_text}.\nNew balance: `{new_balance}`",
            ephemeral=True
        )


class RemoveCoinsModal(discord.ui.Modal, title="Remove Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", placeholder="Enter user ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="Amount", placeholder="Enter amount to remove", required=True, max_length=10)

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
        new_balance = get_user_coins(user_id)

        await send_log(
            interaction.guild,
            "➖ Coins Removed",
            f"**Staff:** {interaction.user.mention}\n**User:** {user_text}\n**Removed:** `{amount}`\n**New Balance:** `{new_balance}`",
            color=COLOR_WARN
        )

        await interaction.response.send_message(
            f"Removed `{amount}` coins from {user_text}.\nNew balance: `{new_balance}`",
            ephemeral=True
        )


class SetCoinsModal(discord.ui.Modal, title="Set Coins"):
    user_id_input = discord.ui.TextInput(label="User ID", placeholder="Enter user ID", required=True, max_length=30)
    amount_input = discord.ui.TextInput(label="New Balance", placeholder="Enter exact balance", required=True, max_length=10)

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
            f"**Staff:** {interaction.user.mention}\n**User:** {user_text}\n**New Balance:** `{amount}`",
            color=COLOR_INFO
        )

        await interaction.response.send_message(
            f"Set balance of {user_text} to `{amount}` coins.",
            ephemeral=True
        )


class CheckUserModal(discord.ui.Modal, title="Check User"):
    user_id_input = discord.ui.TextInput(label="User ID", placeholder="Enter user ID", required=True, max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return

        try:
            user_id = int(str(self.user_id_input).strip())
        except ValueError:
            await interaction.response.send_message("Invalid user ID.", ephemeral=True)
            return

        member = interaction.guild.get_member(user_id)
        balance = get_user_coins(user_id)
        pending = get_unfulfilled_key_rewards(user_id)

        ensure_stats_user(user_id)
        user_stats = stats_db[str(user_id)]

        embed = discord.Embed(
            title="👤 USER CHECK",
            description=f"{premium_divider()}",
            color=COLOR_INFO
        )
        embed.add_field(name="User", value=member.mention if member else f"`{user_id}`", inline=False)
        embed.add_field(name="Coins", value=f"`{balance}`", inline=True)
        embed.add_field(name="Pending Key Rewards", value=f"`{len(pending)}`", inline=True)
        embed.add_field(name="Total Spins", value=f"`{user_stats['total_spins']}`", inline=True)
        embed.add_field(name="Coins Spent", value=f"`{user_stats['total_coins_spent']}`", inline=True)
        embed.add_field(name="Coins Won", value=f"`{user_stats['total_coins_won']}`", inline=True)
        embed.add_field(name="Wins", value=f"`{user_stats['wins']}`", inline=True)
        embed.add_field(name="Losses", value=f"`{user_stats['losses']}`", inline=True)
        embed.add_field(name="Key Wins", value=f"`{user_stats['pending_key_wins']}`", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


class FulfillKeyModal(discord.ui.Modal, title="Fulfill Pending Key Reward"):
    user_id_input = discord.ui.TextInput(label="User ID", placeholder="Enter user ID", required=True, max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return

        try:
            user_id = int(str(self.user_id_input).strip())
        except ValueError:
            await interaction.response.send_message("Invalid user ID.", ephemeral=True)
            return

        fulfilled = fulfill_next_pending_key(user_id, interaction.user.id)
        if not fulfilled:
            await interaction.response.send_message("No pending key reward found for this user.", ephemeral=True)
            return

        member = interaction.guild.get_member(user_id)
        user_text = member.mention if member else f"`{user_id}`"

        await send_log(
            interaction.guild,
            "🔑 Key Reward Fulfilled",
            f"**Staff:** {interaction.user.mention}\n**User:** {user_text}\n**Reward:** `{fulfilled['product_type']}`",
            color=COLOR_SUCCESS
        )

        try:
            if member:
                await member.send(
                    embed=discord.Embed(
                        title="🔑 Your Key Reward Was Fulfilled",
                        description=(
                            f"{premium_divider()}\n"
                            f"Staff marked your **{fulfilled['product_type']}** reward as fulfilled.\n"
                            f"{premium_divider()}"
                        ),
                        color=COLOR_SUCCESS
                    )
                )
        except Exception:
            pass

        await interaction.response.send_message(
            f"Fulfilled next pending key reward for {user_text}.",
            ephemeral=True
        )


# =========================================================
# VIEWS
# =========================================================
class WheelMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Spin 5", style=discord.ButtonStyle.primary, emoji="🎡", custom_id="wheel_spin_5")
    async def spin_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await perform_spin(interaction, 5)

    @discord.ui.button(label="Spin 10", style=discord.ButtonStyle.success, emoji="✨", custom_id="wheel_spin_10")
    async def spin_10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await perform_spin(interaction, 10)

    @discord.ui.button(label="Spin 25", style=discord.ButtonStyle.danger, emoji="💎", custom_id="wheel_spin_25")
    async def spin_25(self, interaction: discord.Interaction, button: discord.ui.Button):
        await perform_spin(interaction, 25)

    @discord.ui.button(label="Balance", style=discord.ButtonStyle.secondary, emoji="💰", custom_id="wheel_balance")
    async def balance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_balance_embed(interaction.user), ephemeral=True)

    @discord.ui.button(label="Rewards", style=discord.ButtonStyle.secondary, emoji="🎁", custom_id="wheel_rewards")
    async def rewards_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_rewards_embed(), ephemeral=True)


class WheelStaffView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Add Coins", style=discord.ButtonStyle.success, emoji="➕", custom_id="staff_add_coins")
    async def add_coins_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(AddCoinsModal())

    @discord.ui.button(label="Remove Coins", style=discord.ButtonStyle.danger, emoji="➖", custom_id="staff_remove_coins")
    async def remove_coins_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(RemoveCoinsModal())

    @discord.ui.button(label="Set Coins", style=discord.ButtonStyle.primary, emoji="🧾", custom_id="staff_set_coins")
    async def set_coins_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(SetCoinsModal())

    @discord.ui.button(label="Check User", style=discord.ButtonStyle.secondary, emoji="👤", custom_id="staff_check_user")
    async def check_user_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(CheckUserModal())

    @discord.ui.button(label="Fulfill Key", style=discord.ButtonStyle.success, emoji="🔑", custom_id="staff_fulfill_key")
    async def fulfill_key_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(FulfillKeyModal())


# =========================================================
# COMMANDS
# =========================================================
@bot.tree.command(name="deploy_wheel_panels", description="Post the wheel panel and staff panel to the configured channels")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def deploy_wheel_panels(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return

    public_channel = guild.get_channel(WHEEL_PANEL_CHANNEL_ID)
    staff_channel = guild.get_channel(WHEEL_STAFF_PANEL_CHANNEL_ID)

    if not isinstance(public_channel, discord.TextChannel):
        await interaction.response.send_message("Public wheel channel not found.", ephemeral=True)
        return

    if not isinstance(staff_channel, discord.TextChannel):
        await interaction.response.send_message("Staff panel channel not found.", ephemeral=True)
        return

    await public_channel.send(embed=build_main_panel_embed(), view=WheelMainView())
    await staff_channel.send(embed=build_staff_panel_embed(), view=WheelStaffView())

    await interaction.response.send_message("Wheel panels deployed successfully.", ephemeral=True)


@bot.tree.command(name="send_wheel_panel", description="Send the public wheel panel here")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def send_wheel_panel(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    await interaction.response.send_message(embed=build_main_panel_embed(), view=WheelMainView())


@bot.tree.command(name="send_wheel_staff_panel", description="Send the staff wheel panel here")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def send_wheel_staff_panel(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    await interaction.response.send_message(embed=build_staff_panel_embed(), view=WheelStaffView())


@bot.tree.command(name="balance", description="Check your wheel balance")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def balance(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Server only.", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_balance_embed(interaction.user), ephemeral=True)


@bot.tree.command(name="wheel_stats", description="Check your wheel stats")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def wheel_stats(interaction: discord.Interaction):
    ensure_stats_user(interaction.user.id)
    data = stats_db[str(interaction.user.id)]

    embed = discord.Embed(
        title="📊 YOUR WHEEL STATS",
        description=f"{premium_divider()}",
        color=COLOR_INFO
    )
    embed.add_field(name="Total Spins", value=f"`{data['total_spins']}`", inline=True)
    embed.add_field(name="Coins Spent", value=f"`{data['total_coins_spent']}`", inline=True)
    embed.add_field(name="Coins Won", value=f"`{data['total_coins_won']}`", inline=True)
    embed.add_field(name="Wins", value=f"`{data['wins']}`", inline=True)
    embed.add_field(name="Losses", value=f"`{data['losses']}`", inline=True)
    embed.add_field(name="Pending Key Wins", value=f"`{data['pending_key_wins']}`", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================================================
# READY
# =========================================================
@bot.event
async def on_ready():
    global coins_db, stats_db, pending_keys_db

    coins_db = load_json(COINS_FILE, {})
    stats_db = load_json(STATS_FILE, {})
    pending_keys_db = load_json(PENDING_KEYS_FILE, {})

    print("Wheel bot is starting...")
    print(f"Logged in as: {bot.user} ({bot.user.id})")
    print(f"Guild ID loaded: {GUILD_ID}")

    bot.add_view(WheelMainView())
    bot.add_view(WheelStaffView())

    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}.")
    except Exception as e:
        print(f"Slash command sync error: {e}")

    print("Wheel bot is ready.")


bot.run(TOKEN)
