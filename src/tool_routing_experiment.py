"""Request-scoped comparisons; permission filtering precedes selection."""
import json
import re
from contextvars import ContextVar
from dataclasses import replace

from src.turn_contract import FAMILY_TOOLS, canonical_tool, recently_executed_families, _damerau_distance

MODES = frozenset({'baseline', 'recent', 'all'})
FIXTURE_MODES = frozenset({'recent_no_family_gate', 'recent_fixture_only'})
MODEL_CHOICE_MODE = 'recent_model_choice'
MODEL_CHOICE_MODEL = 'odysseus-qwen3.5-tools-pre-heretic'

# Scheme-less public hostnames are web references too. Boundaries avoid
# treating email addresses or local/path/to/file.ext as standalone websites.
WEB_REFERENCE = re.compile(
    r'https?://[^\s<>]+'
    r'|(?<![\w@./-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
    r'(?!(?:txt|md|json|csv|tsv|ya?ml|xml|log|pdf|docx?|xlsx?|pptx?|'
    r'png|jpe?g|gif|webp|svg|py|js|ts|css|html?)(?![a-z]))'
    r'[a-z]{2,63}(?![\w@.-])', re.I,
)

# Per-execution test boundary, never a global permission change.
note_fixture_scope = ContextVar('note_fixture_scope', default=None)


def has_research_hint(text):
    """Availability hint only: a mention never dispatches a research job."""
    return any(
        token in {'research', 'researching', 'researcher', 'researchers', 'deepresearch'}
        or (7 <= len(token) <= 9 and _damerau_distance(token, 'research') <= 1)
        for token in re.findall(r'[a-z]+', str(text or '').casefold())
    )


def experiment_mode(value, owner, model=None):
    # Account-scoped rollout. Missing header means the user's normal UI, not
    # fixture mode. Explicit baseline still permits a clean comparison.
    if owner == 'pewds' and model == MODEL_CHOICE_MODEL:
        if value is None or value == MODEL_CHOICE_MODE:
            return MODEL_CHOICE_MODE
    if (owner == 'sft_alex_creator' and model == MODEL_CHOICE_MODEL
            and value == MODEL_CHOICE_MODE):
        # Explicit parity tests use the same contract, never this account's
        # default. Backend ownership and all tool toggles still apply.
        return MODEL_CHOICE_MODE
    if value in FIXTURE_MODES and owner == 'sft_alex_creator':
        return value
    # Explicit test override only. No global or session default is changed.
    if owner not in {'sft_alex_creator', 'pewds'}:
        return 'baseline'
    return value if value in MODES else 'baseline'


def select_experiment_inventory(inventory, routed, history, mode, *, user_text='', browser_requested=False):
    if mode not in ({'recent', 'all', MODEL_CHOICE_MODE} | FIXTURE_MODES):
        return routed
    families = set(routed.capabilities) - {'unknown'}
    # A supplied HTTP(S) resource is structural evidence, independent of the
    # spelling/wording of the requested action. Offer both page and video
    # readers; the model selects the correct one. Permissions still precede
    # selection, and a URL never grants write or arbitrary-network authority.
    # Preserve the route's already-resolved browser request as well. A second
    # lexical classifier must not veto navigation merely for lacking https://.
    url_family = {'search_browser'} if (
        mode == MODEL_CHOICE_MODE
        and (browser_requested or WEB_REFERENCE.search(user_text))
    ) else set()
    families.update(url_family)
    research_family = {'research'} if mode == MODEL_CHOICE_MODE and has_research_hint(user_text) else set()
    families.update(research_family)
    explicit_image_edit = (
        mode == MODEL_CHOICE_MODE
        and routed.capabilities == frozenset({'image_editing'})
        and routed.required_read_operation is None
        and any(canonical_tool(name) == 'edit_image' for name in routed.required)
    )
    if not explicit_image_edit and not (
        mode == MODEL_CHOICE_MODE and families
        and routed.required_read_operation is None
    ):
        # A concrete current-turn route owns the inventory. Reintroducing
        # previously used families here lets an explicit domain switch retain
        # stale authority and lets a resolved follow-up drift into shell.
        families.update(recently_executed_families(
            history, user_turns=6, maximum=3,
            include_failed_attempts=mode == MODEL_CHOICE_MODE,
        ))
    names = set().union(*(FAMILY_TOOLS.get(f, ()) for f in families))
    offered = frozenset(n for n in inventory.offered
                        if mode == 'all' or canonical_tool(n) in names)
    # The model-choice rollout was intentionally launched without lexical
    # tool forcing so we could observe the fine-tuned model's own selection.
    # Replays now show a narrower failure boundary: the model sometimes
    # ignores an already-resolved, read-only list/search/repeat operation and
    # fabricates or emits an empty lead-in. Preserve only the router's sealed
    # safe-read operation in this model-specific mode. Ambiguous requests still
    # have no operation and remain model-selected; mutation authority is
    # unchanged.
    sealed_read = (
        routed.required_read_operation if mode == MODEL_CHOICE_MODE else None
    )
    sealed_read_required = frozenset(
        name for name in offered
        if sealed_read is not None
        and canonical_tool(name) == canonical_tool(sealed_read.tool)
    )
    if sealed_read is not None and not sealed_read_required:
        # Never retain an operation whose own tool was removed by permissions.
        # A different required action cannot satisfy this invariant.
        sealed_read = None
    sealed_required = sealed_read_required
    explicit_cookbook_action = (
        mode == MODEL_CHOICE_MODE
        and routed.capabilities == frozenset({'cookbook_admin'})
        and {
            canonical_tool(name) for name in routed.required
        } <= {'download_model', 'serve_preset', 'stop_served_model'}
        and bool(routed.required)
    )
    if explicit_cookbook_action:
        sealed_required |= frozenset(
            name for name in offered
            if canonical_tool(name) in {
                canonical_tool(required) for required in routed.required
            }
        )
    if explicit_image_edit:
        # An explicit supported image edit has one execution owner. Preserve
        # that typed requirement so prose cannot fabricate or refuse an
        # operation the user clearly requested and the backend can perform.
        sealed_required |= frozenset(
            name for name in offered if canonical_tool(name) == 'edit_image'
        )
    explicit_web_read = (
        mode == MODEL_CHOICE_MODE
        and routed.capabilities == frozenset({'search_browser'})
        and bool(routed.required)
        and {
            canonical_tool(name) for name in routed.required
        } <= {'web_search', 'web_fetch'}
    )
    if explicit_web_read:
        # Search discovery and page retrieval are distinct read-only
        # operations.  Once the turn router resolves one exactly, retaining
        # the whole warm web family lets the model substitute browser
        # navigation or repeat an old search. Preserve the resolved read while
        # leaving genuinely ambiguous web turns model-selected.
        required_web_names = {
            canonical_tool(required) for required in routed.required
        }
        # Keep one immutable recovery-capable set. The resolved reader still
        # executes first, but a failed/empty brokered read may recover through
        # page fetch or the private browser without rebuilding the contract.
        # This avoids both premature abandonment and mid-turn permission
        # expansion.
        recovery_names = set(required_web_names) | {'private_browser'}
        if 'web_search' in required_web_names:
            recovery_names.add('web_fetch')
        offered = frozenset(
            name for name in offered
            if canonical_tool(name) in recovery_names
        )
        sealed_required |= frozenset(
            name for name in offered
            if canonical_tool(name) in required_web_names
        )
    explicit_model_call = (
        mode == MODEL_CHOICE_MODE
        and bool(routed.required)
        and {
            canonical_tool(name) for name in routed.required
        } == {'chat_with_model'}
    )
    if explicit_model_call:
        offered = frozenset(
            name for name in offered if canonical_tool(name) == 'chat_with_model'
        )
        sealed_required |= offered
    explicit_chat_history_search = (
        mode == MODEL_CHOICE_MODE
        and bool(routed.required)
        and {
            canonical_tool(name) for name in routed.required
        } == {'search_chats'}
    )
    if explicit_chat_history_search:
        offered = frozenset(
            name for name in offered if canonical_tool(name) == 'search_chats'
        )
        sealed_required |= offered
    return replace(
        inventory, offered=offered, required=sealed_required,
        schema_json=tuple(s for s in inventory.schema_json
                          if json.loads(s)['function']['name'] in offered),
        required_read_operation=sealed_read, routing_experiment=mode,
        # Available families are not mutation authorization. Keep the original
        # request's authority; selection only changes what the model can see.
        active_capabilities=routed.active_capabilities | frozenset(url_family | research_family),
        capabilities=routed.capabilities | frozenset(url_family | research_family),
    )


def model_choice_private_tools(owner, model, contract):
    """Only offered private-record tools; never grant external/code authority."""
    if (owner not in {'pewds', 'sft_alex_creator'} or model != MODEL_CHOICE_MODEL
            or contract.routing_experiment != MODEL_CHOICE_MODE):
        return frozenset()
    from src.clean_agent_preview import SAFE_WRITE_TOOLS
    return frozenset(canonical_tool(n) for n in contract.offered) & SAFE_WRITE_TOOLS
