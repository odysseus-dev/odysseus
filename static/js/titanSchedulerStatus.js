/**
 * Shared Titan VRAM scheduler status (canonical GPU state).
 */
(function () {
  const API_BASE = window.API_BASE || "";
  const STATUS_URL = `${API_BASE}/api/titan/scheduler/status`;

  async function fetchStatus(signal) {
    try {
      const r = await fetch(STATUS_URL, { credentials: "same-origin", signal });
      if (!r.ok) return null;
      return await r.json();
    } catch (_) {
      return null;
    }
  }

  function phaseFromStatus(st) {
    return (st && st.state && st.state.phase) || "idle";
  }

  function formatBadge(st) {
    if (!st) return { text: "Scheduler ?", cls: "warn" };
    const phase = phaseFromStatus(st);
    const llm = st.llm || {};
    const sd = st.sd || {};
    const vram = st.vram_used_mb;
    const total = st.gpu_total_mb;
    const queued = (st.state && st.state.external_jobs_queued) || 0;

    if (phase === "generating" || phase === "swapping" || phase === "restoring_llm") {
      const step = Number((st.state && st.state.progress_step) || 0);
      const totalSteps = Number((st.state && st.state.progress_total) || 0);
      let text = phase === "swapping" ? "VRAM swap" : phase === "restoring_llm" ? "Restore LLM" : "SD gen";
      if (totalSteps > 0 && step > 0) text += ` ${Math.round((step / totalSteps) * 100)}%`;
      return { text, cls: "busy", phase, llm, sd, vram, gpu_total_mb: total, queued };
    }
    if (phase === "error") {
      return { text: "Scheduler error", cls: "error", phase, llm, sd, vram, gpu_total_mb: total, queued };
    }
    if (queued > 0) {
      return { text: `Queue ${queued}`, cls: "warn", phase, llm, sd, vram, gpu_total_mb: total, queued };
    }
    const parts = [];
    if (llm.active) parts.push(`LLM ${llm.profile || "?"}`);
    if (sd.active) parts.push(`SD ${sd.profile || "?"}`);
    if (!parts.length) parts.push("idle");
    if (vram != null && total != null) parts.push(`${vram}/${total} MB`);
    return { text: parts.join(" · "), cls: "ok", phase, llm, sd, vram, gpu_total_mb: total, queued };
  }

  function imagePhasePayload(st) {
    const state = (st && st.state) || {};
    return {
      phase: state.phase || "idle",
      progress_step: state.progress_step || 0,
      progress_total: state.progress_total || 0,
      last_error: state.last_error,
      llm: st && st.llm,
      sd: st && st.sd,
      vram_used_mb: st && st.vram_used_mb,
      gpu_total_mb: st && st.gpu_total_mb,
    };
  }

  window.titanSchedulerStatus = {
    fetchStatus,
    phaseFromStatus,
    formatBadge,
    imagePhasePayload,
    STATUS_URL,
  };
})();
