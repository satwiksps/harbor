"""Verify local database/PVC persistence and a compatible Helm configuration rollback."""

import json
import subprocess
import time
from pathlib import Path

from local_settings import (
    CONTEXT,
    NAMESPACE,
    POSTGRES_DATABASE,
    POSTGRES_USER,
    RELEASE,
)
from local_settings import (
    KUBE as BASE,
)

ROOT = Path(__file__).resolve().parents[1]
HELM = str(ROOT / ".tools" / "helm.exe")
REPORT = ROOT / ".runtime" / "release-verification.json"


def command(arguments, timeout=240):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def kube(*arguments, timeout=240):
    return command([*BASE, *arguments], timeout=timeout)


def helm(*arguments):
    return command(
        [
            HELM,
            *arguments,
            "--kube-context",
            CONTEXT,
            "-n",
            NAMESPACE,
        ],
        timeout=900,
    )


def snapshot():
    pvc = json.loads(kube("get", "pvc", "-o", "json"))
    pods = json.loads(kube("get", "pods", "-o", "json"))
    users = kube(
        "exec",
        "deployment/postgres",
        "--",
        "psql",
        "-U",
        POSTGRES_USER,
        "-d",
        POSTGRES_DATABASE,
        "-Atc",
        "SELECT id FROM users ORDER BY id;",
    )
    store = kube(
        "exec",
        "deployment/wordpress",
        "--",
        "wp",
        "eval",
        """
$privacy = get_post((int)get_option('wp_page_for_privacy_policy'));
echo wp_json_encode([
    'products' => get_posts(['post_type'=>'product', 'numberposts'=>-1, 'fields'=>'ids',
                            'orderby'=>'ID', 'order'=>'ASC']),
    'orders' => wc_get_orders(['limit'=>-1, 'return'=>'ids', 'orderby'=>'ID', 'order'=>'ASC']),
    'privacy_id' => $privacy ? $privacy->ID : null,
    'privacy_content_sha256' => $privacy ? hash('sha256', $privacy->post_content) : null,
    'coming_soon' => get_option('woocommerce_coming_soon'),
]);
""",
    )
    return {
        "pvcs": {item["metadata"]["name"]: item["metadata"]["uid"] for item in pvc["items"]},
        "data_pods": {
            item["metadata"]["labels"]["app"]: item["metadata"]["uid"]
            for item in pods["items"]
            if item["metadata"].get("labels", {}).get("app") in {"wordpress", "mariadb", "postgres"}
            and not item["metadata"].get("deletionTimestamp")
        },
        "user_ids": users.splitlines(),
        "store": json.loads(store),
    }


def assert_same_data(before, after):
    for field in ("pvcs", "user_ids", "store"):
        assert before[field] == after[field], f"Persistent {field} changed during verification"


def main():
    current = json.loads(helm("status", RELEASE, "-o", "json"))
    assert current["info"]["status"] == "deployed", "Start with a healthy deployed release"
    baseline_revision = current["version"]
    deployment = json.loads(kube("get", "deployment/gateway", "-o", "json"))
    baseline_cpu = deployment["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"][
        "cpu"
    ]
    test_cpu = "900m" if baseline_cpu != "900m" else "800m"
    before = snapshot()
    assert before["store"]["coming_soon"] == "no", "Publish the demo store before testing"
    report = {"baseline_revision": baseline_revision, "before": before}

    for name in ("postgres", "mariadb", "wordpress"):
        kube("rollout", "restart", "deployment/" + name)
        kube("rollout", "status", "deployment/" + name, "--timeout=180s")
    after_restart = snapshot()
    assert_same_data(before, after_restart)
    for name, old_uid in before["data_pods"].items():
        assert after_restart["data_pods"][name] != old_uid, f"{name} was not replaced"
    report["after_data_pod_restarts"] = after_restart
    print(
        "PASS: WordPress, MariaDB and PostgreSQL pod replacement retained records and PVCs",
        flush=True,
    )

    changed = False
    try:
        # Reuse the current compatible values and images; only one CPU limit changes.
        changed = True
        helm(
            "upgrade",
            RELEASE,
            str(ROOT / "deploy/chart"),
            "--reuse-values",
            "--set-string",
            "gateway.resources.limits.cpu=" + test_cpu,
            "--wait",
            "--timeout",
            "15m",
        )
        kube("wait", "--for=condition=complete", "job/wordpress-bootstrap", "--timeout=300s")
        edited = json.loads(kube("get", "deployment/gateway", "-o", "json"))
        assert (
            edited["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["cpu"]
            == test_cpu
        )
        assert_same_data(before, snapshot())
        report["upgrade_revision"] = json.loads(helm("status", RELEASE, "-o", "json"))["version"]
    finally:
        if changed:
            helm("rollback", RELEASE, str(baseline_revision), "--wait", "--timeout", "15m")
    restored = json.loads(kube("get", "deployment/gateway", "-o", "json"))
    assert (
        restored["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["cpu"]
        == baseline_cpu
    )
    after_rollback = snapshot()
    assert_same_data(before, after_rollback)
    report.update(
        {
            "after_rollback": after_rollback,
            "final_revision": json.loads(helm("status", RELEASE, "-o", "json"))["version"],
            "restored_gateway_cpu_limit": baseline_cpu,
            "scope": "Configuration rollback with unchanged image tags; not a database or old-binary restore",
            "verified_at_unix": int(time.time()),
        }
    )
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS: compatible Helm upgrade and rollback restored configuration and retained data")
    print(f"Evidence written to {REPORT}")


if __name__ == "__main__":
    main()
