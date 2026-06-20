import os
import httpx
import logging
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig

router = APIRouter(prefix="/webhook/whatsapp", tags=["whatsapp"])

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "dummy_token")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dummy_verify")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "dummy_id")

@router.get("/")
async def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    """WhatsApp webhook verification endpoint."""
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logging.info("Webhook verified.")
        return int(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")

@router.post("/")
async def handle_whatsapp_message(request: Request):
    """Handle incoming WhatsApp messages."""
    data = await request.json()
    
    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if not messages:
            return {"status": "ignored"}
            
        message = messages[0]
        from_number = message.get("from")
        text = message.get("text", {}).get("body", "")
        
        if text:
            # Dispatch agent work asynchronously to avoid blocking the webhook response
            import asyncio
            asyncio.create_task(process_with_agent(from_number, text))
            
    except Exception as e:
        logging.error(f"Error processing message: {e}")
        
    return {"status": "ok"}

async def process_with_agent(to_number: str, text: str):
    """Initializes Antigravity Agent and sends response back via WhatsApp."""
    config = LocalAgentConfig(
        system_instructions="You are a remote assistant managed via WhatsApp.",
        capabilities=CapabilitiesConfig()
    )
    
    try:
        async with Agent(config) as agent:
            response = await agent.chat(text)
            
            full_reply = ""
            async for token in response:
                full_reply += token
                
            await send_whatsapp_message(to_number, full_reply)
    except Exception as e:
        await send_whatsapp_message(to_number, f"Agent error: {e}")

async def send_whatsapp_message(to_number: str, text: str):
    """Sends a text message back to the WhatsApp user."""
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text}
    }
    
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, headers=headers)
