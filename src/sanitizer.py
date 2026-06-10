# src/sanitizer.py
import os
import httpx
import logging
import asyncio
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Load config from environment directly to avoid circular imports with core.constants
def get_config():
    return {
        "enabled": os.getenv("PII_SANITIZATION_ENABLED", "False").lower() == "true",
        "url": os.getenv("PII_SANITIZER_URL", ""),
        "policy": os.getenv("PII_SANITIZATION_POLICY", "warn").lower(),
        "timeout": float(os.getenv("PII_SANITIZER_TIMEOUT", "5"))
    }

async def sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sanitize PII from messages before sending to LLM.
    Uses the configured sanitizer endpoint.
    """
    config = get_config()
    if not config["enabled"] or not config["url"]:
        return messages

    try:
        # Extract all text segments that need sanitization to batch them if possible,
        # but for a generic architecture, we'll process them in parallel.
        
        tasks = []
        indices = [] # Keep track of where each text came from
        
        for i, msg in enumerate(messages):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                tasks.append(_sanitize_text(content))
                indices.append((i, None))
            elif isinstance(content, list):
                for j, block in enumerate(content):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            tasks.append(_sanitize_text(text))
                            indices.append((i, j))

        if not tasks:
            return messages

        # Run all sanitization requests in parallel
        results = await asyncio.gather(*tasks)
        
        # Reconstruct messages with sanitized content
        sanitized_messages = [dict(m) for m in messages]
        for (msg_idx, block_idx), sanitized_text in zip(indices, results):
            if block_idx is None:
                sanitized_messages[msg_idx]["content"] = sanitized_text
            else:
                # Deep copy the block to avoid mutating the original message list
                new_block = dict(sanitized_messages[msg_idx]["content"][block_idx])
                new_block["text"] = sanitized_text
                
                # Create a new list for content to ensure we don't mutate shared references
                new_content = list(sanitized_messages[msg_idx]["content"])
                new_content[block_idx] = new_block
                sanitized_messages[msg_idx]["content"] = new_content
                
        return sanitized_messages

    except Exception as e:
        logger.error(f"PII sanitization failed: {e}")
        policy = config["policy"]
        if policy == "block":
            raise RuntimeError(f"Request blocked: PII sanitization failed and policy is 'block'. Error: {e}")
        elif policy == "warn":
            logger.warning("PII sanitization failed; proceeding with unsanitized messages per policy.")
        
        return messages

def sanitize_messages_sync(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Synchronous wrapper for sanitize_messages."""
    config = get_config()
    if not config["enabled"] or not config["url"]:
        return messages
    
    try:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We are in a thread with a running loop (e.g. FastAPI worker thread)
                # This is tricky in Python. For sync calls in FastAPI threads, 
                # we can use a fresh loop or run_coroutine_threadsafe.
                # However, llm_call is often called from FastAPI's threadpool.
                return asyncio.run(_sanitize_messages_async_wrapper(messages))
            else:
                return loop.run_until_complete(sanitize_messages(messages))
        except RuntimeError:
            return asyncio.run(sanitize_messages(messages))
    except Exception as e:
        logger.error(f"PII sanitization (sync) failed: {e}")
        if config["policy"] == "block":
            raise RuntimeError(f"Request blocked: PII sanitization failed and policy is 'block'. Error: {e}")
        return messages

async def _sanitize_messages_async_wrapper(messages):
    return await sanitize_messages(messages)

async def _sanitize_text(text: str) -> str:
    """Send text to sanitizer endpoint and return sanitized version."""
    if not text or not text.strip():
        return text

    config = get_config()
    try:
        async with httpx.AsyncClient(timeout=config["timeout"]) as client:
            # Generic provider architecture: POST JSON with a "text" field.
            # This supports TrustBoost and other common PII scrubbing APIs.
            payload = {"text": text}
            response = await client.post(config["url"], json=payload)
            response.raise_for_status()
            
            data = response.json()
            
            # Heuristic to find sanitized content in generic JSON response
            # 1. TrustBoost: data["data"]["sanitized_content"]
            # 2. Common: data["sanitized_text"] or data["text"]
            # 3. Fallback: Check if response is just a string or has a clear result field
            
            if isinstance(data, str):
                return data
                
            sanitized = None
            if isinstance(data, dict):
                # Try common keys
                keys = ["sanitized_content", "sanitized_text", "text", "output", "result"]
                for k in keys:
                    if k in data:
                        sanitized = data[k]
                        break
                
                # Try nested data (TrustBoost style)
                if sanitized is None and "data" in data and isinstance(data["data"], dict):
                    inner = data["data"]
                    for k in keys:
                        if k in inner:
                            sanitized = inner[k]
                            break
            
            if sanitized is not None:
                return str(sanitized)
            
            logger.warning(f"Sanitizer at {PII_SANITIZER_URL} returned unrecognized format: {data}")
            return text

    except Exception as e:
        logger.debug(f"Error calling sanitizer at {PII_SANITIZER_URL}: {e}")
        raise e
