"""CLI behaviour of kubestats.main without a cluster."""
from __future__ import annotations

import pytest
from kubernetes.client.exceptions import ApiException
from typer.testing import CliRunner

from kubestats import kubectl, main


@pytest.fixture()
def unknown_namespace(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the cluster: get_namespace answers 404, collectors must not run."""
    collected: list[str] = []

    def not_found(name: str):
        raise ApiException(status=404, reason="Not Found")

    def collector(*args, **kwargs):
        collected.append("called")
        return []

    monkeypatch.setattr(kubectl, "load_config", lambda **kwargs: None)
    monkeypatch.setattr(kubectl, "get_namespace", not_found)
    for fn in ("get_crd_stats", "get_adoption_stats", "get_istio_stats",
               "get_service_entries", "list_pods"):
        monkeypatch.setattr(kubectl, fn, collector)
    return collected


@pytest.mark.parametrize("command", ["crds", "adoption", "istio", "all"])
def test_unknown_namespace_exits_with_error(unknown_namespace: list[str], command: str) -> None:
    result = CliRunner().invoke(main.app, [command, "--namespace", "tippfehler"])

    assert result.exit_code == 1
    assert "Namespace not found" in result.output
    assert "tippfehler" in result.output
    assert unknown_namespace == []
