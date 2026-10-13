"""Wave 3 metadata requires a real allowlisted Linux producer artifact."""
import pytest
from src import browser_identity as browser
from src.agent_runtime.resources import ResourceIdentityError


@pytest.mark.parametrize('system,machine', [('Darwin', 'x86_64'), ('Darwin', 'arm64'),
    ('Windows', 'AMD64'), ('Windows', 'ARM64'), ('Linux', 'riscv64')])
async def test_unsupported_platform_fails_before_producer_execution(monkeypatch, system, machine):
    monkeypatch.setattr(browser.platform, 'system', lambda: system)
    monkeypatch.setattr(browser.platform, 'machine', lambda: machine)
    async def forbidden(*args, **kwargs): pytest.fail('Unsupported producer was executed')
    monkeypatch.setattr(browser, 'run_client', forbidden)
    with pytest.raises(ResourceIdentityError, match='Unsupported browser producer platform'):
        await browser.trusted_producer()


def test_observed_release_hash_contract_is_explicit():
    assert set(browser.PRODUCER_HASHES) == {'linux-x64', 'linux-arm64'}
    assert browser.PRODUCER_VERSION == '0.35.0'
    assert browser.SESSION_ACTIONS == {'session_info'}
