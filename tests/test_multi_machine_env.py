"""Unit tests for scripts/multi_machine_env.py."""

import ipaddress

import pytest

import scripts.multi_machine_env as mme


def test_is_tailscale_ip_recognizes_cgnat_range():
    assert mme.is_tailscale_ip("100.64.0.1") is True
    assert mme.is_tailscale_ip("100.127.255.254") is True


def test_is_tailscale_ip_rejects_public_and_loopback():
    assert mme.is_tailscale_ip("93.184.216.34") is False
    assert mme.is_tailscale_ip("127.0.0.1") is False
    assert mme.is_tailscale_ip("not-an-ip") is False


def test_parse_llm_hosts_splits_and_strips():
    assert mme.parse_llm_hosts(" box-a , box-b, ") == ["box-a", "box-b"]


def test_check_env_passes_minimal_docker_workstation():
    env = {
        "LM_STUDIO_URL": "http://host.docker.internal:1234",
        "ODYSSEUS_ALLOW_PRIVATE_CALDAV": "1",
    }
    results = mme.check_multi_machine_env(env)
    required_failures = [r for r in results if r.required and not r.ok]
    assert required_failures == []


def test_check_env_requires_private_caldav_flag_for_tailscale_radicale():
    env = {
        "RADICALE_URL": "http://100.64.0.10:5232/alice/",
        "ODYSSEUS_ALLOW_PRIVATE_CALDAV": "0",
    }
    results = mme.check_multi_machine_env(env)
    by_name = {r.name: r for r in results}
    assert by_name["private_caldav"].ok is False
    assert "ODYSSEUS_ALLOW_PRIVATE_CALDAV" in by_name["private_caldav"].detail


def test_check_env_accepts_tailscale_radicale_when_flag_set():
    env = {
        "RADICALE_URL": "http://100.64.0.10:5232/alice/",
        "ODYSSEUS_ALLOW_PRIVATE_CALDAV": "1",
        "LM_STUDIO_URL": "http://host.docker.internal:1234",
    }
    results = mme.check_multi_machine_env(env)
    by_name = {r.name: r for r in results}
    assert by_name["private_caldav"].ok is True


def test_check_env_warns_when_llm_hosts_missing():
    env = {"ODYSSEUS_ALLOW_PRIVATE_CALDAV": "1"}
    results = mme.check_multi_machine_env(env)
    by_name = {r.name: r for r in results}
    assert by_name["llm_hosts"].ok is False
    assert by_name["llm_hosts"].required is False


def test_extract_host_from_url():
    assert mme.extract_host("http://100.64.0.5:5232/alice/") == "100.64.0.5"
    assert mme.extract_host("https://calendar.example.com/dav") == "calendar.example.com"
