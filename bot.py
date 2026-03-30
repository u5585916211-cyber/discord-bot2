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
GUILD_ID_RAW = os.getenv("GUILD_ID")

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

# optional staff role:
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
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

coins_db = {}
stats_db = {}
pending_keys_db = {}

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
    current = get_user_coins(user_id)
    set_user_coins(user_id, current + int(amount))


def remove_user_coins(user_id: int, amount: int):
    current = get_user_coins(user_id)
    set_user_coins(user_id, max(0, current - int(amount)))


def ensure_stats_user(user_id: int):
    uid = str(user_id)
    if uid not in stats_db:
        stats_db[uid] = {
            "total_spins": 0,
            "total_coins_spent": 0,
            "total_coins_won": 0,
            "last_spin_at": None,
            "wins": 0,
            "losses": 0,
            "pending_key_wins": 0,
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
    if reward["type"] == "freespin":
        return f"{reward['amount']} Free Spin Credit"
    return "Unknown"


async def send_log(guild: discord.Guild, title: str, description: str, color: int = COLOR_LOG):
    ch = guild.get_channel(WHEEL_LOG_CHANNEL_ID)
    if isinstance(ch, discord.TextChannel):
        embed = discord.Embed(title=title, description=description, color=color)
        await ch.send(embed=embed)


# =========================================================
# WHEEL REWARDS
# =========================================================
WHEEL_CONFIG = {
    5: [
        {"type": "lose", "weight": 50},
        {"type": "coins", "amount": 3, "weight": 22},
        {"type": "coins", "amount": 5, "weight": 15},
        {"type": "coins", "amount": 8, "weight": 8},
        {"type": "key", "product": "1 Day", "weight": 4},
        {"type": "freespin", "amount": 5, "weight": 1},
    ],
    10: [
        {"type": "lose", "weight": 38},
        {"type": "coins", "amount": 6, "weight": 22},
        {"type": "coins", "amount": 10, "weight": 18},
        {"type": "coins", "amount": 15, "weight": 10},
        {"type": "key", "product": "1 Day", "weight": 8},
        {"type": "key", "product": "1 Week", "weight": 3},
        {"type": "freespin", "amount": 10, "weight": 1},
    ],
    25: [
        {"type": "lose", "weight": 28},
        {"type": "coins", "amount": 15, "weight": 20},
        {"type": "coins", "amount": 25, "weight": 20},
        {"type": "coins", "amount": 40, "weight": 12},
        {"type": "key", "product": "1 Day", "weight": 8},
        {"type": "key", "product": "1 Week", "weight": 7},
        {"type": "key", "product": "Lifetime", "weight": 3},
        {"type": "freespin", "amount": 25, "weight": 2},
    ],
}


def roll_reward(spin_cost: int) -> dict:
    pool = WHEEL_CONFIG[spin_cost]
    choices = [r for r in pool]
    weights = [r["weight"] for r in pool]
    return random.choices(choices, weights=weights, k=1)[0].copy()


# =========================================================
# EMBEDS
# =========================================================
def build_main_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎡 GEN COIN WHEEL",
        description=(
            f"{premium_divider()}\n"
            f"Spin the wheel with **server coins** and try your luck.\n\n"
            f"**Available Spins**\n"
            f"• 5 Coins\n"
            f"• 10 Coins\n"
            f"• 25 Coins\n\n"
            f"**Possible Rewards**\n"
            f"• More coins\n"
            f"• 1 Day key reward\n"
            f"• 1 Week key reward\n"
            f"• Lifetime key reward\n\n"
            f"Use the buttons below.\n"
            f"{premium_divider()}"
        ),
        color=COLOR_MAIN
    )
    embed.set_footer(text="Internal server coins only.")
    return embed


def build_rewards_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎁 WHEEL REWARDS",
        description=(
            f"{premium_divider()}\n"
            f"**5 Coin Spin**\n"
            f"• chance to lose\n"
            f"• small coin wins\n"
            f"• low 1 Day key chance\n\n"
            f"**10 Coin Spin**\n"
            f"• better coin rewards\n"
            f"• 1 Day / 1 Week key chance\n\n"
            f"**25 Coin Spin**\n"
            f"• best rewards\n"
            f"• highest key chance\n"
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
        title="🛠️ GEN WHEEL STAFF PANEL",
        description=(
            f"{premium_divider()}\n"
            f"Use the buttons below to manage user balances and key rewards.\n\n"
            f"**Available Actions**\n"
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


def build_spin_start_embed(member: discord.Member, cost: int) -> discord.Embed:
    embed = discord.Embed(
        title="🎡 WHEEL SPINNING...",
        description=(
            f"{premium_divider()}\n"
            f"{member.mention} is spinning the **{cost} coin wheel**...\n"
            f"{premium_divider()}"
        ),
        color=COLOR_WARN
    )
    return embed


def build_spin_result_embed(member: discord.Member, cost: int, reward: dict, balance_after: int) -> discord.Embed:
    if reward["type"] == "lose":
        title = "💀 YOU LOST"
        color = COLOR_DENY
        reward_text = "No reward this time."
    elif reward["type"] == "coins":
        title = "🎉 YOU WON COINS"
        color = COLOR_SUCCESS
        reward_text = f"You won **{reward['amount']} coins**."
    elif reward["type"] == "key":
        title = "🔑 YOU WON A KEY REWARD"
        color = COLOR_SUCCESS
        reward_text = f"You won a **{reward['product']}** key reward.\nStaff can fulfill it from the staff panel."
    else:
        title = "🎁 BONUS"
        color = COLOR_SUCCESS
        reward_text = f"You received **{reward['amount']} bonus coins**."

    embed = discord.Embed(
        title=title,
        description=(
            f"{premium_divider()}\n"
            f"**User:** {member.mention}\n"
            f"**Spin Cost:** `{cost}` coins\n"
            f"**Reward:** {reward_text}\n"
            f"**New Balance:** `{balance_after}` coins\n"
            f"{premium_divider()}"
        ),
        color=color
    )
    return embed


# =========================================================
# SPIN LOGIC
# =========================================================
user_spin_locks = set()

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

        await interaction.response.send_message(embed=build_spin_start_embed(member, cost), ephemeral=True)

        animation_steps = ["🎡", "🎯", "✨", "💎", "🎁", "🔑", "💥"]
        message = await interaction.original_response()

        for step in animation_steps:
            embed = discord.Embed(
                title=f"{step} SPINNING...",
                description=(
                    f"{premium_divider()}\n"
                    f"{member.mention} is spinning the **{cost} coin wheel**...\n"
                    f"{premium_divider()}"
                ),
                color=COLOR_WARN
            )
            await message.edit(embed=embed, view=None)
            await asyncio.sleep(0.55)

        reward = roll_reward(cost)

        if reward["type"] == "lose":
            stats_db[str(member.id)]["losses"] += 1

        elif reward["type"] == "coins":
            add_user_coins(member.id, reward["amount"])
            stats_db[str(member.id)]["wins"] += 1
            stats_db[str(member.id)]["total_coins_won"] += reward["amount"]

        elif reward["type"] == "freespin":
            add_user_coins(member.id, reward["amount"])
            stats_db[str(member.id)]["wins"] += 1
            stats_db[str(member.id)]["total_coins_won"] += reward["amount"]

        elif reward["type"] == "key":
            add_pending_key(member.id, reward["product"])
            stats_db[str(member.id)]["wins"] += 1
            stats_db[str(member.id)]["pending_key_wins"] += 1

        save_json(STATS_FILE, stats_db)

        balance_after = get_user_coins(member.id)
        result_embed = build_spin_result_embed(member, cost, reward, balance_after)
        await message.edit(embed=result_embed, view=None)

        await send_log(
            guild,
            "🎡 Wheel Spin",
            (
                f"**User:** {member.mention}\n"
                f"**Spin Cost:** `{cost}`\n"
                f"**Reward:** `{reward_label(reward)}`\n"
                f"**Balance After:** `{balance_after}`"
            ),
            color=COLOR_LOG if reward["type"] == "lose" else COLOR_SUCCESS
        )

    finally:
        user_spin_locks.discard(member.id)


# =========================================================
# MODALS
# =========================================================
class AddCoinsModal(discord.ui.Modal, title="Add Coins"):
    user_id_input = discord.ui.TextInput(
        label="User ID",
        placeholder="Enter user ID",
        required=True,
        max_length=30
    )

    amount_input = discord.ui.TextInput(
        label="Amount",
        placeholder="Enter amount to add",
        required=True,
        max_length=10
    )

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
            (
                f"**Staff:** {interaction.user.mention}\n"
                f"**User:** {user_text}\n"
                f"**Added:** `{amount}`\n"
                f"**New Balance:** `{new_balance}`"
            ),
            color=COLOR_SUCCESS
        )

        await interaction.response.send_message(
            f"Added `{amount}` coins to {user_text}.\nNew balance: `{new_balance}`",
            ephemeral=True
        )


class RemoveCoinsModal(discord.ui.Modal, title="Remove Coins"):
    user_id_input = discord.ui.TextInput(
        label="User ID",
        placeholder="Enter user ID",
        required=True,
        max_length=30
    )

    amount_input = discord.ui.TextInput(
        label="Amount",
        placeholder="Enter amount to remove",
        required=True,
        max_length=10
    )

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
            (
                f"**Staff:** {interaction.user.mention}\n"
                f"**User:** {user_text}\n"
                f"**Removed:** `{amount}`\n"
                f"**New Balance:** `{new_balance}`"
            ),
            color=COLOR_WARN
        )

        await interaction.response.send_message(
            f"Removed `{amount}` coins from {user_text}.\nNew balance: `{new_balance}`",
            ephemeral=True
        )


class SetCoinsModal(discord.ui.Modal, title="Set Coins"):
    user_id_input = discord.ui.TextInput(
        label="User ID",
        placeholder="Enter user ID",
        required=True,
        max_length=30
    )

    amount_input = discord.ui.TextInput(
        label="New Balance",
        placeholder="Enter exact balance",
        required=True,
        max_length=10
    )

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
            (
                f"**Staff:** {interaction.user.mention}\n"
                f"**User:** {user_text}\n"
                f"**New Balance:** `{amount}`"
            ),
            color=COLOR_INFO
        )

        await interaction.response.send_message(
            f"Set balance of {user_text} to `{amount}` coins.",
            ephemeral=True
        )


class CheckUserModal(discord.ui.Modal, title="Check User Balance"):
    user_id_input = discord.ui.TextInput(
        label="User ID",
        placeholder="Enter user ID",
        required=True,
        max_length=30
    )

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
    user_id_input = discord.ui.TextInput(
        label="User ID",
        placeholder="Enter user ID",
        required=True,
        max_length=30
    )

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
            (
                f"**Staff:** {interaction.user.mention}\n"
                f"**User:** {user_text}\n"
                f"**Reward:** `{fulfilled['product_type']}`"
            ),
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
                            f"Check with staff for the actual key delivery.\n"
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
@bot.tree.command(name="send_wheel_panel", description="Send the public wheel panel")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def send_wheel_panel(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    await interaction.response.send_message(
        embed=build_main_panel_embed(),
        view=WheelMainView()
    )


@bot.tree.command(name="send_wheel_staff_panel", description="Send the staff wheel panel")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def send_wheel_staff_panel(interaction: discord.Interaction):
    if not is_staff(interaction.user):
        await interaction.response.send_message("Staff only.", ephemeral=True)
        return

    await interaction.response.send_message(
        embed=build_staff_panel_embed(),
        view=WheelStaffView()
    )


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
