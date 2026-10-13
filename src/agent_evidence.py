"""Deterministic evidence and completion contracts for agent runs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from src.agent_runtime.identity import artifact_identity, artifact_version, executable_words, is_test_command, is_validation_command


def workspace_artifact_is_usable(path: Path) -> bool:
    """Reject empty files and obvious text placeholders with binary suffixes."""
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        suffix = path.suffix.casefold()
        header = path.read_bytes()[:32]
    except OSError:
        return False

    signatures = {
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".jpg": (b"\xff\xd8\xff",),
        ".jpeg": (b"\xff\xd8\xff",),
        ".gif": (b"GIF87a", b"GIF89a"),
        ".pdf": (b"%PDF-",),
        ".bmp": (b"BM",),
        ".tif": (b"II*\x00", b"MM\x00*"),
        ".tiff": (b"II*\x00", b"MM\x00*"),
        ".webm": (b"\x1aE\xdf\xa3",),
        ".wav": (b"RIFF",),
        ".docx": (b"PK\x03\x04",),
        ".xlsx": (b"PK\x03\x04",),
        ".pptx": (b"PK\x03\x04",),
    }
    if suffix in signatures:
        if not any(header.startswith(signature) for signature in signatures[suffix]):
            return False
        if suffix == ".wav" and header[8:12] != b"WAVE":
            return False
    elif suffix == ".webp":
        if not (header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
            return False
    elif suffix in {".mp4", ".mov", ".m4v"}:
        if len(header) < 12 or header[4:8] != b"ftyp":
            return False
    return True


class EvidenceKind(str, Enum):
    TOOL_RESULT = "tool_result"
    ARTIFACT_MUTATION = "artifact_mutation"
    ARTIFACT_VALIDATION = "artifact_validation"
    VERIFIER_RESULT = "verifier_result"
    MEDIA_INGRESS = "media_ingress"


class CompletionStatus(str, Enum):
    VERIFIED = "verified"
    SATISFIED = "satisfied"
    UNVERIFIED = "unverified"
    FAILED = "failed"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"
    AWAITING_USER = "awaiting_user"


@dataclass(frozen=True)
class CompletionRequirements:
    required_artifacts: tuple[str, ...] = ()
    verifier_required: bool = False
    executable_verifier_available: bool = False
    verifier_commands: tuple[str, ...] = ()
    # Host workspace used by unattended/native runs.  When supplied, a
    # successful tool event is not enough: the declared artifact must also
    # exist in this workspace at completion time.
    workspace_root: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_artifacts"] = list(self.required_artifacts)
        data["verifier_commands"] = list(self.verifier_commands)
        return data


@dataclass(frozen=True)
class EvidenceEvent:
    event_id: str
    kind: EvidenceKind
    success: bool
    authoritative: bool
    round: int | None = None
    tool: str = ""
    artifact_path: str = ""
    exit_code: int | None = None
    command_sha256: str = ""
    output_sha256: str = ""
    detail: str = ""
    action_id: str = ""
    execution_id: str = ""
    artifact_id: str = ""
    verification_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data


EXTERNAL_EFFECT_UNVERIFIED = "an external operation's resulting state was not independently verified"


@dataclass(frozen=True)
class CompletionDecision:
    status: CompletionStatus
    can_complete: bool
    reason: str
    evidence_ids: tuple[str, ...] = ()
    missing_artifacts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["evidence_ids"] = list(self.evidence_ids)
        data["missing_artifacts"] = list(self.missing_artifacts)
        return data


_ARTIFACT_PATH = r"(?:/|\./|\.\./)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9]{1,12}"
_ARTIFACT_REQUEST_RE = re.compile(
    rf"\b(?:writ(?:e|ten)|creat(?:e|ed)|make|made|sav(?:e|ed)|produc(?:e|ed)|"
    rf"generat(?:e|ed)|export(?:ed)?|edit(?:ed)?|modif(?:y|ied)|updat(?:e|ed)|"
    rf"fix(?:ed)?|put|plac(?:e|ed))\b"
    rf"[^\n]{{0,80}}?(?P<path>{_ARTIFACT_PATH})",
    re.IGNORECASE,
)
_OUTPUT_PATH_RE = re.compile(
    rf"\b(?:output|artifact)(?:\s+(?:file|path))?\b[^\n]{{0,40}}?(?P<path>{_ARTIFACT_PATH})",
    re.IGNORECASE,
)
_EXPLICIT_OUTPUT_FILE_RE = re.compile(
    rf"\b(?:to|at|as|into)\s+(?:the\s+|a\s+)?(?:single\s+)?(?:file|path)\s+(?P<path>{_ARTIFACT_PATH})",
    re.IGNORECASE,
)
_NAMED_OUTPUT_FILE_RE = re.compile(
    rf"\b(?:in|into)\s+(?:a|the)\s+file\s+(?:called|named)\s+(?P<path>{_ARTIFACT_PATH})",
    re.IGNORECASE,
)
_EXPLICIT_OUTPUT_DIRECTORY_RE = re.compile(
    r"\b(?:sav(?:e|ed)|writ(?:e|ten)|creat(?:e|ed)|make|made|produc(?:e|ed)|"
    r"generat(?:e|ed)|export(?:ed)?|put|plac(?:e|ed))\b"
    r"[^\n]{0,100}?\b(?:in|into|to|under|inside)\s+"
    r"[`'\"]?(?P<path>/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+/?)"
    r"(?=[`'\"\s.,;:]|$)",
    re.IGNORECASE,
)
_LOCALIZED_OUTPUT_DIRECTORY_RE = re.compile(
    r"(?:保存(?:到|至|入)?|创建|生成|输出(?:到|至|入)?)"
    r"[^\n]{0,80}?"
    r"[`'\"]?(?P<path>/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+/)"
    r"(?=[`'\"\s.,;:，。；：]|$)",
    re.IGNORECASE,
)
_LOCALIZED_ARTIFACT_REQUEST_RE = re.compile(
    rf"(?:保存(?:为|到)?|写入|创建|生成|输出(?:为|到)?|"
    rf"保存|書き込|作成|生成|出力|저장|작성|생성|출력)"
    rf"[^\n]{{0,80}}?(?P<path>{_ARTIFACT_PATH})",
    re.IGNORECASE,
)
_TEST_COMMAND_RE = re.compile(
    r"(?:^|[;&|\s])(?:pytest|python(?:3)?\s+-m\s+pytest|npm\s+(?:run\s+)?test|"
    r"pnpm\s+test|yarn\s+test|make\s+test|cargo\s+test|go\s+test|"
    r"/(?:tests?|verifier)/[^\s;&|]+)",
    re.IGNORECASE,
)
_MUTATION_COMMAND_RE = re.compile(
    r"(?:\b(?:write_file|edit_file|apply_patch|touch|tee|cp|mv|mkdir|ln|install)\b|"
    r"\b(?:ffmpeg|sox)\b[^\n;&|]*(?:/workspace/|\.(?:mp4|webm|mov|mkv|avi|mp3|wav|m4a|aac|flac|ogg|opus)\b)|"
    r"\bsed\s+-[A-Za-z]*i[A-Za-z]*(?:\.[^\s;&|]+)?\b|\bperl\s+-p?i(?:[A-Za-z]*)?\b|"
    r"(?:^|\s)>{1,2}\s*|"
    r"\.(?:save|savefig|write_text|write_bytes|to_csv|to_json|to_excel|to_parquet|"
    r"to_html|to_markdown|to_pickle|to_feather|mkdir|symlink_to|rename|replace|"
    r"unlink)\s*\(|"
    r"\b(?:os\.(?:makedirs|mkdir|rename|replace|remove|unlink|symlink)|"
    r"shutil\.(?:copy|copy2|copyfile|copytree|move))\s*\(|"
    r"\bopen\s*\([^\n]{0,240}?[\"'](?:w|a|x)[+b]?[\"'])",
    re.IGNORECASE,
)
_VALIDATION_COMMAND_RE = re.compile(
    r"(?:\btest\s+-[efsd]\b|\b(?:cat|head|tail|stat|wc|jq|cmp|diff)\b|"
    r"(?:^|[;&|\s])(?:coqc|gcc|g\+\+|clang|clang\+\+|javac|rustc)\b|"
    r"(?:^|[;&|\s])(?:cargo\s+(?:build|check)|go\s+build|npm\s+(?:run\s+)?build|"
    r"pnpm\s+build|yarn\s+build)\b|"
    r"\.read_(?:text|bytes)\s*\(|\bopen\s*\([^\n]{0,240}?[\"']r[+b]?[\"'])",
    re.IGNORECASE,
)


def command_is_validation(command: str) -> bool:
    """Return whether a shell command provides executable verification evidence."""
    value = str(command or "")
    return is_validation_command(_command_text(value))


def command_is_test(command: str) -> bool:
    """Return whether a shell command executes a recognized test runner."""
    return is_test_command(_command_text(str(command or "")))


def _clean_path(value: str) -> str:
    return str(value or "").strip().strip("`'\"").rstrip(".,;:)")


def _is_prose_abbreviation(value: str) -> bool:
    return _clean_path(value).lower() in {"e.g", "i.e"}


def _artifact_match_is_negated(instruction: str, match: re.Match[str]) -> bool:
    """Reject paths attached to an explicitly negated mutation verb."""

    prefix = instruction[max(0, match.start() - 32):match.start()]
    return bool(re.search(r"(?:do\s+not|don't|must\s+not|never)\s+$", prefix, re.IGNORECASE))


def _artifact_match_is_callable(instruction: str, match: re.Match[str], path: str) -> bool:
    """Reject dotted callable names such as ``json.dumps(...)`` as artifacts."""

    if "/" in path or "\\" in path:
        return False
    if instruction[match.end("path"):].startswith("("):
        return True
    # Procedural prompts often name existence helpers without parentheses,
    # e.g. "verify with os.path.exists or ls". They are code references, not
    # output filenames, even though the generic path regex sees an extension.
    return bool(re.fullmatch(r"(?:os\.path|pathlib\.Path|Path)\.[A-Za-z_]\w*", path))


def _artifact_match_is_email_host(instruction: str, match: re.Match[str]) -> bool:
    """Reject the domain portion of an email address as an output path."""

    start = match.start("path")
    prefix = instruction[max(0, start - 80):start]
    return bool(re.search(r"[A-Za-z0-9_.+-]+@$", prefix))


def _workspace_path_identity(value: str) -> str:
    """Return a stable identity for native workspace path aliases."""

    path = _clean_path(value).replace("\\", "/")
    for prefix in ("/tmp_workspace/", "/workspace/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _known_input_is_explicit_mutation_target(instruction: str, path: str) -> bool:
    """Preserve a known input only when the user explicitly asks to edit it.

    Input descriptions commonly say that a file is "saved in" or is a
    "post-write checklist".  Those phrases must not turn read-only evidence
    into a required output artifact.  Direct edit/update requests remain
    supported.
    """

    escaped = re.escape(_clean_path(path))
    active_edit = rf"(?<![-\w])(?:edit|modify|update|fix)\s+(?:the\s+)?[`'\"]?{escaped}"
    direct_create = (
        rf"(?<![-\w])(?:write|create|make|save|produce|generate|export|put|place)"
        rf"\s+[`'\"]?{escaped}"
    )
    directed_create = (
        rf"(?<![-\w])(?:write|create|make|save|produce|generate|export|put|place)"
        rf"\b[^\n]{{0,80}}?\b(?:to|into|at|as|under|inside)\s+"
        rf"(?:the\s+|a\s+)?[`'\"]?{escaped}"
    )
    return any(
        re.search(pattern, instruction, re.IGNORECASE)
        for pattern in (active_edit, direct_create, directed_create)
    )


def _unquoted_statements(text: str) -> Iterable[tuple[str, str]]:
    """Yield original statements and their reportable prose, with quotes masked.

    Mask before splitting so punctuation inside an example cannot change the
    scope of the surrounding sentence. Inline code identifiers stay visible.
    """
    def mask(match: re.Match[str]) -> str:
        value = match.group()
        # Quotation marks around an artifact identify a target, rather than
        # quote a report. Keep that target available for exact path matching.
        if value[0] in {'"', "'"} and re.fullmatch(_ARTIFACT_PATH, value[1:-1]):
            return ' ' + value[1:-1] + ' '
        return re.sub(r'[^\n]', ' ', value)

    masked = re.sub(
        r'```[\s\S]*?```|~~~[\s\S]*?~~~|"[^"\n]*"|(?<!\w)\'[^\'\n]*\'(?!\w)',
        mask, text,
    )
    start = 0
    for boundary in re.finditer(r'(?<=[.!?;])(?=\s)|(?<=\n)', masked):
        end = boundary.start()
        if end > start:
            yield text[start:end], masked[start:end].replace('`', '')
        start = end
    if start < len(text):
        yield text[start:], masked[start:].replace('`', '')


def _execution_obligation(requirements: CompletionRequirements) -> bool:
    """A derived view of the existing contract, never a separate declaration."""
    return bool(requirements.required_artifacts or requirements.verifier_required
                or requirements.executable_verifier_available or requirements.verifier_commands)


def infer_completion_requirements(
    instruction: str,
    *,
    executable_verifier_available: bool = False,
    verifier_commands: Sequence[str] = (),
    known_input_paths: Sequence[str] = (),
) -> CompletionRequirements:
    """Infer only explicitly requested output/edit paths from an instruction."""

    # Explanations can contain imperative examples. Their embedded actions
    # are not requests to execute those actions. Keep independent requests in
    # other statements, and keep explicitly supplied verifier requirements.
    explanatory_request = re.compile(
        r'^\s*(?:please\s+|(?:can|could|would)\s+you\s+)?'
        r'(?:explain|describe|summari[sz]e|teach|discuss|'
        r'show\s+(?:me\s+)?(?:an?\s+)?example|how\b)', re.I)
    text = ''.join(scoped for _, scoped in _unquoted_statements(str(instruction or ''))
                   if not explanatory_request.search(scoped))
    paths: list[str] = []
    for pattern in (
        _ARTIFACT_REQUEST_RE,
        _OUTPUT_PATH_RE,
        _EXPLICIT_OUTPUT_FILE_RE,
        _NAMED_OUTPUT_FILE_RE,
        _LOCALIZED_ARTIFACT_REQUEST_RE,
        _EXPLICIT_OUTPUT_DIRECTORY_RE,
        _LOCALIZED_OUTPUT_DIRECTORY_RE,
    ):
        for match in pattern.finditer(text):
            path = _clean_path(match.group("path"))
            if _artifact_match_is_negated(text, match):
                continue
            if _artifact_match_is_callable(text, match, path):
                continue
            if _artifact_match_is_email_host(text, match):
                continue
            if path and not _is_prose_abbreviation(path) and path not in paths:
                paths.append(path)
    paths = [path.rstrip("/") if path != "/" else path for path in paths]
    paths = list(dict.fromkeys(paths))
    input_identities = {
        _workspace_path_identity(path)
        for path in known_input_paths
        if _clean_path(path)
    }
    if input_identities:
        paths = [
            path
            for path in paths
            if _workspace_path_identity(path) not in input_identities
            or _known_input_is_explicit_mutation_target(text, path)
        ]
    # When the instruction names an absolute output directory and then gives
    # relative example filenames (for example ``1.tex, 2.tex, ...``), the
    # directory is the actual completion contract.  Treating the first example
    # filename as a root-level required artifact causes false blocked runs and
    # can provoke destructive repair calls outside the output directory.
    explicit_directories = [
        path
        for path in paths
        if path.startswith("/") and not Path(path).suffix
    ]
    if explicit_directories:
        paths = [
            path
            for path in paths
            if path in explicit_directories
            or any(path.startswith(directory.rstrip("/") + "/") for directory in explicit_directories)
        ]
        explicit_files = [path for path in paths if Path(path).suffix]
        if explicit_files:
            paths = [
                path for path in paths
                if path not in explicit_directories
                or not any(file.startswith(path.rstrip("/") + "/") for file in explicit_files)
            ]
    cleaned_verifier_commands = tuple(dict.fromkeys(
        str(command or "").strip()
        for command in verifier_commands
        if str(command or "").strip()
    ))
    explicit_test_request = re.search(
        r'(?:^|[.;\n]|\b(?:and|then))\s*'
        r'(?:please\s+|(?:can|could|would)\s+you\s+)?'
        r'(?:run|execute)\s+(?:(?:the|all|a|full)\s+)*'
        r'(?:tests?\b|test\s+suite\b|pytest\b|unittest\b|npm\s+test\b)', text, re.I)
    verifier_required = executable_verifier_available or bool(cleaned_verifier_commands) or bool(
        explicit_test_request or (paths and re.search(
            r"\b(?:then|after(?:wards)?|and)\b[^\n]{0,100}\b(?:test|verify|check|validate)\b",
            text,
            re.IGNORECASE,
        ))
    )
    return CompletionRequirements(
        required_artifacts=tuple(paths),
        verifier_required=verifier_required,
        executable_verifier_available=(
            executable_verifier_available or bool(cleaned_verifier_commands) or bool(explicit_test_request)
        ),
        verifier_commands=cleaned_verifier_commands,
    )


def requirements_from_runtime_context(
    context: Mapping[str, Any] | None,
    *,
    instruction: str = "",
) -> CompletionRequirements:
    runtime_context = context or {}
    known_inputs: list[str] = []
    for value in runtime_context.get("input_files") or ():
        path = _clean_path(str(value or ""))
        if path:
            known_inputs.append(path)
    media_ingress = runtime_context.get("media_ingress")
    if isinstance(media_ingress, Mapping):
        for artifact in media_ingress.get("artifacts") or ():
            if not isinstance(artifact, Mapping):
                continue
            path = _clean_path(str(artifact.get("source_path") or ""))
            if path:
                known_inputs.append(path)

    raw = runtime_context.get("completion_requirements")
    if not isinstance(raw, Mapping):
        return infer_completion_requirements(
            instruction,
            known_input_paths=known_inputs,
        )
    paths = raw.get("required_artifacts")
    if not isinstance(paths, (list, tuple)):
        paths = ()
    cleaned = tuple(
        path
        for value in paths
        if (path := _clean_path(str(value or "")))
    )
    input_identities = {
        _workspace_path_identity(path) for path in known_inputs
    }
    if input_identities:
        cleaned = tuple(
            path
            for path in cleaned
            if _workspace_path_identity(path) not in input_identities
            or _known_input_is_explicit_mutation_target(instruction, path)
        )
    verifier_commands = raw.get("verifier_commands")
    if not isinstance(verifier_commands, (list, tuple)):
        verifier_commands = ()
    cleaned_verifier_commands = tuple(dict.fromkeys(
        str(command or "").strip()
        for command in verifier_commands
        if str(command or "").strip()
    ))
    return CompletionRequirements(
        required_artifacts=cleaned,
        verifier_required=bool(raw.get("verifier_required")),
        executable_verifier_available=(
            bool(raw.get("executable_verifier_available"))
            or bool(cleaned_verifier_commands)
        ),
        verifier_commands=cleaned_verifier_commands,
        workspace_root=_clean_path(str(raw.get("workspace_root") or "")),
    )


def _digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def _path_is_mentioned(command: str, required_path: str) -> bool:
    command = str(command or "")
    path = _clean_path(required_path)
    if not path:
        return False
    return path in command or Path(path).name in command


def _artifact_path_matches_required(artifact_path: str, required_path: str, workspace: str = "") -> bool:
    artifact = str(artifact_path or '').strip()
    required = _clean_path(required_path)
    if not artifact or not required:
        return False
    return artifact_identity(artifact, workspace) == artifact_identity(required, workspace)


def _explicit_tool_paths(tool: str, command: str) -> list[str]:
    if tool == "write_file":
        try:
            args = json.loads(command or "{}")
        except (TypeError, json.JSONDecodeError):
            args = None
        if isinstance(args, Mapping):
            path = str(args.get("path") or "").strip()
            return [path] if path else []
        # Keep compatibility with the legacy ``path\ncontent`` transport.
        path = (str(command or "").splitlines()[0] if command else "").strip()
        return [path] if path else []
    if tool == "edit_file":
        try:
            args = json.loads(command or "{}")
        except (TypeError, json.JSONDecodeError):
            return []
        path = str(args.get("path") or "").strip() if isinstance(args, dict) else ""
        return [path] if path else []
    if tool == "apply_patch":
        try:
            args = json.loads(command or "{}")
        except (TypeError, json.JSONDecodeError):
            args = None
        if isinstance(args, Mapping):
            patch = args.get("patch")
            command = patch if isinstance(patch, str) else ""
        return [
            match.group(1).strip()
            for match in re.finditer(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+)$", command or "", re.MULTILINE)
            if _clean_path(match.group(1))
        ]
    if tool == "inspect_media":
        try:
            args = json.loads(command or "{}")
        except (TypeError, json.JSONDecodeError):
            return []
        path = (
            _clean_path(str(args.get("output_path") or ""))
            if isinstance(args, dict)
            else ""
        )
        paths = [path] if path else []
        if isinstance(args, dict) and isinstance(args.get("exports"), list):
            for item in args["exports"]:
                if not isinstance(item, dict):
                    continue
                export_path = _clean_path(str(item.get("output_path") or ""))
                if export_path and export_path not in paths:
                    paths.append(export_path)
        return paths
    if tool == "private_browser":
        try:
            args = json.loads(command or "{}")
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(args, Mapping):
            return []
        action = str(args.get("action") or "").strip().lower()
        if action == "screenshot":
            path = _clean_path(str(args.get("path") or ""))
            return [path] if path else []
        if action != "batch" or not isinstance(args.get("commands"), list):
            return []
        paths: list[str] = []
        for item in args["commands"]:
            if isinstance(item, Mapping):
                item_action = str(item.get("action") or "").strip().lower()
                item_path = item.get("path")
            elif isinstance(item, (list, tuple)) and item:
                item_action = str(item[0] or "").strip().lower()
                item_path = item[1] if len(item) > 1 else ""
            else:
                continue
            if item_action != "screenshot":
                continue
            path = _clean_path(str(item_path or ""))
            if path and path not in paths:
                paths.append(path)
        return paths
    return []


def _command_text(value: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("{"):
        return text
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text
    if not isinstance(payload, Mapping):
        return text
    for key in ("command", "cmd", "shell"):
        command = payload.get(key)
        if isinstance(command, str) and command.strip():
            return command.strip()
    return text


def _matches_declared_verifier(command: str, expected: Sequence[str]) -> bool:
    actual = executable_words(_command_text(command))
    if not actual:
        return False
    return any(
        normalized == actual
        for item in expected
        if (normalized := executable_words(str(item or "")))
    )


def command_has_mutation_effect(command: str) -> bool:
    """Return whether a shell or Python command visibly mutates workspace state."""

    return bool(_MUTATION_COMMAND_RE.search(_command_text(command)))


def _event_id(payload: Mapping[str, Any], occurrence: int) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "ev-" + _digest(f"{occurrence}:{canonical}")[:16]


class EvidenceLedger:
    def __init__(self, requirements: CompletionRequirements | None = None) -> None:
        self.requirements = requirements or CompletionRequirements()
        self.events: list[EvidenceEvent] = []
        self._verification_versions: dict[str, str] = {}
        self._verification_versions_captured = False
        # Retain receipt command identity privately for presentation matching;
        # model prose and client dictionaries never populate this evidence.
        self._verifier_commands: dict[str, tuple[str, ...]] = {}
        # Wave 4 effect assessments from the run's journal, plus the journal
        # order of actions so receipt evidence and effects share one ordering.
        self.effects: list[dict[str, Any]] = []
        self._action_order: dict[str, int] = {}

    def record_effects(self, entries: Iterable[Mapping[str, Any]], action_order: Mapping[str, int],
                       partial_reads: Iterable[str] = ()) -> None:
        """Consume server-derived effect assessments (never model/client data).

        ``partial_reads`` names read actions whose admitted observation was
        partial (offset/limit, truncation or extraction): such a read cannot
        validate omitted content, so its validation event is not authoritative.
        """
        from dataclasses import replace
        from src.agent_runtime.effects import EffectAssessment
        self.effects = [dict(entry) for entry in entries
                        if isinstance(entry, Mapping) and isinstance(entry.get("assessment"), EffectAssessment)]
        self._action_order = {str(k): v for k, v in action_order.items() if type(v) is int}
        partial = set(partial_reads)
        self.events = [replace(event, authoritative=False, detail="partial read; omitted content is unvalidated")
                       if event.kind == EvidenceKind.ARTIFACT_VALIDATION and event.tool == "read_file"
                       and event.action_id in partial else event for event in self.events]

    def _last_success_ordinal(self, required: str) -> int:
        return max((self._action_order.get(event.action_id, 0) for event in self.events
                    if event.kind == EvidenceKind.ARTIFACT_MUTATION and event.authoritative and event.success
                    and _artifact_path_matches_required(event.artifact_path, required, self.requirements.workspace_root)),
                   default=0)

    def _later_effects(self, required: str) -> list[tuple[dict[str, Any], bool]]:
        """Effects after the artifact's last successful mutation, with targeting."""
        floor = self._last_success_ordinal(required)
        later = []
        for entry in self.effects:
            ordinal = entry.get("ordinal")
            if type(ordinal) is not int or ordinal <= floor:
                continue
            explicit = any(_artifact_path_matches_required(path, required, self.requirements.workspace_root)
                           for path in entry.get("paths") or ())
            if explicit or entry.get("unknown_scope"):
                later.append((entry, explicit))
        return later

    @staticmethod
    def _entry_unsettled(entry: Mapping[str, Any], explicit: bool) -> bool:
        """One effect may have changed state with no settled evidence.

        Explicit targets are unsettled by unknown/timed-out/cancelled outcomes
        and by failures after the producer reached its mutation stage (atomic
        refusals keep the earlier artifact). Unknown-scope effects are
        unsettled when nothing captured their settlement: cancellation,
        interruption, or failed process teardown; settled shell/Python changes
        are already tracked through artifact version capture.
        """
        from src.agent_runtime.effects import CleanupState, ExecutionOutcome
        unknown = {ExecutionOutcome.ATTEMPTED, ExecutionOutcome.INTERRUPTED, ExecutionOutcome.CANCELLED,
                   ExecutionOutcome.RUNNING}
        assessment = entry["assessment"]
        if not assessment.unresolved_impact:
            return False
        if assessment.execution in unknown:
            return True
        if explicit:
            return (assessment.execution is ExecutionOutcome.TIMED_OUT
                    or (assessment.execution is ExecutionOutcome.FAILED and bool(entry.get("mutation_attempted"))))
        return assessment.cleanup is CleanupState.FAILED

    def _effect_unsettled(self, required: str) -> bool:
        """A later operation may have partially changed this artifact."""
        return any(self._entry_unsettled(entry, explicit) for entry, explicit in self._later_effects(required))

    def _current_effects(self) -> list[dict[str, Any]]:
        """Effects of this journal's own actions, in action order."""
        return sorted((entry for entry in self.effects if type(entry.get("ordinal")) is int),
                      key=lambda entry: entry["ordinal"])

    def _contradicted_target(self) -> str:
        """A changed file whose latest effect a fresh readback contradicts.

        Only the latest effect per target counts: an earlier effect superseded
        by a later requested write is history, not a contradiction.
        """
        from src.agent_runtime.effects import EffectVerdict
        latest: dict[str, dict[str, Any]] = {}
        for entry in self._current_effects():
            for path in entry.get("paths") or ():
                latest[path] = entry
        return next((path for path, entry in latest.items()
                     if entry["assessment"].verdict is EffectVerdict.CONTRADICTED), "")

    def unverified_external_effects(self) -> list[dict[str, Any]]:
        """Executed external effects whose resulting state is not verified.

        A remote acknowledgement is execution evidence only. Without an
        admitted independent readback these effects never support a
        definitive statement that the external state changed.
        """
        from src.agent_runtime.effects import EffectVerdict
        return [entry for entry in self.effects if entry.get("external")
                and entry["assessment"].verdict not in {EffectVerdict.VERIFIED, EffectVerdict.NOT_EXECUTED}]

    def effect_disclosures(self) -> tuple[str, ...]:
        """Server-authored facts for unverified external effects."""
        from src.agent_runtime.effects import ExecutionOutcome
        facts = []
        for entry in self.unverified_external_effects():
            tool = str(entry.get("tool") or "external operation")
            execution = entry["assessment"].execution
            if execution is ExecutionOutcome.REPORTED_SUCCESS:
                facts.append(f"External operation {tool} reported success; any external change it made was "
                             "not independently verified.")
            elif execution is ExecutionOutcome.FAILED:
                facts.append(f"External operation {tool} reported failure; it may have partially taken effect.")
            else:
                facts.append(f"External operation {tool} has an unknown outcome; it may or may not have "
                             "taken effect.")
        return tuple(dict.fromkeys(facts))

    def _effect_contradicted(self, required: str) -> str:
        """The latest effect targeting the artifact, if fresh readback contradicts it."""
        from src.agent_runtime.effects import EffectVerdict
        targeting = [entry for entry in self.effects if type(entry.get("ordinal")) is int
                     and any(_artifact_path_matches_required(path, required, self.requirements.workspace_root)
                             for path in entry.get("paths") or ())]
        if not targeting:
            return ""
        latest = max(targeting, key=lambda entry: entry["ordinal"])["assessment"]
        return latest.effect_id if latest.verdict is EffectVerdict.CONTRADICTED else ""

    @classmethod
    def from_tool_events(
        cls,
        tool_events: Iterable[Mapping[str, Any]],
        requirements: CompletionRequirements | None = None,
    ) -> "EvidenceLedger":
        ledger = cls(requirements)
        for event in tool_events or []:
            if isinstance(event, Mapping):
                ledger.record_tool_event(event)
        return ledger

    def _append(
        self,
        *,
        kind: EvidenceKind,
        success: bool,
        authoritative: bool,
        source: Mapping[str, Any],
        artifact_path: str = "",
        detail: str = "",
    ) -> EvidenceEvent:
        command = str(source.get("command") or "")
        output = str(source.get("output") or source.get("error") or "")
        exit_code = source.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            exit_code = None
        payload = {
            "kind": kind.value,
            "round": source.get("round"),
            "tool": source.get("tool"),
            "artifact_path": artifact_path,
            "exit_code": exit_code,
            "command_sha256": _digest(command),
            "output_sha256": _digest(output),
        }
        action_id = str(source.get('action_id') or '')
        execution_id = str(source.get('execution_id') or '')
        if action_id:
            payload.update(action_id=action_id, execution_id=execution_id)
        evidence = EvidenceEvent(
            event_id=_event_id(payload, len(self.events)),
            kind=kind,
            success=success,
            authoritative=authoritative,
            round=int(source["round"]) if isinstance(source.get("round"), int) else None,
            tool=str(source.get("tool") or ""),
            artifact_path=artifact_path,
            exit_code=exit_code,
            command_sha256=payload["command_sha256"],
            output_sha256=payload["output_sha256"],
            detail=detail,
            action_id=action_id,
            execution_id=execution_id,
            artifact_id=artifact_identity(artifact_path, self.requirements.workspace_root) if artifact_path else '',
            verification_id=('verification-' + _event_id(payload, len(self.events)))
            if kind in {EvidenceKind.VERIFIER_RESULT, EvidenceKind.ARTIFACT_VALIDATION} else '',
        )
        self.events.append(evidence)
        return evidence

    def record_tool_event(self, event: Mapping[str, Any]) -> None:
        tool = str(event.get("tool") or "")
        command = str(event.get("command") or "")
        exit_code = event.get("exit_code")
        authoritative = (
            isinstance(exit_code, int) and not isinstance(exit_code, bool)
            and not event.get("blocked") and not event.get("approval_required")
            and event.get("execution_attempted") is not False
        )
        success = authoritative and exit_code == 0 and not event.get('error')
        if not authoritative:
            success = not bool(event.get("error"))
        self._append(
            kind=EvidenceKind.TOOL_RESULT,
            success=success,
            authoritative=authoritative,
            source=event,
        )

        explicit_paths = _explicit_tool_paths(tool, command)
        mutation_paths = list(explicit_paths)
        observed_changes = event.get('artifact_changes')
        if isinstance(observed_changes, list) and tool in {'bash', 'python', 'host_shell'}:
            mutation_paths.extend(path for path in self.requirements.required_artifacts
                                  if artifact_identity(path, self.requirements.workspace_root) in observed_changes)
        elif command_has_mutation_effect(command) and tool not in {
            "write_file",
            "edit_file",
            "apply_patch",
            "inspect_media",
        }:
            mutation_paths.extend(
                path
                for path in self.requirements.required_artifacts
                if _path_is_mentioned(command, path)
            )
        seen_paths: set[str] = set()
        for path in mutation_paths:
            path = str(path or '').strip()
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            self._append(
                kind=EvidenceKind.ARTIFACT_MUTATION,
                success=success,
                authoritative=authoritative,
                source=event,
                artifact_path=path,
            )

        if tool == "read_file":
            try:
                read_args = json.loads(command or "{}")
            except (TypeError, json.JSONDecodeError):
                read_args = None
            read_path = (
                str(read_args.get("path") or "").strip()
                if isinstance(read_args, Mapping)
                else command.strip() if read_args is None else ""
            )
            if read_path and any(
                _artifact_path_matches_required(read_path, required, self.requirements.workspace_root)
                for required in self.requirements.required_artifacts
            ):
                self._append(
                    kind=EvidenceKind.ARTIFACT_VALIDATION,
                    success=success,
                    authoritative=authoritative,
                    source=event,
                    artifact_path=read_path,
                    detail="post-write artifact inspection",
                )

        if tool in {"bash", "host_shell"} and (command_is_test(_command_text(command)) or _matches_declared_verifier(
            command,
            self.requirements.verifier_commands,
        )):
            if authoritative:
                versions = event.get('artifact_versions')
                self._verification_versions = dict(versions) if isinstance(versions, Mapping) else {}
                self._verification_versions_captured = isinstance(versions, Mapping)
            verifier = self._append(
                kind=EvidenceKind.VERIFIER_RESULT,
                success=success,
                authoritative=authoritative,
                source=event,
                detail="executable test/verifier command",
            )
            self._verifier_commands[verifier.event_id] = executable_words(_command_text(command))
        elif tool in {"bash", "host_shell"} and is_validation_command(command) and not mutation_paths:
            for path in self.requirements.required_artifacts:
                if _path_is_mentioned(command, path):
                    self._append(
                        kind=EvidenceKind.ARTIFACT_VALIDATION,
                        success=success,
                        authoritative=authoritative,
                        source=event,
                        artifact_path=path,
                    )

    def _supports_verifier_claim(self, identities: Sequence[str] = (), paths: Sequence[str] = ()) -> bool:
        """Only the current passing verifier may support its named runner.

        A test result stays a test result when an unrelated external effect
        keeps the whole run unverified; it never speaks for that effect.
        """
        if self._evaluate_obligations().status != CompletionStatus.VERIFIED:
            return False
        latest = next((event for event in reversed(self.events)
                       if event.kind == EvidenceKind.VERIFIER_RESULT and event.authoritative), None)
        if latest is None or not latest.success:
            return False
        words = self._verifier_commands.get(latest.event_id, ())
        names = {Path(words[0]).name} if words else set()
        if words and re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', Path(words[0]).name) and '-m' in words:
            module_index = words.index('-m') + 1
            if module_index < len(words):
                names.add(words[module_index])
        return (all(identity in names for identity in identities)
                and all(any(_artifact_path_matches_required(word, path, self.requirements.workspace_root)
                            for word in words) for path in paths))

    def _supports_artifact_claim(self, kind: EvidenceKind, paths: Sequence[str]) -> bool:
        """Match every claimed artifact by identity, never by basename."""
        targets = tuple(paths) or self.requirements.required_artifacts
        if not targets or (not paths and len(targets) != 1):
            return False
        if not paths and self.unverified_external_effects():
            # An unnamed "I updated it" may mean the external effect.
            return False
        for path in targets:
            matching = [event for event in self.events if event.kind == kind and event.authoritative
                        and _artifact_path_matches_required(event.artifact_path, path, self.requirements.workspace_root)]
            successful = [event for event in matching if event.success]
            # Match evaluate(): atomic helper failures preserve the previous
            # successful artifact; a partial shell/Python failure may not.
            destructive_failure = bool(matching and not matching[-1].success
                                       and matching[-1].tool in {'bash', 'python'})
            if not successful or destructive_failure:
                return False
            if self.effects and (self._effect_unsettled(path) or self._effect_contradicted(path)):
                return False
        return True

    def record_media_ingress(self, metadata: Mapping[str, Any]) -> None:
        for artifact in metadata.get("artifacts") or []:
            if not isinstance(artifact, Mapping):
                continue
            source = str(artifact.get("source_path") or "")
            payload = {
                "round": 0,
                "tool": "media_ingress",
                "command": source,
                "output": str(artifact.get("source_sha256") or ""),
                "exit_code": 0,
            }
            self._append(
                kind=EvidenceKind.MEDIA_INGRESS,
                success=True,
                authoritative=True,
                source=payload,
                artifact_path=source,
                detail=str(artifact.get("modality") or "media"),
            )

    def evaluate(
        self,
        *,
        exhausted: bool = False,
        awaiting_user: bool = False,
    ) -> CompletionDecision:
        decision = self._evaluate_obligations(exhausted=exhausted, awaiting_user=awaiting_user)
        if decision.status in {CompletionStatus.VERIFIED, CompletionStatus.SATISFIED} and \
                self.unverified_external_effects():
            # Reported external execution is not a verified effect: the run
            # may end, but never as verified or satisfied.
            return CompletionDecision(CompletionStatus.UNVERIFIED, True, EXTERNAL_EFFECT_UNVERIFIED,
                                      decision.evidence_ids, decision.missing_artifacts)
        return decision

    def _evaluate_obligations(
        self,
        *,
        exhausted: bool = False,
        awaiting_user: bool = False,
    ) -> CompletionDecision:
        if awaiting_user:
            return CompletionDecision(
                CompletionStatus.AWAITING_USER,
                False,
                "the run is waiting for user input",
            )
        if exhausted:
            return CompletionDecision(
                CompletionStatus.EXHAUSTED,
                False,
                "the run exhausted its model-round budget",
            )

        verifier_events = [
            event for event in self.events
            if event.kind == EvidenceKind.VERIFIER_RESULT and event.authoritative
        ]
        latest_verifier = verifier_events[-1] if verifier_events else None
        if latest_verifier is not None and not latest_verifier.success:
            return CompletionDecision(
                CompletionStatus.FAILED,
                False,
                "the latest executable verifier failed",
                (latest_verifier.event_id,),
            )

        if latest_verifier and self.requirements.workspace_root:
            for path in self.requirements.required_artifacts:
                identity = artifact_identity(path, self.requirements.workspace_root)
                expected = self._verification_versions.get(identity)
                if expected in {'unobserved', 'missing-or-unreadable'} or (
                    expected is None and self._verification_versions_captured
                ):
                    return CompletionDecision(CompletionStatus.BLOCKED, False,
                                              'artifact version could not be established for verification',
                                              (latest_verifier.event_id,))
                if expected is not None and expected != artifact_version(path, self.requirements.workspace_root):
                    return CompletionDecision(CompletionStatus.BLOCKED, False,
                                              'artifact content changed after verification',
                                              (latest_verifier.event_id,))

        for required in self.requirements.required_artifacts if self.effects else ():
            contradicted = self._effect_contradicted(required)
            if contradicted:
                return CompletionDecision(CompletionStatus.FAILED, False,
                                          "fresh readback contradicts the requested artifact content",
                                          (), (required,))

        if self.effects:
            # Effect obligations hold whether or not artifacts were declared.
            contradicted = self._contradicted_target()
            if contradicted:
                return CompletionDecision(CompletionStatus.FAILED, False,
                                          "fresh readback contradicts the requested state of a changed file",
                                          (), (contradicted,))
            if latest_verifier is not None:
                floor = self._action_order.get(latest_verifier.action_id, 0)
                if any(entry["ordinal"] > floor and self._entry_unsettled(entry, bool(entry.get("paths")))
                       for entry in self._current_effects()):
                    return CompletionDecision(
                        CompletionStatus.BLOCKED, False,
                        "a later operation may have changed state after the latest executable verifier",
                        (latest_verifier.event_id,))

        satisfied_ids: list[str] = []
        missing: list[str] = []
        unsettled: list[str] = []
        workspace_root = str(self.requirements.workspace_root or "").strip()
        for required in self.requirements.required_artifacts:
            matches = [
                event for event in self.events
                if event.kind == EvidenceKind.ARTIFACT_MUTATION
                and _artifact_path_matches_required(event.artifact_path, required, self.requirements.workspace_root)
            ]
            authoritative = [
                event for event in matches
                if event.authoritative
            ]
            latest = authoritative[-1] if authoritative else None
            successful = [event for event in authoritative if event.success]
            latest_success = successful[-1] if successful else None
            # Failed shell/Python mutations may have already truncated or
            # partially overwritten a file before returning non-zero. Atomic
            # helper failures (write_file/edit_file/apply_patch) preserve the
            # last successful artifact and therefore do not erase its evidence.
            destructive_failure = bool(
                latest is not None
                and not latest.success
                and latest.tool in {"bash", "python"}
            )
            filesystem_missing = False
            if latest_success is not None and workspace_root:
                try:
                    root = Path(workspace_root).resolve()
                    identity = artifact_identity(required, workspace_root)
                    candidate = (root / identity.removeprefix('workspace:')).resolve() if identity.startswith('workspace:') else Path(required).resolve()
                    candidate.relative_to(root)
                    filesystem_missing = not workspace_artifact_is_usable(candidate)
                except (OSError, RuntimeError, ValueError):
                    filesystem_missing = True
            if latest_success is None or destructive_failure or filesystem_missing:
                missing.append(required)
            elif self.effects and self._effect_unsettled(required):
                # Earlier success is historical; a later possible change to
                # this artifact has no settled evidence.
                unsettled.append(required)
            else:
                satisfied_ids.append(latest_success.event_id)
        if missing or unsettled:
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                False,
                "required artifacts lack successful mutation evidence" if missing else
                "a later operation may have changed a required artifact without settled evidence",
                tuple(satisfied_ids),
                tuple([*missing, *unsettled]),
            )

        latest_mutation_index = max(
            (
                index
                for index, event in enumerate(self.events)
                if event.kind == EvidenceKind.ARTIFACT_MUTATION
                and event.authoritative
                and event.success
            ),
            default=-1,
        )
        latest_verifier_index = (
            max(
                index
                for index, event in enumerate(self.events)
                if event is latest_verifier
            )
            if latest_verifier is not None
            else -1
        )
        if (
            latest_verifier is not None
            and latest_mutation_index > latest_verifier_index
        ):
            return CompletionDecision(
                CompletionStatus.BLOCKED,
                False,
                "the latest executable verifier predates the latest artifact mutation",
                tuple(satisfied_ids),
            )

        current_validation_ids: list[str] = []
        for required in self.requirements.required_artifacts:
            matching_mutation_indices = [
                index
                for index, event in enumerate(self.events)
                if event.kind == EvidenceKind.ARTIFACT_MUTATION
                and event.authoritative
                and event.success
                and _artifact_path_matches_required(event.artifact_path, required, self.requirements.workspace_root)
            ]
            matching_validations = [
                (index, event)
                for index, event in enumerate(self.events)
                if event.kind == EvidenceKind.ARTIFACT_VALIDATION
                and event.authoritative
                and _artifact_path_matches_required(event.artifact_path, required, self.requirements.workspace_root)
            ]
            if not matching_validations:
                continue
            latest_validation_index, latest_validation = matching_validations[-1]
            latest_artifact_mutation_index = max(matching_mutation_indices, default=-1)
            if latest_validation_index < latest_artifact_mutation_index:
                # A pre-edit inspection cannot invalidate executable checks
                # that passed against the later mutation. It still cannot
                # stand in for current verification when no such check exists.
                if latest_verifier_index > latest_artifact_mutation_index:
                    continue
                return CompletionDecision(
                    CompletionStatus.BLOCKED,
                    False,
                    "the latest artifact validation predates the latest artifact mutation",
                    tuple(satisfied_ids),
                )
            if not latest_validation.success:
                return CompletionDecision(
                    CompletionStatus.FAILED,
                    False,
                    "the latest artifact validation failed",
                    tuple([*satisfied_ids, latest_validation.event_id]),
                )
            current_validation_ids.append(latest_validation.event_id)

        if self.requirements.verifier_required and latest_verifier is None:
            if self.requirements.executable_verifier_available:
                return CompletionDecision(CompletionStatus.BLOCKED, False,
                                          'the request requires an executable verifier result',
                                          tuple(satisfied_ids))
            validation_ids: list[str] = []
            for required in self.requirements.required_artifacts:
                matching_validation = [
                    (index, event)
                    for index, event in enumerate(self.events)
                    if event.kind == EvidenceKind.ARTIFACT_VALIDATION
                    and event.authoritative
                    and event.success
                    and _artifact_path_matches_required(event.artifact_path, required, self.requirements.workspace_root)
                ]
                latest_validation = matching_validation[-1] if matching_validation else None
                if latest_validation is None or latest_validation[0] < latest_mutation_index:
                    return CompletionDecision(
                        CompletionStatus.BLOCKED,
                        False,
                        "the request requires verification but no current artifact validation exists",
                        tuple(satisfied_ids),
                    )
                validation_ids.append(latest_validation[1].event_id)
            if not validation_ids:
                return CompletionDecision(
                    CompletionStatus.BLOCKED,
                    False,
                    "the request requires verification but no executable verifier result exists",
                    tuple(satisfied_ids),
                )
            return CompletionDecision(
                CompletionStatus.SATISFIED,
                True,
                "all declared artifacts have successful mutation and validation evidence",
                tuple([*satisfied_ids, *validation_ids]),
            )
        if latest_verifier is not None:
            return CompletionDecision(
                CompletionStatus.VERIFIED,
                True,
                "the latest executable verifier passed",
                tuple([*satisfied_ids, latest_verifier.event_id]),
            )
        if self.requirements.required_artifacts:
            return CompletionDecision(
                CompletionStatus.SATISFIED,
                True,
                (
                    "all declared artifacts have successful mutation and validation evidence"
                    if current_validation_ids
                    else "all declared artifacts have successful execution evidence; no executable verifier was reported"
                ),
                tuple([*satisfied_ids, *current_validation_ids]),
            )
        successful = [event.event_id for event in self.events if event.success and event.authoritative]
        return CompletionDecision(
            CompletionStatus.UNVERIFIED,
            True,
            "no declared artifact or executable verifier was available",
            tuple(successful[-3:]),
        )

    def to_list(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]
