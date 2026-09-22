from pathlib import Path


def test_compare_renders_ask_user_in_the_originating_pane():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")

    assert "renderAskUserCard" in stream
    assert "} else if (json.type === 'ask_user') {" in stream
    assert "root: hist" in stream
    assert "state._paneSessionIds[paneIdx] === sessionId" in stream
    assert "streamToPane(paneIdx, sessionId, message, aiMessage, resumeOptions)" in stream
    assert "handleCompareSubmit" not in stream


def test_compare_submits_approval_only_to_the_pane_session():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")

    assert "fd.append('tool_approval_id', opts.toolApproval.approval_id || '');" in stream
    assert "fd.append('tool_approval_decision', opts.toolApproval.decision || '');" in stream
    assert "const isApproval = submission.kind === 'tool_approval';" in stream
    assert "const message = isApproval ? ''" in stream
    assert "if (!isApproval) _appendPaneMessage(hist, 'user', message);" in stream
    assert "json.type === 'tool_approval_resolved'" in stream
    assert "json.decision === 'deny' ? 'Denied.'" in stream


def test_compare_continuation_does_not_lose_or_replace_pane_ownership():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")
    index = (root / "static/js/compare/index.js").read_text(encoding="utf-8")

    assert "if (state._abortControllers[paneIdx] === ac)" in stream
    assert "if (activeController === originController)" in stream
    assert "if (activeController) return;" in stream
    assert "registerStreamActions({ rerollPane, autoPreviewHtml: _autoPreviewHtml, setSendBtn: _setSendBtn });" in index
    assert "const compareStillStreaming = state._abortControllers.some(Boolean);" in index
    assert "_setSendBtn(compareStillStreaming ? 'stop' : 'send');" in index


def test_compare_restores_the_card_instead_of_dropping_a_timed_out_choice():
    """The card is removed the moment a choice is accepted.

    If the originating stream still owns the pane when the resume deadline
    passes, the decision has nowhere to go — so the card has to come back
    rather than the click vanishing silently.
    """

    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")

    assert "function _restorePaneAskUserCard(" in stream
    assert "_restorePaneAskUserCard(paneIdx, sessionId, submission, originController);" in stream
    assert "submission.payload || {}" in stream

    start = stream.index("function _resumePaneChoiceWhenIdle(")
    end = stream.index("function _renderPaneAskUserCard(", start)
    resume = stream[start:end]
    # The deadline must not fall through to a bare return any more.
    assert "if (Date.now() - startedAt < 10000) setTimeout(resume, 25);" not in resume
