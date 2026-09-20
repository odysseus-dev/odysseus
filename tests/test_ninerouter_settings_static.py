"""Static regressions for Settings 9router connections card.

Agents: this card is the product connect surface. It must talk to
``/api/ninerouter/connections``, never iframe 9router, and never POST
``/api/model-endpoints`` for cloud connect.
"""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_ADMIN = (_REPO / "static" / "js" / "admin.js").read_text(encoding="utf-8")


def test_settings_card_is_ninerouter_connections():
    assert "9router connections" in _INDEX
    assert "Add API Models" not in _INDEX
    assert 'id="adm-ninerouter-connections"' in _INDEX
    assert 'id="adm-nrApiKey"' in _INDEX


def test_admin_loads_ninerouter_catalog_not_model_endpoint_keys():
    assert "/api/ninerouter/connections" in _ADMIN
    assert "adm-ninerouter-connections" in _ADMIN
