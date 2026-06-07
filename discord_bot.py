"""
Odysseus Discord Bot
Commandes slash pour interagir avec Odysseus depuis Discord.
"""

import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
import requests

# ── Config ────────────────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.environ["DISCORD_TOKEN"]           # required — no default
ODYSSEUS_URL       = os.environ.get("ODYSSEUS_URL",       "http://127.0.0.1:7000")
ODYSSEUS_TOKEN     = os.environ.get("ODYSSEUS_API_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CHAT_MODEL         = os.environ.get("BULLE_MODEL",        "anthropic/claude-3.5-haiku")

# ── Chat via OpenRouter ───────────────────────────────────────────────────────
def _chat(message: str, username: str = "") -> str:
    if not OPENROUTER_API_KEY:
        return "Je ne peux pas repondre pour l'instant (cle API manquante)."
    system = (
        "Tu es Bulle, l'assistante IA du serveur Discord Pikipio Assistance. "
        "Tu reponds en francais, tu es sympathique et concis. "
        "Tu es connectee a Odysseus (gestionnaire de taches, emails, calendrier). "
        "Utilise /todos, /emails, /agenda, /note pour les donnees personnelles."
    )
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": CHAT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": f"{username}: {message}" if username else message},
                ],
                "max_tokens": 800,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Erreur lors de la reponse : {e}"

# ── HTTP helper ───────────────────────────────────────────────────────────────
def _ody(method: str, path: str, body: dict | None = None) -> dict:
    url  = ODYSSEUS_URL + path
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {ODYSSEUS_TOKEN}",
        "Accept": "application/json",
    }
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode(errors="replace")}
    except Exception as e:
        return {"error": str(e)}

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
client  = discord.Client(intents=intents)
tree    = app_commands.CommandTree(client)

@client.event
async def on_ready():
    # Sync slash commands per guild (immediate) + global fallback
    synced_guilds = []
    for guild in client.guilds:
        try:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            synced_guilds.append(guild.name)
        except Exception as e:
            print(f"[Bulle] Sync failed for {guild.name}: {e}")
    await tree.sync()  # global sync as fallback
    print(f"[Bulle] Connecte en tant que {client.user}")
    print(f"[Bulle] Commandes syncees sur: {', '.join(synced_guilds) or 'aucun serveur'}")

# ── /todos ────────────────────────────────────────────────────────────────────
@tree.command(name="todos", description="Affiche tes tâches Odysseus")
async def todos(interaction: discord.Interaction):
    await interaction.response.defer()
    data = _ody("GET", "/api/codex/todos")
    items = data.get("todos") or data.get("items") or []
    if not items:
        await interaction.followup.send("📋 Aucune tâche pour l'instant.")
        return
    lines = []
    for t in items[:15]:
        done  = "✅" if t.get("completed") else "🔲"
        title = t.get("title", "?")
        due   = f" _(échéance : {t['due_date'][:10]})_" if t.get("due_date") else ""
        lines.append(f"{done} {title}{due}")
    await interaction.followup.send("**📋 Tes tâches :**\n" + "\n".join(lines))

# ── /rappel ───────────────────────────────────────────────────────────────────
@tree.command(name="rappel", description="Crée un rappel (ex: /rappel Appeler dentiste demain 14h)")
@app_commands.describe(titre="Ce dont tu veux te rappeler", quand="Quand (ex: demain 9h, dans 2h, lundi 10h)")
async def rappel(interaction: discord.Interaction, titre: str, quand: str):
    await interaction.response.defer()
    data = _ody("POST", "/api/codex/todos", {
        "action": "add",
        "title": titre,
        "due_date": quand,
    })
    if data.get("error"):
        await interaction.followup.send(f"❌ Erreur : {data['error'][:200]}")
    else:
        await interaction.followup.send(f"⏰ Rappel créé : **{titre}** — _{quand}_")

# ── /todo_add ─────────────────────────────────────────────────────────────────
@tree.command(name="todo_add", description="Ajoute une tâche sans date")
@app_commands.describe(titre="La tâche à ajouter")
async def todo_add(interaction: discord.Interaction, titre: str):
    await interaction.response.defer()
    data = _ody("POST", "/api/codex/todos", {"action": "add", "title": titre})
    if data.get("error"):
        await interaction.followup.send(f"❌ {data['error'][:200]}")
    else:
        await interaction.followup.send(f"✅ Tâche ajoutée : **{titre}**")

# ── /emails ───────────────────────────────────────────────────────────────────
@tree.command(name="emails", description="Résumé de tes derniers mails")
@app_commands.describe(nombre="Nombre de mails à afficher (défaut: 5)")
async def emails(interaction: discord.Interaction, nombre: int = 5):
    await interaction.response.defer()
    data = _ody("GET", f"/api/codex/emails?folder=INBOX&limit={min(nombre, 10)}&offset=0&filter=all")
    mails = data.get("emails", [])
    if not mails:
        await interaction.followup.send("📭 Aucun mail trouvé.")
        return
    lines = [f"**📬 {len(mails)} derniers mails** (total boîte : {data.get('total','?')})"]
    for m in mails:
        read  = "📩" if not m.get("is_read") else "📧"
        sender = m.get("from_name") or m.get("from_address", "?")
        subj  = m.get("subject", "(sans objet)")[:60]
        lines.append(f"{read} **{sender}** — {subj}")
    await interaction.followup.send("\n".join(lines))

# ── /agenda ───────────────────────────────────────────────────────────────────
@tree.command(name="agenda", description="Tes événements des 7 prochains jours")
async def agenda(interaction: discord.Interaction):
    await interaction.response.defer()
    now   = datetime.now(timezone.utc)
    end   = now + timedelta(days=7)
    data  = _ody("GET", f"/api/codex/calendar/events?start={now.isoformat()}&end={end.isoformat()}")
    evts  = data.get("events", [])
    if not evts:
        await interaction.followup.send("📅 Aucun événement dans les 7 prochains jours.")
        return
    lines = ["**📅 Agenda — 7 prochains jours :**"]
    for e in evts[:10]:
        start = e.get("dtstart", "")[:16].replace("T", " ")
        title = e.get("summary", "?")
        lines.append(f"• `{start}` — {title}")
    await interaction.followup.send("\n".join(lines))

# ── /note ─────────────────────────────────────────────────────────────────────
@tree.command(name="note", description="Sauvegarde une info dans ta mémoire Odysseus")
@app_commands.describe(texte="Ce que tu veux mémoriser")
async def note(interaction: discord.Interaction, texte: str):
    await interaction.response.defer()
    data = _ody("POST", "/api/codex/memory", {
        "text": texte,
        "category": "note",
        "source": "discord",
    })
    if data.get("error"):
        await interaction.followup.send(f"❌ {data['error'][:200]}")
    else:
        await interaction.followup.send(f"🧠 Mémorisé : _{texte}_")

# ── /chat ─────────────────────────────────────────────────────────────────────
@tree.command(name="chat", description="Pose une question a Bulle")
@app_commands.describe(message="Ta question ou demande")
async def chat(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    reply = _chat(message, username=interaction.user.display_name)
    if len(reply) <= 2000:
        await interaction.followup.send(reply)
    else:
        chunks = [reply[i:i+1990] for i in range(0, len(reply), 1990)]
        await interaction.followup.send(chunks[0])
        for chunk in chunks[1:]:
            await interaction.channel.send(chunk)

# ── /memoire ──────────────────────────────────────────────────────────────────
@tree.command(name="memoire", description="Affiche tes notes mémorisées")
async def memoire(interaction: discord.Interaction):
    await interaction.response.defer()
    data  = _ody("GET", "/api/codex/memory")
    items = data.get("memories") or data.get("items") or []
    if not items:
        await interaction.followup.send("🧠 Aucune note en mémoire.")
        return
    lines = ["**🧠 Mémoire Odysseus :**"]
    for m in items[:10]:
        lines.append(f"• {m.get('text','?')[:100]}")
    await interaction.followup.send("\n".join(lines))

# ── Réponse aux mentions @Bulle ───────────────────────────────────────────────
@client.event
async def on_message(message: discord.Message):
    # Ignorer ses propres messages et les bots
    if message.author.bot:
        return

    # Nettoyer les éventuelles mentions du texte
    text = message.content
    for mention in message.mentions:
        text = text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
    text = text.strip()

    if not text:
        return

    async with message.channel.typing():
        reply = _chat(text, username=message.author.display_name)

    # Découper si > 2000 chars (limite Discord)
    if len(reply) <= 2000:
        await message.reply(reply)
    else:
        chunks = [reply[i:i+1990] for i in range(0, len(reply), 1990)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply(chunk)
            else:
                await message.channel.send(chunk)

# ── run ───────────────────────────────────────────────────────────────────────
client.run(DISCORD_TOKEN)
