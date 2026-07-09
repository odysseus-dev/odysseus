"""Tests for chromadb_health — ok/degraded/down/disabled classification."""
import pytest

from src import service_health as sh


class _Store:
    def __init__(self, healthy):
        self.healthy = healthy


def test_chromadb_both_healthy_ok():
    s = sh.chromadb_health(_Store(True), _Store(True))
    assert s["status"] == sh.OK
    assert s["meta"] == {"rag": True, "memory": True}


def test_chromadb_one_down_degraded():
    s = sh.chromadb_health(_Store(True), _Store(False))
    assert s["status"] == sh.DEGRADED


def test_chromadb_both_unhealthy_down():
    s = sh.chromadb_health(_Store(False), _Store(False))
    assert s["status"] == sh.DOWN


def test_chromadb_both_absent_disabled():
    s = sh.chromadb_health(None, None)
    assert s["status"] == sh.DISABLED


def test_chromadb_one_absent_one_healthy_ok():
    # An absent store is not a failure; the present one being healthy is ok.
    s = sh.chromadb_health(_Store(True), None)
    assert s["status"] == sh.OK
    assert s["meta"]["memory"] is None
