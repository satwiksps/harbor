"""Safety regression tests for the isolated, live application release drill."""

import argparse
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.verify_application_release import (
    APP_WORKLOADS,
    Drill,
    check_image_only_change,
    check_transition,
    source_fingerprints,
    versioned_image,
)


def chart(images):
    return yaml.safe_dump_all(
        [
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": name},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": container,
                                    "image": images["dashboard" if name == "dashboard" else "app"],
                                    "resources": {"limits": {"cpu": "1"}},
                                }
                            ]
                        }
                    }
                },
            }
            for name, container in APP_WORKLOADS.items()
        ]
        + [
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "postgres-data"},
                "spec": {"resources": {"requests": {"storage": "1Gi"}}},
            }
        ]
    )


def test_chart_guard_allows_only_the_five_application_image_changes():
    a = {"app": "app:a", "dashboard": "dashboard:a"}
    b = {"app": "app:b", "dashboard": "dashboard:b"}
    check_image_only_change(chart(a), chart(b), a, b)
    for edit in (
        lambda resources: resources[0]["spec"].update({"replicas": 2}),
        lambda resources: resources[-1]["spec"]["resources"]["requests"].update({"storage": "2Gi"}),
        lambda resources: resources.pop(),
    ):
        resources = list(yaml.safe_load_all(chart(b)))
        edit(resources)
        with pytest.raises(RuntimeError, match="more than application images"):
            check_image_only_change(chart(a), yaml.safe_dump_all(resources), a, b)


@pytest.mark.parametrize("ref", ["app", "app:dev", "app:latest", "host:5000/app", "app:stable"])
def test_image_refs_require_an_explicit_version(ref):
    with pytest.raises(argparse.ArgumentTypeError):
        versioned_image(ref)


def test_image_and_application_payload_must_both_change():
    before = {name: {"image_ids": ["sha256:a"], "payload_sha256": "a"} for name in APP_WORKLOADS}
    after = {name: {"image_ids": ["sha256:b"], "payload_sha256": "b"} for name in APP_WORKLOADS}
    check_transition(before, after)
    with pytest.raises(RuntimeError, match="same imageID"):
        check_transition(before, before)
    relabeled = copy.deepcopy(after)
    relabeled["gateway"]["payload_sha256"] = "a"
    with pytest.raises(RuntimeError, match="retagging/relabeling"):
        check_transition(before, relabeled)
    check_transition(before, before, rollback=True)
    with pytest.raises(RuntimeError, match="original imageID"):
        check_transition(before, after, rollback=True)


def test_source_hash_ignores_line_endings_but_detects_schema_and_app_edits(tmp_path):
    (tmp_path / "backend/migrations").mkdir(parents=True)
    (tmp_path / "upstreams").mkdir()
    for name in ("backend/db.py", "backend/migrate.py", "upstreams/server.py"):
        (tmp_path / name).write_text("# unchanged\n")
    model = tmp_path / "backend/models.py"
    model.write_bytes(b"class Model:\n    pass\n")
    app = tmp_path / "backend/app.py"
    app.write_bytes(b"version = 1\n")
    before = source_fingerprints(tmp_path)
    model.write_bytes(b"class Model:\r\n    pass\r\n")
    assert source_fingerprints(tmp_path) == before
    app.write_bytes(b"version = 2\n")
    changed = source_fingerprints(tmp_path)
    assert changed["payload_sha256"] != before["payload_sha256"]
    assert changed["schema_sha256"] == before["schema_sha256"]
    model.write_bytes(b"class NewModel:\n    pass\n")
    assert source_fingerprints(tmp_path)["schema_sha256"] != before["schema_sha256"]


class ClientStub:
    def __init__(self, events):
        self.events = events

    async def delete(self, path):
        self.events.append("key revoked")
        return SimpleNamespace(raise_for_status=lambda: None)

    async def post(self, path):
        self.events.append("session revoked")
        return SimpleNamespace(raise_for_status=lambda: None)

    async def aclose(self):
        self.events.append("client closed")


class FailureDrill(Drill):
    """Inject operational failures without touching a Kubernetes cluster."""

    def __init__(self, tmp_path, fail_at, execute=True):
        super().__init__(
            SimpleNamespace(
                a_app="app:a",
                b_app="app:b",
                a_dashboard="dash:a",
                b_dashboard="dash:b",
                context="isolated",
                namespace="isolated",
                release="isolated",
                execute=execute,
                report=tmp_path / "evidence.json",
                chart=Path("chart"),
            )
        )
        self.fail_at = fail_at
        self.events = []

    def preflight(self):
        self.events.append("preflight")
        if self.fail_at == "preflight":
            raise RuntimeError("preflight rejected")
        self.baseline_revision = 10

    async def authenticate(self):
        self.events.append("authenticate")
        client = ClientStub(self.events)
        self.clients = [client]
        self.keys = [{"client": client, "id": "temporary", "key": "never-record-this-secret"}]

    def helm(self, *args):
        self.events.append(args[0])
        if args[0] == "status":
            return json.dumps(
                {"version": self.baseline_revision + (self.fail_at == "concurrent_upgrade")}
            )
        if args[0] == "upgrade" and self.fail_at == "upgrade":
            raise RuntimeError("upgrade failed")
        if args[0] == "rollback" and self.fail_at == "rollback":
            raise RuntimeError("rollback failed")

    async def stage(self, name, images):
        self.events.append(name)
        if name == "B":
            raise RuntimeError("B validation failed")
        result = {
            "workloads": {
                name: {"image_ids": ["sha256:a"], "payload_sha256": "a"} for name in APP_WORKLOADS
            }
        }
        self.report["stages"][name] = result
        return result

    def unchanged(self, baseline, current):
        self.events.append("retention checked")


@pytest.mark.parametrize("failure", ["upgrade", "B", "rollback"])
async def test_failure_attempts_rollback_and_cleanup_and_records_evidence(tmp_path, failure):
    drill = FailureDrill(tmp_path, failure)
    await drill.execute()
    assert drill.events.index("upgrade") < drill.events.index("rollback")
    assert drill.events[-3:] == ["key revoked", "session revoked", "client closed"]
    report = json.loads(drill.args.report.read_text())
    assert report["status"] == "failed"
    assert report["upgrade_attempted"]
    assert "never-record-this-secret" not in drill.args.report.read_text()
    assert report.get("rollback_verified", False) is (failure != "rollback")
    if failure == "rollback":
        assert [entry["stage"] for entry in report["errors"]] == ["B", "rollback"]
    else:
        assert len(report["errors"]) == 1


async def test_preflight_failure_preserves_report_without_any_mutation(tmp_path):
    drill = FailureDrill(tmp_path, "preflight")
    await drill.execute()
    assert drill.events == ["preflight"]
    assert json.loads(drill.args.report.read_text())["errors"][0]["stage"] == "preflight"


async def test_default_preflight_never_authenticates_or_upgrades(tmp_path):
    drill = FailureDrill(tmp_path, None, execute=False)
    await drill.execute()
    assert drill.events == ["preflight"]
    assert drill.report["status"] == "preflight_passed_no_mutations"


async def test_concurrent_release_change_is_not_overwritten_or_rolled_back(tmp_path):
    drill = FailureDrill(tmp_path, "concurrent_upgrade")
    await drill.execute()
    assert "upgrade" not in drill.events
    assert "rollback" not in drill.events
    assert drill.events[-3:] == ["key revoked", "session revoked", "client closed"]
    assert "concurrent work" in drill.report["errors"][0]["error"]
