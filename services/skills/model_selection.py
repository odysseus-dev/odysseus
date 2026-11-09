"""Owner-scoped audit worker and teacher endpoint resolution."""

import logging


logger = logging.getLogger(__name__)

def resolve_audit_models(owner=None, model_spec=None, endpoint_url=None):
    """Resolve (url, model, headers, teacher) for an audit run from Settings.

    Worker = Utility model (falling back to Default, normalized to a served
    model id); teacher = the optional Settings → Teacher Model config. Shared
    by the manual /audit-all route and scheduled/event audits. Raises
    ValueError if no worker model.
    """
    from src.endpoint_resolver import resolve_endpoint, resolve_utility_fallback_candidates
    if model_spec and endpoint_url:
        # Scheduled tasks store the endpoint URL and model separately. Resolve
        # the endpoint directly so an explicit task choice cannot be replaced
        # by the global Utility setting.
        from src.endpoint_resolver import build_headers, resolve_endpoint_runtime
        from src.database import ModelEndpoint, SessionLocal
        from src.endpoint_resolver import normalize_base, same_endpoint_base
        from src.auth_helpers import owner_filter
        url = endpoint_url
        model = model_spec
        headers = {}
        db = SessionLocal()
        try:
            query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            for ep in owner_filter(query, ModelEndpoint, owner).all():
                base = normalize_base(getattr(ep, "base_url", "") or "")
                if same_endpoint_base(url, base):
                    runtime_base, api_key = resolve_endpoint_runtime(ep, owner=owner)
                    headers = build_headers(api_key, runtime_base or base)
                    break
        finally:
            db.close()
    elif model_spec:
        from src.ai_interaction import _resolve_model
        url, model, headers = _resolve_model(str(model_spec), owner=owner)
    else:
        url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        # Utility fallbacks are an explicit part of the user's model
        # configuration. Audits must use the same chain as other background
        # work instead of treating an empty primary Utility slot as fatal.
        for fallback_url, fallback_model, fallback_headers in resolve_utility_fallback_candidates(owner=owner):
            url, model, headers = fallback_url, fallback_model, fallback_headers
            break
    if not url or not model:
        raise ValueError("No model configured — set a Default or Utility model in Settings.")
    try:
        from src.llm_core import list_model_ids
        import os as _os
        _avail = list_model_ids(url, headers=headers)
        if _avail and model not in _avail:
            _base = _os.path.basename((model or "").rstrip("/"))
            model = next((a for a in _avail if _os.path.basename(a.rstrip("/")) == _base), None) or _avail[0]
    except Exception:
        pass

    teacher = None
    try:
        from src.settings import get_setting
        if get_setting("teacher_enabled", False):
            spec = (get_setting("teacher_model", "") or "").strip()
            if spec:
                from src.ai_interaction import _resolve_model
                t_url, t_model, t_headers = _resolve_model(spec, owner=owner)
                if t_url and t_model:
                    teacher = (t_url, t_model, t_headers)
    except Exception as e:
        logger.warning(f"Audit teacher resolve failed: {e}")
    return url, model, headers, teacher
