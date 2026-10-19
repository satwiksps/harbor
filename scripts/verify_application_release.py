"""Prove an explicit isolated Helm release runs distinct application binaries A -> B -> A.

Without --execute this performs read-only preflight checks. It never provisions or
deletes an environment, restarts data workloads, or restores database contents.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx2
import yaml
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.acceptance import Acceptance, unpack  # noqa: E402

APP_WORKLOADS = {
    "gateway": "gateway",
    "woo-mcp": "mcp",
    "policies-mcp": "mcp",
    "shipping-mcp": "mcp",
    "dashboard": "dashboard",
}
DATA_WORKLOADS = ("postgres", "mariadb", "wordpress")
ISOLATION_LABEL = "harbor.local/release-verification"
SOURCE_HASH = """
import hashlib, json
from pathlib import Path
files = {
    p.relative_to('/app').as_posix():
        hashlib.sha256(p.read_bytes().replace(b'\\r\\n', b'\\n')).hexdigest()
    for directory in ('backend', 'upstreams')
    for p in sorted((Path('/app') / directory).rglob('*.py'))
}
assert files
print(hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest())
"""
SCHEMA_HASH = """
import hashlib, json
from pathlib import Path
paths = list(Path('/app/backend/migrations').rglob('*.py'))
paths += [Path('/app/backend') / name for name in ('models.py', 'db.py', 'migrate.py')]
files = {
    p.relative_to('/app').as_posix():
        hashlib.sha256(p.read_bytes().replace(b'\\r\\n', b'\\n')).hexdigest()
    for p in sorted(paths)
}
print(hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest())
"""
STORE_SNAPSHOT = """
function drill_normalize($value) {
    $value = json_decode(wp_json_encode($value), true);
    if (is_array($value)) {
        foreach ($value as &$entry) { $entry = drill_normalize($entry); }
        unset($entry);
        if (!array_is_list($value)) { ksort($value); }
    }
    return $value;
}
function drill_hash($value) {
    return hash('sha256', wp_json_encode(drill_normalize($value)));
}
$products = [];
foreach (get_posts(['post_type'=>['product','product_variation'], 'post_status'=>'any',
                   'numberposts'=>-1, 'fields'=>'ids', 'orderby'=>'ID', 'order'=>'ASC']) as $id) {
    $product = wc_get_product($id);
    $products[(string)$id] = drill_hash($product->get_data());
}
$orders = [];
foreach (wc_get_orders(['limit'=>-1, 'orderby'=>'ID', 'order'=>'ASC']) as $order) {
    $data = $order->get_data();
    foreach (['line_items'=>'line_item','tax_lines'=>'tax','shipping_lines'=>'shipping',
              'fee_lines'=>'fee','coupon_lines'=>'coupon'] as $kind => $type) {
        $data[$kind] = [];
        foreach ($order->get_items($type) as $id => $item) {
            $data[$kind][(string)$id] = $item->get_data();
        }
    }
    $orders[(string)$order->get_id()] = drill_hash($data);
}
$privacy = get_post((int)get_option('wp_page_for_privacy_policy'));
echo wp_json_encode([
    'products'=>$products, 'orders'=>$orders,
    'privacy_id'=>$privacy ? $privacy->ID : null,
    'privacy_sha256'=>$privacy ? hash('sha256', $privacy->post_content) : null,
    'coming_soon'=>get_option('woocommerce_coming_soon')
]);
"""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_fingerprints(root):
    for required in (
        "backend/app.py",
        "backend/models.py",
        "backend/db.py",
        "backend/migrate.py",
        "upstreams/server.py",
    ):
        require((root / required).is_file(), f"Candidate source is missing {required}")
    paths = [p for directory in ("backend", "upstreams") for p in (root / directory).rglob("*.py")]
    require(paths, "Candidate source must include backend and upstreams")
    files = {
        p.relative_to(root).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for p in paths
    }
    schema = {
        name: value
        for name, value in files.items()
        if name.startswith("backend/migrations/")
        or name in {"backend/models.py", "backend/db.py", "backend/migrate.py"}
    }
    require(schema, "Candidate source must include the database schema")
    return {"payload_sha256": digest(files), "schema_sha256": digest(schema)}


def versioned_image(value):
    """Reject implicit/moving development tags; runtime hashes remain the proof."""
    if re.fullmatch(r".+@sha256:[a-f0-9]{64}", value):
        return value
    tag = value.rsplit("/", 1)[-1].partition(":")[2]
    if not tag or tag.lower() in {"latest", "dev", "main", "master", "stable"}:
        raise argparse.ArgumentTypeError("Use a version-specific tag or sha256 digest")
    return value


def manifests(text):
    result = {}
    for item in yaml.safe_load_all(text):
        if not item or item.get("metadata", {}).get("annotations", {}).get("helm.sh/hook"):
            continue
        key = (item["apiVersion"], item["kind"], item["metadata"]["name"])
        require(key not in result, f"Duplicate chart resource: {key}")
        result[key] = item
    return result


def check_image_only_change(before, after, a_images, b_images):
    """Fail closed if this chart would modify anything except the five app images."""
    expected = copy.deepcopy(manifests(before))
    for name, container_name in APP_WORKLOADS.items():
        resource = expected.get(("apps/v1", "Deployment", name))
        require(resource is not None, f"Missing deployment {name}")
        containers = resource["spec"]["template"]["spec"]["containers"]
        container = next(c for c in containers if c["name"] == container_name)
        kind = "dashboard" if name == "dashboard" else "app"
        require(container["image"] == a_images[kind], f"{name} is not using expected A image")
        container["image"] = b_images[kind]
    require(
        expected == manifests(after),
        "Rendered chart changes more than application images. Install A with this same chart first.",
    )


def check_transition(before, after, rollback=False):
    for name in APP_WORKLOADS:
        a, b = before[name], after[name]
        if rollback:
            require(a["image_ids"] == b["image_ids"], f"{name}: original imageID not restored")
            require(a["payload_sha256"] == b["payload_sha256"], f"{name}: A payload not restored")
        else:
            require(
                set(a["image_ids"]).isdisjoint(b["image_ids"]),
                f"{name}: A and B resolve to the same imageID",
            )
            require(
                a["payload_sha256"] != b["payload_sha256"],
                f"{name}: application payload unchanged; retagging/relabeling is insufficient",
            )


class Drill:
    def __init__(self, args):
        self.args = args
        self.a_images = {"app": args.a_app, "dashboard": args.a_dashboard}
        self.b_images = {"app": args.b_app, "dashboard": args.b_dashboard}
        self.report = {
            "scope": "Compatible application binaries A -> B -> A; no database restore",
            "target": {
                "context": args.context,
                "namespace": args.namespace,
                "release": args.release,
            },
            "images": {"A": self.a_images, "B": self.b_images},
            "started_at_unix": int(time.time()),
            "execute": args.execute,
            "stages": {},
            "errors": [],
        }
        self.clients = []
        self.keys = []
        self.upgrade_attempted = False
        self.baseline_revision = None

    def run_command(self, args, timeout=240, operation=None):
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        # CLI diagnostics may contain rendered values or credentials. Keep them out
        # of the shareable evidence, which records the failing stage and exit code.
        operation = operation or f"{Path(args[0]).name} {args[1]}"
        require(result.returncode == 0, f"{operation} failed ({result.returncode})")
        return result.stdout.strip()

    def kube(self, *args):
        return self.run_command(
            [self.args.kubectl, "--context", self.args.context, "-n", self.args.namespace, *args],
            operation="kubectl " + args[0],
        )

    def helm(self, *args):
        return self.run_command(
            [self.args.helm, *args, "--kube-context", self.args.context, "-n", self.args.namespace],
            timeout=960,
        )

    def save(self):
        self.args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.args.report.with_suffix(".partial")
        temporary.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        temporary.replace(self.args.report)

    def error(self, stage, error):
        # Application/network exception text may include URLs or upstream results.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        self.report["errors"].append({"stage": stage, "error": detail})
        self.save()

    def preflight(self):
        require(self.a_images["app"] != self.b_images["app"], "A and B app refs must differ")
        require(
            self.a_images["dashboard"] != self.b_images["dashboard"],
            "A and B dashboard refs must differ",
        )
        namespace = json.loads(self.kube("get", "namespace", self.args.namespace, "-o", "json"))
        require(
            namespace["metadata"].get("labels", {}).get(ISOLATION_LABEL) == "isolated",
            f"Target namespace must be explicitly labeled {ISOLATION_LABEL}=isolated",
        )
        self.report["target"]["namespace_uid"] = namespace["metadata"]["uid"]
        status = json.loads(self.helm("status", self.args.release, "-o", "json"))
        require(status["info"]["status"] == "deployed", "Start from a deployed A release")
        self.baseline_revision = status["version"]
        self.report["baseline_revision"] = self.baseline_revision
        values = json.loads(self.helm("get", "values", self.args.release, "--all", "-o", "json"))
        require(
            values["urls"]["console"].rstrip("/") == self.args.base.rstrip("/"),
            "--base must match the isolated release's console URL",
        )
        self.postgres = values["postgres"]
        candidate = source_fingerprints(self.args.candidate_source)
        deployed_schema = self.kube("exec", "deployment/gateway", "--", "python", "-c", SCHEMA_HASH)
        require(
            deployed_schema == candidate["schema_sha256"],
            "Candidate changes database models/migration code. This drill requires an unchanged schema.",
        )
        self.report["candidate_source"] = candidate
        self.report["chart_sha256"] = digest(
            {
                str(p.relative_to(self.args.chart)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.args.chart.rglob("*"))
                if p.is_file()
            }
        )
        current = self.helm("get", "manifest", self.args.release)
        # Values are temporary and private; they are never included in the report.
        with tempfile.TemporaryDirectory(prefix="harbor-release-") as directory:
            path = Path(directory) / "values.json"
            path.write_text(json.dumps(values), encoding="utf-8")
            rendered = self.helm(
                "template",
                self.args.release,
                str(self.args.chart),
                "--is-upgrade",
                "-f",
                str(path),
                "--set-string",
                "images.app=" + self.args.b_app,
                "--set-string",
                "images.dashboard=" + self.args.b_dashboard,
            )
        check_image_only_change(current, rendered, self.a_images, self.b_images)
        self.report["image_only_chart_change"] = True
        self.save()

    def workloads(self, images):
        result = {}
        for name in (*APP_WORKLOADS, *DATA_WORKLOADS):
            self.kube("rollout", "status", "deployment/" + name, "--timeout=180s")
            deploy = json.loads(self.kube("get", "deployment/" + name, "-o", "json"))
            metadata, spec, status = deploy["metadata"], deploy["spec"], deploy["status"]
            require(
                metadata.get("annotations", {}).get("meta.helm.sh/release-name")
                == self.args.release,
                f"{name}: deployment does not belong to the selected Helm release",
            )
            replicas = spec.get("replicas", 1)
            require(replicas > 0, f"{name}: deployment is scaled to zero")
            require(
                status.get("observedGeneration", 0) >= metadata["generation"]
                and status.get("updatedReplicas", 0) == replicas
                and status.get("readyReplicas", 0) == replicas
                and status.get("availableReplicas", 0) == replicas,
                f"{name}: deployment is not fully ready",
            )
            pods = [
                p
                for p in json.loads(self.kube("get", "pods", "-l", "app=" + name, "-o", "json"))[
                    "items"
                ]
                if not p["metadata"].get("deletionTimestamp")
            ]
            require(len(pods) == replicas, f"{name}: unexpected live pod count")
            container_name = APP_WORKLOADS.get(name, name)
            pod_records = []
            for pod in pods:
                container = next(
                    c for c in pod["spec"]["containers"] if c["name"] == container_name
                )
                state = next(
                    c for c in pod["status"]["containerStatuses"] if c["name"] == container_name
                )
                require(state["ready"] and state.get("imageID"), f"{name}: container is not ready")
                if name in APP_WORKLOADS:
                    kind = "dashboard" if name == "dashboard" else "app"
                    require(container["image"] == images[kind], f"{name}: unexpected image ref")
                pod_records.append(
                    {
                        "name": pod["metadata"]["name"],
                        "uid": pod["metadata"]["uid"],
                        "image": container["image"],
                        "image_id": state["imageID"],
                    }
                )
            record = {
                "ready_replicas": replicas,
                "pods": pod_records,
                "image_ids": sorted({p["image_id"] for p in pod_records}),
            }
            if name in APP_WORKLOADS:
                hashes = []
                for pod in pod_records:
                    target = "pod/" + pod["name"]
                    if name == "dashboard":
                        payload = self.kube(
                            "exec",
                            target,
                            "--",
                            "sh",
                            "-c",
                            "test -s /usr/share/nginx/html/index.html && "
                            "find /usr/share/nginx/html -type f -exec sha256sum {} \\; "
                            "| sort | sha256sum",
                        ).split()[0]
                        self.kube(
                            "exec",
                            target,
                            "--",
                            "wget",
                            "-q",
                            "-O",
                            "/dev/null",
                            "http://127.0.0.1:8080/health/ready",
                        )
                    else:
                        payload = self.kube("exec", target, "--", "python", "-c", SOURCE_HASH)
                        port = 8000 if name == "gateway" else 8001
                        self.kube(
                            "exec",
                            target,
                            "--",
                            "python",
                            "-c",
                            "import urllib.request; "
                            f"urllib.request.urlopen('http://127.0.0.1:{port}/health/ready',"
                            " timeout=10).read()",
                        )
                    require(re.fullmatch("[a-f0-9]{64}", payload), f"{name}: invalid payload hash")
                    hashes.append(payload)
                require(len(set(hashes)) == 1, f"{name}: replicas run different payloads")
                record["payload_sha256"] = hashes[0]
                if name == "gateway":
                    record["schema_sha256"] = self.kube(
                        "exec", "deployment/gateway", "--", "python", "-c", SCHEMA_HASH
                    )
                    require(
                        record["schema_sha256"] == self.report["candidate_source"]["schema_sha256"],
                        "Running schema source differs from the preflight compatibility check",
                    )
            result[name] = record
        return result

    def data(self):
        pvcs = json.loads(self.kube("get", "pvc", "-o", "json"))["items"]
        volumes = {}
        for name in DATA_WORKLOADS:
            pvc = next(p for p in pvcs if p["metadata"]["name"] == name + "-data")
            require(pvc["status"]["phase"] == "Bound", f"{name}: PVC is not Bound")
            require(
                pvc["metadata"].get("annotations", {}).get("helm.sh/resource-policy") == "keep",
                f"{name}: PVC retention policy must be keep",
            )
            volumes[name] = {
                "uid": pvc["metadata"]["uid"],
                "volume": pvc["spec"]["volumeName"],
                "capacity": pvc["status"]["capacity"],
                "retention": "keep",
            }

        def query(sql):
            return self.kube(
                "exec",
                "deployment/postgres",
                "--",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                self.postgres["user"],
                "-d",
                self.postgres["database"],
                "-Atc",
                sql,
            )

        users = json.loads(
            query(
                "SELECT coalesce(json_agg(t ORDER BY id), '[]') FROM "
                "(SELECT id, role, active FROM users) t;"
            )
        )
        registry = json.loads(
            query(
                "SELECT coalesce(json_agg(t ORDER BY id), '[]') FROM "
                "(SELECT id, slug, url, enabled, auth_type, header_name, credential_key, permissions "
                "FROM upstreams) t;"
            )
        )
        store = json.loads(
            self.kube("exec", "deployment/wordpress", "--", "wp", "eval", STORE_SNAPSHOT)
        )
        require(
            store["products"] and store["orders"], "Seed products and an order before the drill"
        )
        require(store["coming_soon"] == "no", "Publish the isolated store before the drill")
        return {
            "pvcs": volumes,
            "users": users,
            "registry_sha256": digest(registry),
            "schema_versions": query(
                "SELECT version_num FROM alembic_version ORDER BY 1;"
            ).splitlines(),
            "store": store,
        }

    async def authenticate(self):
        credentials = json.loads(self.args.credentials.read_text(encoding="utf-8-sig"))
        for role in ("admin", "member"):
            client, user = await Acceptance(self.args.base).login(
                credentials[role + "_email"], credentials[role + "_password"]
            )
            self.clients.append(client)
            require(user["role"] == role, f"Expected an active {role} credential")
            response = await client.post("/api/keys", json={"name": "Isolated A-B-A release drill"})
            response.raise_for_status()
            key = response.json()
            self.keys.append({"client": client, "id": key["record"]["id"], "key": key["key"]})

    async def protocol(self):
        admin, member = self.clients
        for client in self.clients:
            (await client.get("/api/auth/me")).raise_for_status()
        servers_response = await admin.get("/api/servers")
        servers_response.raise_for_status()
        servers = servers_response.json()
        for slug in ("woo", "policies", "shipping"):
            require(
                any(
                    s["slug"] == slug and s["enabled"] and s["status"] == "Connected"
                    for s in servers
                ),
                f"{slug}: upstream is not Connected",
            )
        # An administrator-only read tests the role boundary without risking a
        # registry write if authorization regresses.
        forbidden = await member.get("/api/members")
        require(forbidden.status_code == 403, "Member accessed the administrator team API")
        async with (
            httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + self.keys[0]["key"]}, trust_env=False
            ) as ah,
            httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + self.keys[1]["key"]}, trust_env=False
            ) as mh,
            Client(
                streamable_http_client(self.args.base + "/mcp", http_client=ah), cache=None
            ) as ac,
            Client(
                streamable_http_client(self.args.base + "/mcp", http_client=mh), cache=None
            ) as mc,
        ):
            admin_names = {tool.name for tool in (await ac.list_tools()).tools}
            member_names = {tool.name for tool in (await mc.list_tools()).tools}
            write = next(n for n in admin_names if "__create_product_" in n)
            require(write not in member_names, "Member discovered an administrator write tool")
            # Empty arguments are invalid even if a regression bypasses authorization,
            # so this negative check cannot accidentally create a real product.
            denied = await mc.call_tool(write, {})
            require(denied.is_error, "Member direct write call was not rejected")
            require(
                "not permitted" in json.dumps(unpack(denied)).lower(),
                "Member write failed for a reason other than authorization",
            )
            results = {}
            for fragment, arguments in (
                ("__get_policy_", {"topic": "shipping"}),
                ("__quote_shipping_", {"subtotal": "1500.00"}),
                ("__list_products_", {}),
                ("__list_orders_", {"period": "all"}),
            ):
                name = next(n for n in member_names if fragment in n)
                response = await mc.call_tool(name, arguments)
                require(not response.is_error, f"{fragment}: MCP call failed")
                results[fragment] = digest(unpack(response))
        return {
            "existing_sessions_valid": True,
            "existing_keys_valid": True,
            "member_admin_api_denied": True,
            "member_direct_write_denied": True,
            "admin_tools": sorted(admin_names),
            "member_tools": sorted(member_names),
            "read_result_sha256": results,
        }

    async def stage(self, name, images):
        record = {
            "helm_revision": json.loads(self.helm("status", self.args.release, "-o", "json"))[
                "version"
            ]
        }
        self.report["stages"][name] = record
        record["workloads"] = self.workloads(images)
        record["protocol"] = await self.protocol()
        record["data"] = self.data()
        self.save()
        return record

    def unchanged(self, baseline, current):
        require(baseline["data"] == current["data"], "Persistent data, permissions or PVCs changed")
        require(
            baseline["protocol"] == current["protocol"], "MCP permissions or read results changed"
        )
        for name in DATA_WORKLOADS:
            require(
                baseline["workloads"][name] == current["workloads"][name],
                f"{name}: a data workload changed during this application-only drill",
            )

    async def execute(self):
        stage = "preflight"
        try:
            self.preflight()
            if not self.args.execute:
                self.report["status"] = "preflight_passed_no_mutations"
                return
            stage = "authentication"
            await self.authenticate()
            stage = "A"
            baseline = await self.stage("A", self.a_images)
            stage = "upgrade"
            require(
                json.loads(self.helm("status", self.args.release, "-o", "json"))["version"]
                == self.baseline_revision,
                "Release revision changed after preflight; refuse to overwrite concurrent work",
            )
            self.upgrade_attempted = True
            self.report["upgrade_attempted"] = True
            self.save()
            self.helm(
                "upgrade",
                self.args.release,
                str(self.args.chart),
                "--reuse-values",
                "--set-string",
                "images.app=" + self.args.b_app,
                "--set-string",
                "images.dashboard=" + self.args.b_dashboard,
                "--no-hooks",
                "--wait",
                "--timeout",
                "15m",
            )
            stage = "B"
            upgraded = await self.stage("B", self.b_images)
            for name in APP_WORKLOADS.keys() - {"dashboard"}:
                require(
                    upgraded["workloads"][name]["payload_sha256"]
                    == self.report["candidate_source"]["payload_sha256"],
                    f"{name}: deployed B payload does not match the preflight candidate source",
                )
            check_transition(baseline["workloads"], upgraded["workloads"])
            self.unchanged(baseline, upgraded)
            self.report["upgrade_verified"] = True
        except (Exception, KeyboardInterrupt) as error:
            self.error(stage, error)
        finally:
            if self.upgrade_attempted:
                try:
                    self.helm(
                        "rollback",
                        self.args.release,
                        str(self.baseline_revision),
                        "--no-hooks",
                        "--wait",
                        "--timeout",
                        "15m",
                    )
                    restored = await self.stage("A_restored", self.a_images)
                    baseline = self.report["stages"]["A"]
                    check_transition(baseline["workloads"], restored["workloads"], rollback=True)
                    self.unchanged(baseline, restored)
                    self.report["rollback_verified"] = True
                except (Exception, KeyboardInterrupt) as error:
                    self.error("rollback", error)
            for key in self.keys:
                try:
                    (await key["client"].delete("/api/keys/" + key["id"])).raise_for_status()
                except Exception as error:
                    self.error("temporary_key_cleanup", error)
            for client in self.clients:
                try:
                    (await client.post("/api/auth/logout")).raise_for_status()
                    await client.aclose()
                except Exception as error:
                    self.error("session_cleanup", error)
            self.report["finished_at_unix"] = int(time.time())
            if self.report["errors"]:
                self.report["status"] = "failed"
            elif self.args.execute:
                self.report["status"] = "passed"
            self.save()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context", "namespace", "release", "base"):
        parser.add_argument("--" + name, required=True)
    for name in ("a-app", "b-app", "a-dashboard", "b-dashboard"):
        parser.add_argument("--" + name, required=True, type=versioned_image)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--chart", type=Path, default=ROOT / "deploy/chart")
    parser.add_argument(
        "--candidate-source",
        type=Path,
        default=ROOT,
        help="Exact B source checkout used to build the app image, for schema/payload verification",
    )
    parser.add_argument("--report", type=Path, default=ROOT / ".runtime/application-release.json")
    local_helm = ROOT / ".tools/helm.exe"
    parser.add_argument("--helm", default=str(local_helm) if local_helm.exists() else "helm")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument(
        "--execute", action="store_true", help="Permit the isolated A -> B -> A drill"
    )
    args = parser.parse_args(argv)
    args.base = args.base.rstrip("/")
    for name in ("helm", "kubectl"):
        require(shutil.which(getattr(args, name)), f"Cannot find {name}; supply --{name}")
    return args


def main():
    drill = Drill(parse_args())
    asyncio.run(drill.execute())
    print(f"{drill.report['status']}: evidence written to {drill.args.report}")
    if drill.report["errors"]:
        for error in drill.report["errors"]:
            print(f"{error['stage']}: {error['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
