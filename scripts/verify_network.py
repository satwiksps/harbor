"""Verify live project NetworkPolicies and Secret RBAC without reading credentials."""

import json
import secrets
import subprocess
from pathlib import Path

from local_settings import KUBE as BASE
from local_settings import NAMESPACE

PYTHON = "/app/.venv/bin/python"


def kube(*args, stdin=None, ok=True, timeout=90):
    result = subprocess.run(
        [*BASE, *args], input=stdin, text=True, capture_output=True, timeout=timeout
    )
    if ok and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def socket_probe(targets):
    return """
import errno,json,socket
results=[]
for host,port,allowed in TARGETS:
    address=socket.gethostbyname(host)
    try:
        connection=socket.create_connection((host,port),timeout=3)
        connection.close()
        connected=True
        reason='connected'
    except (TimeoutError,ConnectionRefusedError) as exc:
        connected=False
        reason=type(exc).__name__
    results.append({'host':host,'port':port,'address':address,'connected':connected,'reason':reason})
    assert connected == allowed, results
print(json.dumps(results))
""".replace("TARGETS", repr(targets))


def main():
    reports = {}
    # Positive controls establish that targets are listening at the time of denial checks.
    reports["gateway_connections"] = json.loads(
        kube(
            "exec",
            "-i",
            "deployment/gateway",
            "--",
            PYTHON,
            "-",
            stdin=socket_probe(
                [
                    ("woo-mcp", 8001, True),
                    ("policies-mcp", 8001, True),
                    ("shipping-mcp", 8001, True),
                    ("postgres", 5432, True),
                ]
            ),
        ).stdout
    )
    reports["woo_connections"] = json.loads(
        kube(
            "exec",
            "-i",
            "deployment/woo-mcp",
            "--",
            PYTHON,
            "-",
            stdin=socket_probe([("wordpress", 80, True), ("mariadb", 3306, False)]),
        ).stdout
    )
    kube("exec", "deployment/wordpress", "--", "php", "/opt/harbor/database-ready.php")
    reports["wordpress_database_health"] = "passed"
    reports["policy_service_denials"] = json.loads(
        kube(
            "exec",
            "-i",
            "deployment/policies-mcp",
            "--",
            PYTHON,
            "-",
            stdin=socket_probe(
                [
                    ("wordpress", 80, False),
                    ("mariadb", 3306, False),
                    ("postgres", 5432, False),
                    ("woo-mcp", 8001, False),
                ]
            ),
        ).stdout
    )
    pod_name = "harbor-boundary-" + secrets.token_hex(4)
    deployment = json.loads(kube("get", "deployment/gateway", "-o", "json").stdout)
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": pod_name, "labels": {"app": "boundary-check"}},
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 60,
            "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
            "containers": [
                {
                    "name": "probe",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": [
                        PYTHON,
                        "-c",
                        socket_probe(
                            [
                                ("woo-mcp", 8001, False),
                                ("policies-mcp", 8001, False),
                                ("shipping-mcp", 8001, False),
                            ]
                        ),
                    ],
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "32Mi"},
                        "limits": {"cpu": "100m", "memory": "64Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
    try:
        kube("create", "-f", "-", stdin=json.dumps(pod))
        kube("wait", "--for=jsonpath={.status.phase}=Succeeded", "pod/" + pod_name, "--timeout=60s")
        reports["untrusted_pod_denials"] = json.loads(kube("logs", pod_name).stdout)
    finally:
        kube("delete", "pod", pod_name, "--ignore-not-found", "--wait=false", ok=False)
    reports["rbac"] = []
    for verb, resource, expected in [
        ("get", "secret/harbor-upstream-credentials", "yes"),
        ("patch", "secret/harbor-upstream-credentials", "yes"),
        ("update", "secret/harbor-upstream-credentials", "no"),
        ("get", "secret/harbor-app-secrets", "no"),
        ("list", "secrets", "no"),
        ("create", "deployments", "no"),
    ]:
        actual = kube(
            "auth",
            "can-i",
            verb,
            resource,
            f"--as=system:serviceaccount:{NAMESPACE}:harbor-gateway",
            ok=False,
        ).stdout.strip()
        assert actual == expected, (verb, resource, actual)
        reports["rbac"].append({"verb": verb, "resource": resource, "allowed": actual})
    destination = Path(".runtime/network-verification.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print("PASS: gateway positive controls, upstream/network denials, and named-Secret-only RBAC")
    print(f"Evidence written to {destination}; no credentials were printed.")


if __name__ == "__main__":
    main()
