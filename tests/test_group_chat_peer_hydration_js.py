r"""Group-chat reload regression: peer turns must hydrate as their agent (#4964).

In a group chat, each peer agent's turn is cross-persisted into a participant
session as a ``role:'user'`` message whose content is prefixed ``"[name]: "``
(static/js/group.js). The live group view always renders these on the agent
side with the agent's name, but the reload/hydration path used to derive the
bubble side from ``role`` alone, so on reload every peer turn flipped to the
right as "You" (wrong side + wrong sender).

The fix lives entirely in ``sessions.js`` ``_renderHistoryMessage`` (the history
reload/pager path): during group-session hydration only, it extracts the
``[name]:`` prefix into ``_groupPeerName``, strips it from the body, and renders
the turn through the shared history renderer as an ``assistant`` message that
carries ``group_model`` — the metadata key ``chatRenderer.addMessage`` already
honours to label a bubble with a group participant's name verbatim, rather than
running it through ``shortModel()``.

Source-text guards, deliberately (TESTING_STANDARD.md, "Narrow exception"):
``_renderHistoryMessage`` is module-private, and driving it needs a DOM plus the
whole ``sessions.js`` import graph (chatRenderer, markdown, ...). The front end
has no jsdom/node test harness here, so this invariant cannot practically be
exercised at runtime. These guards pin the side/sender derivation so a refactor
cannot silently regress it back to role-only.
"""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SESSIONS = (_REPO / "static" / "js" / "sessions.js").read_text(encoding="utf-8")
_RENDERER = (_REPO / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")


def test_peer_extraction_gated_on_group_session():
    # The peer-prefix extraction must be gated on a group session so a normal
    # chat message that merely starts with "[x]:" is never mis-attributed.
    assert "startsWith('[GRP]')" in _SESSIONS
    assert "_groupPeerName" in _SESSIONS
    m = re.search(r"startsWith\('\[GRP\]'\)\)\s*\{(.*?)\n  \}", _SESSIONS, re.S)
    assert m, "expected a group-session-gated peer-extraction block in sessions.js"
    block = m.group(1)
    assert "match(" in block and r"\[" in block, "peer block must parse a [name]: prefix"
    assert "_groupPeerName =" in block, "peer block must set _groupPeerName"


def test_group_peer_hydrates_as_assistant_named_after_the_peer():
    # A peer turn must render on the agent side ('assistant'), not from the
    # persisted msg.role of 'user', and carry the peer's name as group_model.
    # Deriving the bubble side from msg.role alone is exactly the #4964 bug.
    m = re.search(r"if \(_groupPeerName\) \{(.*?)\n  \}", _SESSIONS, re.S)
    assert m, "expected a _groupPeerName render branch in _renderHistoryMessage"
    branch = m.group(1)
    assert "'assistant'" in branch, (
        "group-peer turns must hydrate on the agent side, not from msg.role"
    )
    assert "msg.role" not in branch, "peer branch reverted to role-only derivation"
    assert re.search(r"group_model:\s*_groupPeerName", branch), (
        "peer branch must label the bubble with the peer name via group_model"
    )
    # The renderer colors an assistant label after the session's resolved model,
    # which paints every peer identically. Recolor per peer, like the live view.
    assert re.search(r"applyModelColor\(roleEl, _groupPeerName\)", branch), (
        "peer bubbles must be colored per peer, not after the session model"
    )


def test_renderer_labels_group_model_verbatim():
    # Cross-file guard: sessions.js leans on chatRenderer honouring `group_model`
    # as a verbatim sender label for non-user bubbles. shortModel() would rewrite
    # a peer name (it splits on "/" and truncates past 25 chars), so the label
    # must not go through it. If this hook is dropped, the hydration fix regresses.
    assert re.search(
        r"if \(metadata\?\.group_model && role !== 'user'\) \{\s*_roleText = metadata\.group_model;",
        _RENDERER,
    ), "chatRenderer.addMessage must label non-user bubbles with metadata.group_model"
