# ==============================
# GEN ARCADE BOT (FINAL FIXED)
# ==============================

import os
import json
import random
import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

# ==============================
# ENV
# ==============================
TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

# ==============================
# FILES
# ==============================
COINS_FILE = "coins.json"
DAILY_FILE = "daily.json"

# ==============================
# LOAD / SAVE
# ==============================
def load(file, default):
    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump(default, f)
    with open(file) as f:
        return json.load(f)

def save(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

coins = load(COINS_FILE, {})
daily = load(DAILY_FILE, {})

# ==============================
# BOT
# ==============================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

active_games = {}

# ==============================
# HELPERS
# ==============================
def get_coins(uid):
    return coins.get(str(uid), 0)

def add_coins(uid, amount):
    coins[str(uid)] = get_coins(uid) + amount
    save(COINS_FILE, coins)

def remove_coins(uid, amount):
    coins[str(uid)] = max(0, get_coins(uid) - amount)
    save(COINS_FILE, coins)

# ==============================
# EMBEDS
# ==============================
def ui(title, desc, color=0x8E44AD):
    return discord.Embed(title=title, description=desc, color=color)

# ==============================
# MAIN PANEL
# ==============================
class MainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎡 Wheel", style=discord.ButtonStyle.primary)
    async def wheel(self, i: discord.Interaction, b):
        await i.response.send_message("Choose bet:", view=WheelView(), ephemeral=True)

    @discord.ui.button(label="💣 Mines", style=discord.ButtonStyle.danger)
    async def mines(self, i: discord.Interaction, b):
        await i.response.send_message("Choose bet:", view=MinesBetView(), ephemeral=True)

    @discord.ui.button(label="🎁 Daily", style=discord.ButtonStyle.success)
    async def daily_btn(self, i: discord.Interaction, b):
        uid = str(i.user.id)

        now = datetime.now(timezone.utc)
        if uid in daily:
            last = datetime.fromisoformat(daily[uid])
            if now - last < timedelta(hours=24):
                await i.response.send_message("⏳ Already claimed", ephemeral=True)
                return

        reward = random.randint(1, 100)
        add_coins(i.user.id, reward)

        daily[uid] = now.isoformat()
        save(DAILY_FILE, daily)

        await i.response.send_message(f"🎁 You got **{reward} coins**", ephemeral=True)

    @discord.ui.button(label="💰 Balance", style=discord.ButtonStyle.secondary)
    async def bal(self, i: discord.Interaction, b):
        await i.response.send_message(f"💰 {get_coins(i.user.id)} coins", ephemeral=True)

# ==============================
# WHEEL
# ==============================
class WheelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    async def spin(self, i, bet):
        if get_coins(i.user.id) < bet:
            await i.response.send_message("Not enough coins", ephemeral=True)
            return

        remove_coins(i.user.id, bet)

        await i.response.send_message("🎡 Spinning...", ephemeral=True)

        await asyncio.sleep(1)

        chance = random.random()

        if bet == 5:
            win = chance > 0.55
        elif bet == 10:
            win = chance > 0.6
        else:
            win = chance > 0.7

        if win:
            reward = int(bet * random.uniform(1.5, 2.5))
            add_coins(i.user.id, reward)
            await i.followup.send(f"🎉 WIN {reward} coins", ephemeral=True)
        else:
            await i.followup.send("💀 LOST", ephemeral=True)

    @discord.ui.button(label="5", style=discord.ButtonStyle.primary)
    async def b5(self, i, b):
        await self.spin(i, 5)

    @discord.ui.button(label="10", style=discord.ButtonStyle.success)
    async def b10(self, i, b):
        await self.spin(i, 10)

    @discord.ui.button(label="25", style=discord.ButtonStyle.danger)
    async def b25(self, i, b):
        await self.spin(i, 25)

# ==============================
# MINES
# ==============================
class MinesBetView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="5", style=discord.ButtonStyle.primary)
    async def b5(self, i, b):
        await start_mines(i, 5)

    @discord.ui.button(label="10", style=discord.ButtonStyle.success)
    async def b10(self, i, b):
        await start_mines(i, 10)

    @discord.ui.button(label="25", style=discord.ButtonStyle.danger)
    async def b25(self, i, b):
        await start_mines(i, 25)

async def start_mines(i, bet):
    if get_coins(i.user.id) < bet:
        await i.response.send_message("Not enough coins", ephemeral=True)
        return

    remove_coins(i.user.id, bet)

    mines = random.sample(range(9), 2)

    active_games[i.user.id] = {
        "type": "mines",
        "bet": bet,
        "mines": mines,
        "opened": []
    }

    await i.response.send_message("💣 Pick tiles", view=MinesView(i.user.id), ephemeral=True)

class MinesView(discord.ui.View):
    def __init__(self, uid):
        super().__init__(timeout=120)
        self.uid = uid

        for x in range(9):
            self.add_item(MineBtn(x))

class MineBtn(discord.ui.Button):
    def __init__(self, idx):
        super().__init__(label="?", row=idx//3)
        self.idx = idx

    async def callback(self, i):
        game = active_games.get(i.user.id)

        if not game:
            await i.response.send_message("Game ended", ephemeral=True)
            return

        if self.idx in game["mines"]:
            await i.response.edit_message(content="💀 BOOM", view=None)
            active_games.pop(i.user.id, None)
            return

        self.label = "✅"
        self.style = discord.ButtonStyle.success
        self.disabled = True

        game["opened"].append(self.idx)

        if len(game["opened"]) >= 3:
            reward = game["bet"] * 2
            add_coins(i.user.id, reward)
            await i.response.edit_message(content=f"🎉 WIN {reward}", view=None)
            active_games.pop(i.user.id, None)
            return

        await i.response.edit_message(view=self.view)

# ==============================
# COMMAND
# ==============================
@bot.tree.command(name="panel")
async def panel(i: discord.Interaction):
    await i.response.send_message(
        embed=ui("🎮 GEN ARCADE", "Play games & earn coins"),
        view=MainView()
    )

# ==============================
# READY
# ==============================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.add_view(MainView())
    bot.add_view(WheelView())
    bot.add_view(MinesBetView())
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))

# ==============================
bot.run(TOKEN)
