"""
JUNIPERUS110_INFINITE_MIND_BRIDGE

Governed access from Juniperus to the local Infinite Mind / Infinite Brain workspace.
- READ-ONLY by default
- No mutations
- No external calls
- No secret storage
"""

import json
import logging
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Core paths
BASE_DIR = Path(__file__).resolve().parents[2]
INFINITE_BRAIN_ROOT = Path("C:/Users/iamcy/CymaticsDev/06_INFINITE_BRAIN")
INFINITE_MIND_DATA_ROOT = BASE_DIR / "data" / "gnexus" / "infinite-mind"
MISSION_CONTROL_ROOT = BASE_DIR / "data" / "gnexus" / "mission-control"

# File type safe list
SAFE_EXTENSIONS = {
    ".md", ".txt", ".json", ".jsonl",
    ".yaml", ".yml", ".ps1", ".py",
    ".html", ".csv", ".log",
}

# Dangerous patterns
DANGEROUS_PATTERNS = {
    ".git", "venv", "node_modules", "__pycache__",
    "dist", "build", ".cache", "logs",
}

# File classifications
CLASSIFICATIONS = {
    "finalizer", "receipt", "verifier", "mission-control",
    "memory", "skill", "canon", "protocol", "runbook",
    "dashboard", "bridge", "repair", "replay", "ledger", "unknown",
}

# Secret redaction patterns
SECRET_PATTERNS = [
    r"api[_-]?key",
    r"token",
    r"secret",
    r"password",
    r"Authorization",
    r"X-API-Key",
    r"Bearer",
    r"AWS_",
]


class InfiniteMindBridge:
    """Governed access to Infinite Mind."""

    def __init__(self):
        self.source_root = INFINITE_BRAIN_ROOT
        self.data_root = INFINITE_MIND_DATA_ROOT
        self.mission_root = MISSION_CONTROL_ROOT
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Ensure required directories exist."""
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "context-packs").mkdir(parents=True, exist_ok=True)
        self.mission_root.mkdir(parents=True, exist_ok=True)

    def get_infinite_mind_state(self) -> Dict[str, Any]:
        """
        Get the current state of Infinite Mind binding.
        
        Returns state classification: missing, exists_empty, exists_unscanned,
        indexed, indexed_with_warnings, error
        """
        state_file = self.data_root / "source-binding.json"
        
        if state_file.exists():
            try:
                return json.loads(state_file.read_text(encoding="utf-8-sig"))
            except Exception as e:
                logger.error(f"Failed to load source binding: {e}")
                return self._error_state(str(e))
        
        # Classify current state
        if not self.source_root.exists():
            state = self._missing_state()
        elif not any(self.source_root.iterdir()):
            state = self._empty_state()
        else:
            state = self._unscanned_state()
        
        # Save state
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        
        return state

    def _missing_state(self) -> Dict[str, Any]:
        """Source root does not exist."""
        return {
            "sourceRoot": str(self.source_root),
            "scanStatus": "missing",
            "exists": False,
            "indexed": False,
            "indexedAt": None,
            "fileCount": 0,
            "candidateCount": 0,
            "contextPackCount": 0,
            "warnings": ["Source root does not exist"],
            "mutationAllowed": False,
            "writebackAllowed": False,
            "externalCalls": False,
            "secretsStored": False,
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }

    def _empty_state(self) -> Dict[str, Any]:
        """Source root exists but is empty."""
        return {
            "sourceRoot": str(self.source_root),
            "scanStatus": "exists_empty",
            "exists": True,
            "indexed": False,
            "indexedAt": None,
            "fileCount": 0,
            "candidateCount": 0,
            "contextPackCount": 0,
            "warnings": ["Source root is empty"],
            "mutationAllowed": False,
            "writebackAllowed": False,
            "externalCalls": False,
            "secretsStored": False,
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }

    def _unscanned_state(self) -> Dict[str, Any]:
        """Source root exists but has not been indexed."""
        return {
            "sourceRoot": str(self.source_root),
            "scanStatus": "exists_unscanned",
            "exists": True,
            "indexed": False,
            "indexedAt": None,
            "fileCount": 0,
            "candidateCount": 0,
            "contextPackCount": 0,
            "warnings": ["Source root exists but has not been scanned"],
            "mutationAllowed": False,
            "writebackAllowed": False,
            "externalCalls": False,
            "secretsStored": False,
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }

    def _error_state(self, error: str) -> Dict[str, Any]:
        """Error state."""
        return {
            "sourceRoot": str(self.source_root),
            "scanStatus": "error",
            "exists": False,
            "indexed": False,
            "indexedAt": None,
            "fileCount": 0,
            "candidateCount": 0,
            "contextPackCount": 0,
            "warnings": [f"Error: {error}"],
            "mutationAllowed": False,
            "writebackAllowed": False,
            "externalCalls": False,
            "secretsStored": False,
            "generatedAt": datetime.utcnow().isoformat() + "Z",
        }

    def scan_infinite_mind(self, max_size_mb: int = 10) -> Dict[str, Any]:
        """
        Scan Infinite Brain and index safe files.
        
        LAYER 2: Safe Scanner
        """
        if not self.source_root.exists():
            logger.warning(f"Source root not found: {self.source_root}")
            return {"status": "missing", "warnings": ["Source root does not exist"]}

        file_index = []
        candidate_records = []
        source_map = {}
        warnings = []
        indexed_count = 0
        candidate_count = 0

        # Walk and index
        try:
            for file_path in self.source_root.rglob("*"):
                if not file_path.is_file():
                    continue

                # Check danger patterns
                if any(danger in str(file_path) for danger in DANGEROUS_PATTERNS):
                    continue

                # Check extension
                if file_path.suffix.lower() not in SAFE_EXTENSIONS:
                    continue

                # Check size
                try:
                    size_bytes = file_path.stat().st_size
                    if size_bytes > max_size_mb * 1024 * 1024:
                        warnings.append(f"File too large: {file_path.relative_to(self.source_root)}")
                        continue
                except OSError:
                    continue

                # Read and classify
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    
                    # Redact secrets
                    redacted = self.redact_sensitive_text(content)
                    
                    # Classify
                    classification = self._classify_file(file_path, content)
                    
                    # Extract snippet
                    snippet = content[:500] if content else ""
                    
                    # Compute hash
                    file_hash = hashlib.sha256(content.encode()).hexdigest()
                    
                    # Guess title
                    title_guess = self._guess_title(file_path, content)
                    
                    # Extract tags
                    tags = self._extract_tags(file_path, content)
                    
                    record = {
                        "id": file_hash[:16],
                        "relativePath": str(file_path.relative_to(self.source_root)),
                        "absolutePath": str(file_path),
                        "fileType": file_path.suffix.lower(),
                        "sizeBytes": size_bytes,
                        "modifiedAt": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
                        "sha256": file_hash,
                        "classification": classification,
                        "titleGuess": title_guess,
                        "snippet": snippet[:200],
                        "tags": tags,
                        "confidence": 0.8,
                        "warnings": [],
                    }
                    
                    file_index.append(record)
                    candidate_records.append(record)
                    indexed_count += 1
                    candidate_count += 1
                    source_map[record["id"]] = record["relativePath"]
                    
                except (UnicodeDecodeError, OSError) as e:
                    warnings.append(f"Failed to read {file_path.relative_to(self.source_root)}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Scan error: {e}")
            warnings.append(str(e))

        # Save indexes
        self._save_json(self.data_root / "file-index.json", file_index)
        self._save_json(self.data_root / "candidate-records.json", candidate_records)
        self._save_json(self.data_root / "source-map.json", source_map)

        # Save scan report
        report = {
            "status": "indexed" if indexed_count > 0 else "empty",
            "scanStatus": "indexed_with_warnings" if warnings else "indexed",
            "sourceRoot": str(self.source_root),
            "scannedAt": datetime.utcnow().isoformat() + "Z",
            "fileCount": indexed_count,
            "candidateCount": candidate_count,
            "warnings": warnings,
            "mutationAllowed": False,
            "writebackAllowed": False,
            "externalCalls": False,
            "secretsStored": False,
        }
        self._save_json(self.data_root / "scan-report.json", report)

        # Update source binding
        binding = self.get_infinite_mind_state()
        binding.update({
            "scanStatus": report["scanStatus"],
            "indexed": True,
            "indexedAt": report["scannedAt"],
            "fileCount": indexed_count,
            "candidateCount": candidate_count,
            "warnings": warnings,
        })
        self._save_json(self.data_root / "source-binding.json", binding)

        return report

    def load_index(self) -> Optional[List[Dict[str, Any]]]:
        """Load the file index (LAYER 2)."""
        index_file = self.data_root / "file-index.json"
        if not index_file.exists():
            return None
        try:
            return json.loads(index_file.read_text(encoding="utf-8-sig"))
        except Exception as e:
            logger.error(f"Failed to load index: {e}")
            return None

    def search_infinite_mind(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search the indexed Infinite Mind (LAYER 4).
        Search uses generated index files, not recursive scan on each request.
        """
        index = self.load_index()
        if not index:
            return []

        query_lower = query.lower()
        results = []

        for record in index:
            score = 0.0
            
            # Title match (highest weight)
            if query_lower in record.get("titleGuess", "").lower():
                score += 2.0
            
            # Tag match
            for tag in record.get("tags", []):
                if query_lower in tag.lower():
                    score += 1.5
            
            # Classification match
            if query_lower in record.get("classification", "").lower():
                score += 1.0
            
            # Content snippet match
            if query_lower in record.get("snippet", "").lower():
                score += 0.5
            
            # Path match
            if query_lower in record.get("relativePath", "").lower():
                score += 0.3
            
            if score > 0:
                record["searchScore"] = score
                results.append(record)

        # Sort by score
        results.sort(key=lambda r: r["searchScore"], reverse=True)
        return results[:limit]

    def list_context_packs(self) -> List[Dict[str, Any]]:
        """List all context packs (LAYER 3, 4)."""
        packs_dir = self.data_root / "context-packs"
        packs_dir.mkdir(parents=True, exist_ok=True)
        
        index_file = packs_dir / "index.json"
        if not index_file.exists():
            return []
        
        try:
            return json.loads(index_file.read_text(encoding="utf-8-sig"))
        except Exception as e:
            logger.error(f"Failed to load context packs: {e}")
            return []

    def load_context_pack(self, pack_id: str) -> Optional[Dict[str, Any]]:
        """Load a specific context pack (LAYER 3, 4)."""
        packs_dir = self.data_root / "context-packs"
        pack_file = packs_dir / f"{pack_id}.json"
        
        if not pack_file.exists():
            return None
        
        try:
            return json.loads(pack_file.read_text(encoding="utf-8-sig"))
        except Exception as e:
            logger.error(f"Failed to load pack {pack_id}: {e}")
            return None

    def assemble_context_bundle(
        self, pack_ids: List[str], search_terms: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Assemble an operation context bundle from selected context packs (LAYER 6).
        Used by operator loop.
        """
        bundle = {
            "bundleId": hashlib.sha256(
                (str(pack_ids) + str(search_terms or [])).encode()
            ).hexdigest()[:16],
            "createdAt": datetime.utcnow().isoformat() + "Z",
            "contextPacks": [],
            "searchResults": [],
        }

        # Load requested packs
        for pack_id in pack_ids:
            pack = self.load_context_pack(pack_id)
            if pack:
                bundle["contextPacks"].append(pack)

        # Load search results
        if search_terms:
            all_results = []
            for term in search_terms:
                results = self.search_infinite_mind(term, limit=5)
                all_results.extend(results)
            # Deduplicate
            seen_ids = set()
            for result in all_results:
                record_id = result.get("id")
                if record_id and record_id not in seen_ids:
                    bundle["searchResults"].append(result)
                    seen_ids.add(record_id)

        return bundle

    @staticmethod
    def redact_sensitive_text(text: str) -> str:
        """Redact common secret patterns from text."""
        redacted = text
        for pattern in SECRET_PATTERNS:
            redacted = re.sub(
                f"({pattern})\\s*[:=]\\s*[^\\s,;}}]+",
                r"\1=***REDACTED***",
                redacted,
                flags=re.IGNORECASE,
            )
        return redacted

    @staticmethod
    def _classify_file(file_path: Path, content: str) -> str:
        """Classify a file based on name and content."""
        name_lower = file_path.name.lower()
        content_lower = content[:1000].lower()

        if "finalizer" in name_lower or "finalizer" in content_lower:
            return "finalizer"
        elif "receipt" in name_lower or "receipt" in content_lower:
            return "receipt"
        elif "verifier" in name_lower or "verifier" in content_lower:
            return "verifier"
        elif "mission" in name_lower or "mission" in content_lower:
            return "mission-control"
        elif "memory" in name_lower or "memory" in content_lower:
            return "memory"
        elif "skill" in name_lower or "skill" in content_lower:
            return "skill"
        elif "canon" in name_lower or "canon" in content_lower:
            return "canon"
        elif "protocol" in name_lower or "protocol" in content_lower:
            return "protocol"
        elif "runbook" in name_lower or "runbook" in content_lower:
            return "runbook"
        elif "dashboard" in name_lower or "dashboard" in content_lower:
            return "dashboard"
        elif "bridge" in name_lower or "bridge" in content_lower:
            return "bridge"
        elif "repair" in name_lower or "repair" in content_lower:
            return "repair"
        elif "replay" in name_lower or "replay" in content_lower:
            return "replay"
        elif "ledger" in name_lower or "ledger" in content_lower:
            return "ledger"
        else:
            return "unknown"

    @staticmethod
    def _guess_title(file_path: Path, content: str) -> str:
        """Guess a title from filename or first line of content."""
        name = file_path.stem
        if name and name != "README":
            return name
        
        # Try first line
        first_line = content.split("\n")[0].strip()
        if first_line and len(first_line) < 100 and not first_line.startswith("{"):
            return first_line
        
        return file_path.name

    @staticmethod
    def _extract_tags(file_path: Path, content: str) -> List[str]:
        """Extract tags from filename and content."""
        tags = []
        
        # From filename
        name = file_path.stem.lower()
        if "_" in name:
            tags.extend(name.split("_"))
        
        # From extension
        tags.append(file_path.suffix[1:].lower() if file_path.suffix else "file")
        
        # From classification-like keywords
        keywords = [
            "protocol", "runbook", "receipt", "finalizer", "verifier",
            "mission", "memory", "skill", "canon", "dashboard",
        ]
        for keyword in keywords:
            if keyword in name or keyword in content[:500].lower():
                tags.append(keyword)
        
        # Deduplicate and limit
        return list(set(tags))[:10]

    @staticmethod
    def _save_json(path: Path, data: Any):
        """Save data to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# Global instance
_bridge_instance = None


def get_bridge() -> InfiniteMindBridge:
    """Get or create the global bridge instance."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = InfiniteMindBridge()
    return _bridge_instance
