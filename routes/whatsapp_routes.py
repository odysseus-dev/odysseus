"""
routes/whatsapp_routes.py - Integracao Evolution API (WhatsApp) + Odysseus
"""
import json, logging, sqlite3, os, uuid
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
import httpx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

EVOLUTION_URL      = os.environ.get("EVOLUTION_API_URL",  "https://evo.praxisis.com.br")
EVOLUTION_KEY      = os.environ.get("EVOLUTION_API_KEY",  "34237ede368f59f51004f700afc2b28c")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "edsonferreira1")
WA_DB_PATH         = Path(os.environ.get("ODYSSEUS_DATA_DIR", "data")) / "whatsapp.db"
WA_BOT_ENABLED     = os.environ.get("WA_BOT_ENABLED", "true").lower() == "true"
# Numeros autorizados a receber resposta do bot (so o numero, sem @s.whatsapp.net)
WA_ALLOWED_SENDERS = set(filter(None, os.environ.get("WA_ALLOWED_SENDERS", "554188331769").split(",")))

def _db():
    c = sqlite3.connect(str(WA_DB_PATH))
    c.row_factory = sqlite3.Row
    return c

def _init_db():
    with _db() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS wa_messages (
                id TEXT PRIMARY KEY,
                instance TEXT NOT NULL,
                remote_jid TEXT NOT NULL,
                from_me INTEGER NOT NULL DEFAULT 0,
                push_name TEXT,
                msg_type TEXT,
                content TEXT,
                timestamp INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_wa_jid_ts ON wa_messages(instance, remote_jid, timestamp DESC);
            CREATE TABLE IF NOT EXISTS wa_config (key TEXT PRIMARY KEY, value TEXT);
        ''')
_init_db()

def _get_config(key, default=None):
    with _db() as c:
        row = c.execute("SELECT value FROM wa_config WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

def _set_config(key, value):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO wa_config (key, value) VALUES (?,?)", (key, str(value)))

async def _evo_enrich_names(instance, jids_without_name: list):
    """Busca push_name de contatos sem nome na Evolution API e salva no banco."""
    if not jids_without_name:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for jid in jids_without_name[:20]:  # max 20 por vez
                r = await client.post(
                    f"{EVOLUTION_URL}/chat/findContacts/{instance}",
                    headers={"apikey": EVOLUTION_KEY, "Content-Type": "application/json"},
                    json={"where": {"remoteJid": jid}},
                )
                if r.status_code != 200:
                    continue
                contacts = r.json() if isinstance(r.json(), list) else []
                for c in contacts:
                    name = c.get("pushName") or c.get("name")
                    if name:
                        with _db() as db:
                            db.execute(
                                "UPDATE wa_messages SET push_name=? WHERE remote_jid=? AND (push_name IS NULL OR push_name='')",
                                (name, jid)
                            )
                        logger.info(f"[WhatsApp] Nome enriquecido: {jid} -> {name!r}")
    except Exception as e:
        logger.debug(f"[WhatsApp] enrich_names error: {e}")

async def _evo_send_text(instance, jid, text):
    url = f"{EVOLUTION_URL}/message/sendText/{instance}"
    headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json={"number": jid, "text": text}, headers=headers)
        r.raise_for_status()
    return r.json()

def _extract_content(data):
    msg = data.get("message", {})
    if not msg:
        return "[sem conteudo]", "unknown"
    if "conversation" in msg:
        return msg["conversation"], "text"
    if "extendedTextMessage" in msg:
        return msg["extendedTextMessage"].get("text", ""), "text"
    if "imageMessage" in msg:
        cap = msg["imageMessage"].get("caption", "")
        return f"[imagem]{': '+cap if cap else ''}", "image"
    if "audioMessage" in msg or "pttMessage" in msg:
        return "[audio]", "audio"
    if "videoMessage" in msg:
        cap = msg["videoMessage"].get("caption", "")
        return f"[video]{': '+cap if cap else ''}", "video"
    if "documentMessage" in msg:
        return f"[documento: {msg['documentMessage'].get('fileName','?')}]", "document"
    return "[midia nao suportada]", data.get("messageType", "unknown")

async def _llm_response(text, sender_name, sender_jid, history):
    try:
        import json as _json
        from core.database import SessionLocal, ModelEndpoint
        from src.endpoint_resolver import build_chat_url, build_headers, normalize_base
        from src.agent_loop import stream_agent_loop

        db2 = SessionLocal()
        _wa_model = _get_config("wa_model") or os.environ.get("WA_MODEL", "")
        ep_found = None
        model = None
        all_eps = db2.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        for _ep in all_eps:
            _c = _json.loads(_ep.cached_models) if _ep.cached_models else []
            _p = _json.loads(_ep.pinned_models) if _ep.pinned_models else []
            all_m = _p + _c
            if _wa_model and _wa_model in all_m:
                ep_found = _ep
                model = _wa_model
                break
        if not ep_found:
            for _ep in all_eps:
                _c = _json.loads(_ep.cached_models) if _ep.cached_models else []
                _p = _json.loads(_ep.pinned_models) if _ep.pinned_models else []
                for _m in (_p + _c):
                    if not any(x in _m.lower() for x in ("moondream","vision","embed","clip")):
                        ep_found = _ep
                        model = _m
                        break
                if ep_found:
                    break
        db2.close()
        if not ep_found or not model:
            return "Nenhum modelo configurado para responder via WhatsApp."

        base_url = normalize_base(ep_found.base_url)
        hdrs = build_headers(ep_found.api_key or "", base_url)
        endpoint_url = build_chat_url(base_url)

        system = (
            f"Voce e o Odysseus, assistente pessoal de Edson Ferreira, respondendo via WhatsApp.\n"
            f"Contato: {sender_name} ({sender_jid.replace('@s.whatsapp.net','')}).\n"
            f"Data/hora: {datetime.now().strftime('%d/%m/%Y %H:%M')}.\n"
            f"Voce tem acesso a TODAS as ferramentas do sistema: busca na web, notas, tarefas, email, calendario, e mais.\n"
            f"Use as ferramentas sempre que necessario para dar respostas completas e precisas.\n"
            f"Responda em portugues do Brasil. Seja direto e conciso (adequado para WhatsApp — sem markdown excessivo)."
        )
        messages = [{"role": "system", "content": system}]
        for m in history[-8:]:
            messages.append(m)
        messages.append({"role": "user", "content": text})

        logger.info(f"[WhatsApp] Agent loop: endpoint={ep_found.name!r} model={model!r}")

        # Selecionar tools relevantes igual o task_scheduler faz
        relevant_tools = None
        try:
            from src.tool_index import get_tool_index, ASSISTANT_ALWAYS_AVAILABLE
            tool_idx = get_tool_index()
            if tool_idx:
                rag_tools = tool_idx.get_tools_for_query(text, k=12)
                relevant_tools = rag_tools | ASSISTANT_ALWAYS_AVAILABLE
                # Tools WhatsApp sempre disponíveis
                relevant_tools |= {"whatsapp_send_message", "whatsapp_list_chats",
                                   "whatsapp_read_messages", "whatsapp_search"}
        except Exception as _te:
            logger.warning(f"[WhatsApp] tool index error: {_te}")

        import re as _re

        # Acumula o texto do round atual; reseta a cada novo round de ferramentas
        # para que só o texto da resposta final (sem fenced blocks) chegue ao WhatsApp
        current_round_text = ""
        final_text = ""
        tool_results = []

        async for event_str in stream_agent_loop(
            endpoint_url=endpoint_url,
            model=model,
            messages=messages,
            headers=hdrs,
            owner="edson",
            max_rounds=10,
            relevant_tools=relevant_tools,
        ):
            if event_str.startswith("data: [DONE]"):
                final_text = current_round_text
                break
            if not event_str.startswith("data: "):
                continue
            try:
                data = _json.loads(event_str[6:])
                if "delta" in data:
                    current_round_text += data["delta"]
                elif data.get("type") == "agent_step":
                    # Novo round começa — descarta texto anterior (era raciocínio/tool calls)
                    current_round_text = ""
                elif data.get("type") == "tool_output":
                    out = data.get("stdout") or data.get("output") or data.get("result") or ""
                    if isinstance(out, str) and out.strip():
                        tool_results.append(f"[{data.get('tool','?')}] {out[:300]}")
            except Exception:
                pass

        result = (final_text or current_round_text).strip()

        # Remove fenced code blocks que possam ter vazado (```bash ... ```)
        result = _re.sub(r'```[\w\-]*\n.*?```', '', result, flags=_re.DOTALL).strip()

        if not result and tool_results:
            result = "\n".join(tool_results[:3])
        if not result:
            result = "Nao consegui processar sua solicitacao. Tente novamente."

        # Remove tags de thinking
        try:
            from src.text_helpers import strip_think
            result = strip_think(result, prose=True, prompt_echo=True).strip() or result
        except Exception:
            pass

        return result
    except Exception as e:
        logger.error(f"[WhatsApp] LLM error: {e}", exc_info=True)
        return "Desculpe, ocorreu um erro ao processar sua mensagem."

async def _handle_incoming(instance, remote_jid, push_name, content, msg_type):
    if not WA_BOT_ENABLED or msg_type not in ("text",):
        return
    try:
        with _db() as c:
            rows = c.execute(
                "SELECT from_me, content FROM wa_messages WHERE instance=? AND remote_jid=? ORDER BY timestamp DESC LIMIT 12",
                (instance, remote_jid)
            ).fetchall()
        history = []
        for row in reversed(rows[1:]):
            history.append({"role": "assistant" if row["from_me"] else "user", "content": row["content"]})
        reply = await _llm_response(content, push_name or remote_jid, remote_jid, history)
        await _evo_send_text(instance, remote_jid, reply)
        with _db() as c:
            c.execute(
                "INSERT OR IGNORE INTO wa_messages (id,instance,remote_jid,from_me,push_name,msg_type,content,timestamp) VALUES (?,?,?,1,NULL,'text',?,?)",
                (str(uuid.uuid4()), instance, remote_jid, reply, int(datetime.now().timestamp()))
            )
    except Exception as e:
        logger.error(f"[WhatsApp] handler error: {e}", exc_info=True)

def setup_whatsapp_routes():

    @router.post("/webhook")
    async def webhook(request: Request, background_tasks: BackgroundTasks):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON invalido")
        event    = body.get("event", "")
        instance = body.get("instance", "")
        data     = body.get("data", {})
        if event not in ("messages.upsert", "message.upsert", "MESSAGES_UPSERT"):
            return {"status": "ignored", "event": event}
        key        = data.get("key", {})
        remote_jid = key.get("remoteJid", "")
        from_me    = bool(key.get("fromMe", False))
        msg_id     = key.get("id", str(uuid.uuid4()))
        push_name  = data.get("pushName", "")
        timestamp  = data.get("messageTimestamp", int(datetime.now().timestamp()))
        content, msg_type = _extract_content(data)
        if "@g.us" in remote_jid:
            return {"status": "ignored", "reason": "grupo"}
        try:
            with _db() as c:
                c.execute(
                    "INSERT OR IGNORE INTO wa_messages (id,instance,remote_jid,from_me,push_name,msg_type,content,timestamp) VALUES (?,?,?,?,?,?,?,?)",
                    (msg_id, instance, remote_jid, 1 if from_me else 0, push_name, msg_type, content, timestamp)
                )
        except Exception as ex:
            logger.warning(f"[WhatsApp] DB: {ex}")
        sender_number = remote_jid.split("@")[0]
        if not from_me and instance == EVOLUTION_INSTANCE and sender_number in WA_ALLOWED_SENDERS:
            background_tasks.add_task(_handle_incoming, instance, remote_jid, push_name, content, msg_type)
        return {"status": "ok", "from_me": from_me}

    @router.get("/chats")
    async def list_chats(request: Request, limit: int = 50):
        with _db() as c:
            rows = c.execute(
                """SELECT remote_jid,
                   MAX(CASE WHEN from_me=0 THEN push_name END) AS push_name,
                   MAX(timestamp) AS last_ts,
                   COUNT(*) AS msg_count,
                   SUM(CASE WHEN from_me=0 THEN 1 ELSE 0 END) AS received_count,
                   (SELECT content FROM wa_messages m2 WHERE m2.remote_jid=w.remote_jid AND m2.instance=w.instance ORDER BY timestamp DESC LIMIT 1) AS last_message
                   FROM wa_messages w WHERE instance=?
                   GROUP BY remote_jid ORDER BY last_ts DESC LIMIT ?""",
                (EVOLUTION_INSTANCE, limit)
            ).fetchall()
        result = [dict(r) for r in rows]
        # Enriquece nomes em background para contatos sem push_name
        missing = [r['remote_jid'] for r in result if not r.get('push_name')]
        if missing:
            import asyncio
            asyncio.create_task(_evo_enrich_names(EVOLUTION_INSTANCE, missing))
        return result

    @router.get("/messages/{jid:path}")
    async def get_messages(jid: str, request: Request, limit: int = 80):
        with _db() as c:
            rows = c.execute(
                "SELECT * FROM wa_messages WHERE remote_jid=? AND instance=? ORDER BY timestamp DESC LIMIT ?",
                (jid, EVOLUTION_INSTANCE, limit)
            ).fetchall()
        return list(reversed([dict(r) for r in rows]))

    @router.post("/send")
    async def send_message(request: Request):
        body = await request.json()
        jid  = body.get("jid", "").strip()
        text = body.get("text", "").strip()
        if not jid or not text:
            raise HTTPException(400, "jid e text sao obrigatorios")
        result = await _evo_send_text(EVOLUTION_INSTANCE, jid, text)
        with _db() as c:
            c.execute(
                "INSERT OR IGNORE INTO wa_messages (id,instance,remote_jid,from_me,push_name,msg_type,content,timestamp) VALUES (?,?,?,1,NULL,'text',?,?)",
                (str(uuid.uuid4()), EVOLUTION_INSTANCE, jid, text, int(datetime.now().timestamp()))
            )
        return result

    @router.get("/status")
    async def status(request: Request):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{EVOLUTION_URL}/instance/connectionState/{EVOLUTION_INSTANCE}",
                    headers={"apikey": EVOLUTION_KEY}
                )
                evo = r.json() if r.status_code == 200 else {"error": r.text}
        except Exception as e:
            evo = {"error": str(e)}
        with _db() as c:
            total = c.execute("SELECT COUNT(*) FROM wa_messages WHERE instance=?", (EVOLUTION_INSTANCE,)).fetchone()[0]
            chats = c.execute("SELECT COUNT(DISTINCT remote_jid) FROM wa_messages WHERE instance=?", (EVOLUTION_INSTANCE,)).fetchone()[0]
        return {"instance": EVOLUTION_INSTANCE, "bot_enabled": WA_BOT_ENABLED,
                "db_messages": total, "db_chats": chats, "evolution_status": evo}

    @router.get("/config")
    async def get_wa_config(request: Request):
        return {
            "wa_model": _get_config("wa_model", os.environ.get("WA_MODEL", "glm-4.7-flash")),
            "wa_endpoint_id": _get_config("wa_endpoint_id", ""),
            "bot_enabled": WA_BOT_ENABLED,
        }

    @router.post("/config")
    async def set_wa_config(request: Request):
        body = await request.json()
        if "wa_model" in body:
            _set_config("wa_model", body["wa_model"])
        if "wa_endpoint_id" in body:
            _set_config("wa_endpoint_id", body["wa_endpoint_id"])
        return {"ok": True}


    @router.get("/models")
    def list_wa_models(request: Request):
        """Retorna endpoints e modelos disponiveis para o seletor do WhatsApp.
        Nao exige admin — e uma visao publica dos modelos habilitados."""
        try:
            from core.database import SessionLocal, ModelEndpoint
            db = SessionLocal()
            try:
                eps = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
                result = []
                for ep in eps:
                    cached = json.loads(ep.cached_models) if getattr(ep, "cached_models", None) else []
                    pinned = json.loads(ep.pinned_models) if getattr(ep, "pinned_models", None) else []
                    models = pinned + [m for m in cached if m not in pinned]
                    if models:
                        result.append({
                            "id": ep.id,
                            "name": ep.name,
                            "is_enabled": ep.is_enabled,
                            "cached_models": cached,
                            "pinned_models": pinned,
                        })
                return result
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[WhatsApp] list_wa_models error: {e}", exc_info=True)
            return []

    return router