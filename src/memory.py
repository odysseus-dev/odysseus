
import json
import logging
import os
import time
import uuid
import re
from typing import List, Dict, Tuple, Callable, Any
from datetime import datetime

from core.file_lock import file_lock

logger = logging.getLogger(__name__)

def tokenize(text: str) -> List[str]:
    """Simple tokenizer that splits on whitespace and removes punctuation."""
    return [word.strip('.,!?";') for word in text.split()]

def get_text_similarity(text1: str, text2: str) -> float:
    """Calculate Jaccard similarity between two texts."""
    if not text1 or not text2:
        return 0.0
    
    tokens1 = set(tokenize(text1.lower()))
    tokens2 = set(tokenize(text2.lower()))
    
    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0
        
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    
    return len(intersection) / len(union)

class MemoryManager:
    def __init__(self, data_dir: str):
        self.memory_file = os.path.join(data_dir, "memory.json")
        self.ensure_file_exists()
        
    def extract_memory_from_chat(self, chat_history: List[Dict], session_id: str = None) -> List[Dict]:
        """
        Extract memory entries from chat history as a fallback when LLM fails.
        
        Args:
            chat_history: List of chat messages with 'role' and 'content' keys
            session_id: Optional session ID to associate with extracted memories
            
        Returns:
            List of memory entries with text, timestamp, and optional session_id
        """
        memories = []
        
        for msg in chat_history:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant":
                content = str(msg.get("content", ""))
                lines = content.split('\n')
                
                for line in lines:
                    line = line.strip()
                    # Look for bullet points or numbered lists that might contain memories
                    if re.match(r'^[-*•]|\d+\.', line):
                        # Extract the text after the bullet/number. Group both
                        # markers so the capture applies to either — the previous
                        # `^[-*•]|\d+\.\s*(.*)` put the group on the numbered branch
                        # only, so a bullet line matched with group(1)=None and
                        # crashed on .strip().
                        text_match = re.match(r'^(?:[-*•]|\d+\.)\s*(.*)', line)
                        if text_match:
                            text = text_match.group(1).strip()
                            if text:
                                memories.append({
                                    "text": text,
                                    "timestamp": int(datetime.now().timestamp()),
                                    "session_id": session_id
                                })
                    # If we see a heading that suggests memories
                    elif re.search(r'memory|fact|note|remember', line, re.I):
                        pass
                    # If we see a clear separator or end
                    elif re.match(r'^={3,}|-{3,}|_{3,}', line):
                        pass
                        
        return memories
        
    def process_inline_memory_command(self, message: str) -> Tuple[bool, str]:
        """
        Check if a message is an inline memory command (e.g. "remember: X").
        
        Args:
            message: The user message to check
            
        Returns:
            Tuple of (is_command, extracted_text) where is_command is True if 
            the message matches the memory command pattern
        """
        # Pattern for memory commands: "remember: X", "memorize: X", "save: X", etc.
        pattern = r'^(?:remember|memorize|save|note|store)[:\-]?\s+(.+)$'
        match = re.match(pattern, message.strip(), re.IGNORECASE)
        
        if match:
            return True, match.group(1).strip()
        else:
            return False, ""
    
    def ensure_file_exists(self):
        """Create memory file if it doesn't exist."""
        if not os.path.exists(self.memory_file):
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)
    
    def load_all(self) -> List[Dict]:
        """Load all memory entries from JSON file (unfiltered)."""
        if not os.path.exists(self.memory_file):
            return []

        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return self._validate_entries(data)
        except (json.JSONDecodeError, PermissionError) as e:
            logger.error("Error loading memory.json: %s", e)
            return self._migrate_from_legacy()

        return []

    def load(self, owner: str = None) -> List[Dict]:
        """Load memory entries, optionally filtered by owner."""
        entries = self.load_all()
        if owner is None:
            return entries
        return [e for e in entries if e.get("owner") == owner]
    
    def claim_ownerless(self, owner: str) -> None:
        """Assign all ownerless memory entries to ``owner`` (one-time migration).

        Atomic read-modify-write via mutate() so it can't clobber concurrent
        writes. Merged in from the former services/memory MemoryManager during
        unification."""

        def _claim(entries):
            claimed = 0
            for e in entries:
                if not e.get("owner"):
                    e["owner"] = owner
                    claimed += 1
            return entries, claimed

        claimed = self.mutate(_claim)
        if claimed:
            logger.info("Claimed %d ownerless memories for %s", claimed, owner)

    def _validate_entries(self, entries: List[Dict]) -> List[Dict]:
        """Ensure all entries have required fields."""
        validated = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if "id" not in entry:
                entry["id"] = str(uuid.uuid4())
            if "timestamp" not in entry:
                entry["timestamp"] = int(time.time())
            if "source" not in entry:
                entry["source"] = "unknown"
            if "category" not in entry:
                entry["category"] = "fact"
            if "uses" not in entry:
                entry["uses"] = 0
            validated.append(entry)
        return validated
    
    def _migrate_from_legacy(self) -> List[Dict]:
        """Migrate from old text format to JSON if needed."""
        legacy_path = os.path.join(os.path.dirname(self.memory_file), "memory.txt")
        if not os.path.exists(legacy_path):
            return []
            
        logger.info("Converting legacy memory.txt to new JSON format")
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]
            
            entries = []
            for line in lines:
                entries.append({
                    "id": str(uuid.uuid4()),
                    "text": line,
                    "timestamp": int(time.time()),
                    "source": "user",
                    "category": "fact"
                })
            
            self.save(entries)
            return entries
        except Exception as e:
            logger.error("Failed to convert legacy memory: %s", e)
            return []
    
    def save(self, entries: List[Dict]):
        """Save memory entries to JSON file."""
        # Validate entries before saving
        for entry in entries:
            if "id" not in entry:
                entry["id"] = str(uuid.uuid4())
            if "timestamp" not in entry:
                entry["timestamp"] = int(time.time())
            if "source" not in entry:
                entry["source"] = "user"
            if "category" not in entry:
                entry["category"] = "fact"
        
        # Atomic write. The tmp file carries the live PID so two processes saving
        # the same file (the main app + the memory MCP subprocess, or parallel
        # tests) never collide on the rename target; fsync before replace for
        # durability (mirrors core/atomic_io.atomic_write_json).
        tmp_file = f"{self.memory_file}.tmp.{os.getpid()}"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, self.memory_file)

    def mutate(self, fn: Callable[[List[Dict]], Tuple[List[Dict], Any]]) -> Any:
        """Atomically read-modify-write memory.json under a cross-process lock.

        ``fn(entries)`` receives the freshly-loaded entries (already under the
        lock) and must return ``(new_entries, result)``: ``new_entries`` is
        persisted and ``result`` is returned to the caller. Running the whole
        load -> modify -> save cycle inside one lock acquisition is what closes
        the lost-update window (two unguarded cycles otherwise silently drop one
        write — see core/file_lock).

        ``fn`` MUST be fast and purely in-memory. Never run an LLM/network call
        inside ``fn`` (it would hold the cross-process lock for the call's full
        duration), and never call ``mutate()``/``file_lock()`` again from within
        ``fn`` (the lock is not reentrant). For LLM-driven edits, do the slow work
        first, then call ``mutate()`` with an ``fn`` that merges the result onto
        the fresh list.
        """
        with file_lock(self.memory_file):
            entries = self.load_all()
            new_entries, result = fn(entries)
            self.save(new_entries)
            return result
    
    def add_entry(self, text: str, source: str = "user", category: str = "fact", owner: str = None) -> Dict:
        """Add a new memory entry."""
        if not text.strip():
            raise ValueError("Memory text cannot be empty")

        entry = {
            "id": str(uuid.uuid4()),
            "text": text.strip(),
            "timestamp": int(time.time()),
            "source": source,
            "category": category,
            "uses": 0,
        }
        if owner:
            entry["owner"] = owner
        return entry

    def increment_uses(self, ids: List[str]) -> None:
        """Bump the uses counter for each memory id. Called after a memory has
        actually been injected into a chat's context (not just retrieved).

        Atomic read-modify-write via mutate() so a concurrent writer (or the
        background consolidation) can't clobber the bump."""
        if not ids:
            return
        id_set = set(ids)

        def _bump(entries):
            for e in entries:
                if e.get("id") in id_set:
                    e["uses"] = int(e.get("uses", 0) or 0) + 1
            return entries, None

        self.mutate(_bump)
    
    def find_duplicates(self, text: str, entries: List[Dict] = None) -> List[Dict]:
        """Find duplicate memory entries based on text content."""
        if entries is None:
            entries = self.load()
            
        text_lower = text.strip().lower()
        return [entry for entry in entries if entry["text"].lower() == text_lower]
            
    def categorize_memory_by_relevance(self, message: str, memories: list):
        """Categorize memories by type and relevance"""
        categories = {
            "contacts": [],
            "preferences": [],
            "facts": [],
            "tasks": []
        }
        
        msg_lower = message.lower()
        
        for mem in memories:
            text_lower = mem["text"].lower()
            
            # Contact info
            if any(word in text_lower for word in ["phone", "email", "address", "lives", "works"]):
                if any(word in msg_lower for word in ["contact", "phone", "address", "email"]):
                    categories["contacts"].append(mem)
            
            # Personal preferences
            elif any(word in text_lower for word in ["likes", "dislikes", "prefers", "favorite"]):
                if any(word in msg_lower for word in ["like", "prefer", "favorite", "want"]):
                    categories["preferences"].append(mem)
            
            # Tasks and todos
            elif any(word in text_lower for word in ["todo", "task", "remind", "meeting"]):
                if any(word in msg_lower for word in ["todo", "task", "schedule", "remind"]):
                    categories["tasks"].append(mem)
            
            # General facts - only if very relevant
            else:
                if get_text_similarity(message, mem["text"]) > 0.4:
                    categories["facts"].append(mem)
        
        return categories

    def get_relevant_memories(self, query: str, memories: list, threshold: float = 0.05, max_items: int = 8):
        """Get memories that are relevant to the query based on text similarity and semantic keyword matching."""
        if not memories or not query.strip():
            return []
            
        # Define keyword categories for semantic matching
        identity_words = ["name", "who", "i", "am", "called", "identity", "myself", "me", "my"]
        contact_words = ["phone", "email", "address", "contact", "number", "where", "located", "reach"]
        preference_words = ["like", "prefer", "favorite", "want", "love", "hate", "dislike", "enjoy", "interested"]
        task_words = ["todo", "task", "remind", "meeting", "appointment", "schedule", "deadline"]
        fact_words = ["what", "when", "where", "how", "why", "explain", "describe", "information", "know"]
        
        query_lower = query.lower()
        
        # Determine query type based on keywords
        query_type = None
        if any(word in query_lower for word in identity_words):
            query_type = "identity"
        elif any(word in query_lower for word in contact_words):
            query_type = "contact"
        elif any(word in query_lower for word in preference_words):
            query_type = "preference"
        elif any(word in query_lower for word in task_words):
            query_type = "task"
        elif any(word in query_lower for word in fact_words):
            query_type = "fact"
        
        relevant = []
        identity_memories = []
        other_memories = []
        
        # Separate identity memories from others
        for memory in memories:
            memory_text = memory["text"].lower()
            # Check if this is an identity memory (contains name patterns or identity indicators)
            is_identity = any([
                re.search(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', memory["text"]),
                any(word in memory_text for word in ["name is", "i'm", "i am", "called", "my name", "named", "call me"])
            ])
            if is_identity:
                identity_memories.append(memory)
            else:
                other_memories.append(memory)
        
        # For identity queries, include all identity memories regardless of similarity
        if query_type == "identity" and identity_memories:
            # Give them high scores to ensure they're included first
            for memory in identity_memories:
                relevant.append((0.9, memory))  # High score for identity memories in identity queries
        
        # Process other memories with similarity scoring
        for memory in other_memories:
            memory_text = memory["text"].lower()
            memory_tokens = set(tokenize(memory_text))
            query_tokens = set(tokenize(query_lower))
            
            # Calculate base Jaccard similarity
            if not query_tokens or not memory_tokens:
                continue
                
            base_similarity = len(query_tokens & memory_tokens) / len(query_tokens | memory_tokens)
            final_score = base_similarity
            
            # Apply boosts based on semantic matching
            if query_type == "contact":
                # Boost memories with contact information
                has_contact_info = any(word in memory_text for word in ["@gmail.com", "@", ".com", 
                                                                     "phone", "number", "address", 
                                                                     "http", "www", "tel:"])
                if has_contact_info:
                    final_score *= 1.4  # 40% boost for contact-related memories
            
            elif query_type == "preference":
                # Boost memories with preference indicators
                has_preference = any(word in memory_text for word in ["like", "love", "hate", "dislike", 
                                                                   "prefer", "favorite", "enjoy", "interested"])
                if has_preference:
                    final_score *= 1.3  # 30% boost for preference-related memories
            
            elif query_type == "task":
                # Boost memories with task indicators
                has_task = any(word in memory_text for word in ["todo", "task", "remind", "meeting", 
                                                              "appointment", "schedule", "deadline", "need to"])
                if has_task:
                    final_score *= 1.3  # 30% boost for task-related memories
            
            # Always consider exact phrase matches as highly relevant
            if query.lower() in memory["text"].lower():
                final_score = max(final_score, 0.8)  # Ensure high relevance for exact matches
            
            # Include memory if it meets threshold after boosts
            if final_score >= threshold:
                relevant.append((final_score, memory))
        
        # Sort by final score (descending) and return top matches
        relevant.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in relevant[:max_items]]
