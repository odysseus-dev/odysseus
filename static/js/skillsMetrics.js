export function auditNumber(sk, key) {
  if (!sk) return null;
  const verdict = sk.verdict && typeof sk.verdict === 'object' ? sk.verdict : null;
  const candidates = [sk[key], verdict && verdict[key]];
  for (const value of candidates) {
    if (value === null || value === undefined || value === '') continue;
    const n = Number(value);
    if (Number.isFinite(n)) return Math.trunc(n);
  }
  return null;
}

export function skillSavedTurnsValue(sk) {
  const turns = auditNumber(sk, 'saved_turns');
  return turns === null ? 0 : turns;
}

export function necessityKind(sk) {
  const nec = sk && sk.necessity;
  // The recommended keeper is the duplicate group winner, not a review item.
  // Lower-priority members remain marked as duplicates for bin/review flows.
  if (sk && sk._duplicateGroup && !sk._duplicateKeep) return 'duplicate';
  if (sk && sk._duplicateGroup && sk._duplicateKeep) {
    const reason = String(nec?.reason || '').toLowerCase();
    const redundant = (nec?.redundant_with || []).filter(Boolean);
    if (redundant.length || /duplicat|redundan|overlap|same skill|same procedure/.test(reason)) return null;
  }
  if (!nec || nec.necessary !== false) return null;
  const reason = String(nec.reason || '').toLowerCase();
  const redundant = (nec.redundant_with || []).filter(Boolean);
  if (redundant.length || /duplicat|redundan|overlap|same skill|same procedure/.test(reason)) return 'duplicate';
  if (/trivial|generic|capable assistant|without a saved|not need|unnecessary/.test(reason)) return 'trivial';
  return 'irrelevant';
}

export function skillNeedsAudit(sk) {
  if (sk && sk.source === 'builtin') return false;
  return !(sk && sk.audit_verdict);
}

export function skillIsProven(sk, approvalThreshold = 0.85) {
  if (!sk) return false;
  if (sk.source === 'builtin') return sk.status === 'published';
  const conf = Number(sk.confidence || 0);
  const baseline = String(sk.baseline_verdict || '').toLowerCase();
  const savedTurns = skillSavedTurnsValue(sk);
  const savedCallsRaw = auditNumber(sk, 'saved_tool_calls');
  const savedCalls = savedCallsRaw === null ? 0 : savedCallsRaw;
  const usefulness = Number(sk.usefulness ?? 0);
  const necessity = necessityKind(sk);
  if (necessity === 'duplicate' || necessity === 'irrelevant') return false;
  if (baseline === 'worse') return false;
  if (baseline === 'same' && usefulness < 0.5 && savedTurns <= 0 && savedCalls <= 0) return false;
  return sk.status === 'published' && sk.audit_verdict === 'pass' && conf >= approvalThreshold;
}

export function skillIsApproved(sk, approvalThreshold = 0.85) {
  if (!sk || sk.status !== 'published') return false;
  if (sk.necessity?.necessary === false) return false;
  return sk.source === 'builtin' || (sk.audit_verdict === 'pass' && Number(sk.confidence || 0) >= approvalThreshold);
}

export function skillNeedsReview(sk, approvalThreshold = 0.85) {
  if (!sk) return false;
  if (sk.source === 'builtin') return false;
  // Baseline efficiency is evidence, not an extra approval gate. A skill
  // must not show both Approved and Draft merely because it saves no turns.
  // Unaudited skills keep their separate Queued state.
  return !!sk.audit_verdict && !skillIsApproved(sk, approvalThreshold);
}

export function skillsSummaryMetrics(skills, approvalThreshold = 0.85) {
  const list = Array.isArray(skills) ? skills : [];
  const total = list.length;
  const approved = list.filter(sk => skillIsApproved(sk, approvalThreshold)).length;
  const proven = list.filter(sk => skillIsProven(sk, approvalThreshold)).length;
  const needsAudit = list.filter(skillNeedsAudit).length;
  const audited = list.filter(sk => !!sk?.audit_verdict).length;
  const review = list.filter(sk => skillNeedsReview(sk, approvalThreshold)).length;
  const drafts = list.filter(sk => (sk?.status || 'draft') === 'draft').length;
  const binned = list.filter(sk => sk?.status === 'binned').length;
  const savedTurns = list.reduce((sum, sk) => sum + Math.max(0, skillSavedTurnsValue(sk)), 0);
  return { total, approved, proven, needsAudit, audited, review, drafts, binned, savedTurns };
}

export function filterSkillsByQuickFilter(skills, quickFilter, approvalThreshold = 0.85) {
  const list = Array.isArray(skills) ? skills : [];
  if (quickFilter === 'all') return list;
  if (quickFilter === 'approved') return list.filter(sk => sk.source !== 'builtin' && skillIsApproved(sk, approvalThreshold));
  if (quickFilter === 'builtin') return list.filter(sk => sk.source === 'builtin');
  if (quickFilter === 'draft') return list.filter(sk => sk.source !== 'builtin' && !skillIsApproved(sk, approvalThreshold));
  if (quickFilter === 'proven') return list.filter(sk => skillIsProven(sk, approvalThreshold));
  if (quickFilter === 'audited') return list.filter(sk => !!sk?.audit_verdict);
  if (quickFilter === 'saved') return list.filter(sk => skillSavedTurnsValue(sk) > 0);
  if (quickFilter === 'unaudited') return list.filter(skillNeedsAudit);
  if (quickFilter === 'review') return list.filter(sk => skillNeedsReview(sk, approvalThreshold));
  if (quickFilter === 'drafts') return list.filter(sk => (sk?.status || 'draft') === 'draft');
  if (quickFilter === 'binned') return list.filter(sk => sk?.status === 'binned');
  return list;
}
