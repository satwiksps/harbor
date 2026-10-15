"""Live verification through Inspector and the standalone examples/ask_gateway.py agent."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".secrets" / "external-client"
REPORT = ROOT / ".runtime" / "external-agent-report.json"
INSPECTOR_VERSION = "2.11.0"


def redact(value, sensitive):
    encoded = json.dumps(value, ensure_ascii=False)
    for secret in sensitive:
        if secret:
            encoded = encoded.replace(secret, "[REDACTED]")
    encoded = re.sub(
        r"AIza[A-Za-z0-9_-]{20,}|(?:harbor|cc)_[A-Za-z0-9_-]{30,}", "[REDACTED]", encoded
    )
    return json.loads(encoded.replace(chr(0x2014), "; "))


def inspector_command():
    package = (
        ROOT / ".tools" / "external-client" / "node_modules" / "@modelcontextprotocol" / "inspector"
    )
    metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
    if metadata["version"] != INSPECTOR_VERSION:
        raise RuntimeError("Install the pinned MCP Inspector version")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for MCP Inspector")
    return [node, str(package / metadata["bin"]["mcp-inspector"])]


def child_environment():
    keep = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "LC_ALL",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    environment.update(
        {
            "CI": "true",
            "NO_COLOR": "1",
            "PYTHONUTF8": "1",
            "MCP_INSPECTOR_SECRET_STORE": "memory",
            "MCP_STORAGE_DIR": str(WORK / "inspector-state"),
            "NODE_OPTIONS": "--dns-result-order=ipv4first",
        }
    )
    return environment


def run_process(command, environment, timeout):
    process = subprocess.Popen(
        command,
        cwd=WORK,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        if os.name == "nt"
        else 0,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=10)
        raise
    return {"return_code": process.returncode, "stdout": stdout, "stderr": stderr}


def read_inspector(result):
    if result["return_code"]:
        raise RuntimeError("MCP Inspector failed: " + result["stderr"][-1500:])
    value = json.loads(result["stdout"])
    return value.get("result", value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://harbor.localhost:8080")
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--inspector-only", action="store_true")
    parser.add_argument(
        "--order-id", type=int, help="Also inspect and quote shipping for this real order"
    )
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    inspector = inspector_command()
    if args.prepare_only:
        print("Pinned Inspector and standalone agent are ready; no credentials or API calls used.")
        return 0
    member = json.loads((ROOT / ".secrets" / "demo-member.json").read_text(encoding="utf-8-sig"))
    gemini_key = (
        ""
        if args.inspector_only
        else (ROOT / ".secrets" / "gemini-api-key.txt").read_text(encoding="utf-8-sig").strip()
    )
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "gateway": args.base.rstrip("/") + "/mcp",
        "member_id": member["id"],
        "versions": {
            "inspector": INSPECTOR_VERSION,
            "agent": "examples/ask_gateway.py",
            "mcp_sdk": "2.3.0",
            "google_genai": "2.31.0",
        },
        "model": args.model,
        "checks": [],
        "external_agent_attempted": not args.inspector_only,
    }
    key_id, token = None, ""
    config_path = WORK / "inspector.json"
    sensitive = [member["password"], gemini_key]
    environment = child_environment()
    try:
        with httpx.Client(
            base_url=args.base,
            timeout=20,
            trust_env=False,
            headers={"Origin": args.base.rstrip("/")},
        ) as http:
            login = http.post(
                "/api/auth/login", json={"email": member["email"], "password": member["password"]}
            )
            login.raise_for_status()
            identity = login.json()
            if identity["user"]["role"] != "member" or identity["user"]["id"] != member["id"]:
                raise RuntimeError("Expected the read-only demonstration member")
            http.headers["X-CSRF-Token"] = identity["csrf_token"]
            try:
                issued = http.post("/api/keys", json={"name": "External client acceptance"})
                issued.raise_for_status()
                token, key_id = issued.json()["key"], issued.json()["record"]["id"]
                sensitive.append(token)
                config_path.write_text(
                    json.dumps(
                        {
                            "mcpServers": {
                                "harbor": {
                                    "type": "http",
                                    "url": report["gateway"],
                                    "protocolEra": "auto",
                                    "headers": {"Authorization": "Bearer " + token},
                                    "connectionTimeout": 15000,
                                    "requestTimeout": 25000,
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                os.chmod(config_path, 0o600)
                command = inspector + [
                    "--cli",
                    "--config",
                    str(config_path),
                    "--server",
                    "harbor",
                    "--format",
                    "json",
                    "--quiet",
                    "--stored-auth-only",
                ]
                listing = read_inspector(
                    run_process(command + ["--method", "tools/list"], environment, 45)
                )
                names = [tool["name"] for tool in listing["tools"]]
                if not all(
                    any(name.startswith(slug + "__") for name in names)
                    for slug in ("woo", "policies", "shipping")
                ):
                    raise RuntimeError("Inspector did not discover all three upstreams")
                if any("create_product" in name or "update_order_status" in name for name in names):
                    raise RuntimeError("A write tool was visible to the member")
                report["visible_tools"] = names
                report["checks"].append(
                    {"name": "Inspector discovers three upstreams with member key", "passed": True}
                )
                orders_tool = next(name for name in names if "__list_orders_" in name)
                orders = read_inspector(
                    run_process(
                        command
                        + [
                            "--method",
                            "tools/call",
                            "--tool-name",
                            orders_tool,
                            "--tool-args-json",
                            json.dumps({"period": "today", "per_page": 5}),
                        ],
                        environment,
                        45,
                    )
                )
                if orders.get("isError"):
                    raise RuntimeError("Inspector order tool failed")
                report["checks"].append(
                    {"name": "Inspector calls today's orders successfully", "passed": True}
                )
                config_path.unlink(missing_ok=True)
                if args.inspector_only:
                    report["passed"] = True
                else:
                    before_response = http.get("/api/audit?limit=200")
                    before_response.raise_for_status()
                    before = {row["id"] for row in before_response.json()["items"]}
                    environment.update(
                        {
                            "GEMINI_API_KEY": gemini_key,
                            "GATEWAY_API_KEY": token,
                            "GATEWAY_URL": report["gateway"],
                        }
                    )
                    prompt = "Read today's WooCommerce orders and the shipping policy. Call both upstreams even if orders are empty. Summarize the orders and explain which meet the free-shipping threshold. Cite actual order IDs and policy version. Do not change anything."
                    if args.order_id:
                        prompt += f" Inspect order {args.order_id} and quote its shipping using the actual subtotal and destination from that order. Include the quote and its rate-table version."
                    report["prompt"] = prompt
                    result = run_process(
                        [
                            sys.executable,
                            str(ROOT / "examples" / "ask_gateway.py"),
                            "--model",
                            args.model,
                            "--prompt",
                            prompt,
                        ],
                        environment,
                        150,
                    )
                    report["agent_exit_code"] = result["return_code"]
                    report["agent_stderr"] = result["stderr"][-2000:]
                    try:
                        report["agent_result"] = json.loads(result["stdout"])
                    except ValueError:
                        report["agent_result"] = {"passed": False, "error": "Invalid agent output"}
                    audit_response = http.get("/api/audit?limit=200")
                    audit_response.raise_for_status()
                    rows = [
                        row for row in audit_response.json()["items"] if row["id"] not in before
                    ]
                    report["audit_evidence"] = [
                        {
                            field: row[field]
                            for field in (
                                "id",
                                "user_id",
                                "server_name",
                                "tool_name",
                                "timestamp",
                                "outcome",
                            )
                        }
                        for row in rows
                    ]
                    successful = {row["server_name"] for row in rows if row["outcome"] == "success"}
                    if (
                        result["return_code"]
                        or not report["agent_result"].get("passed")
                        or len(successful) < 2
                        or any(row["user_id"] != member["id"] for row in rows)
                    ):
                        raise RuntimeError(
                            "Independent agent did not complete the two-upstream prompt with member audit attribution"
                        )
                    report["checks"].append(
                        {
                            "name": "Independent real Gemini agent calls two upstreams as user B",
                            "passed": True,
                        }
                    )
                    report["passed"] = True
            finally:
                if key_id:
                    revoked = http.delete(f"/api/keys/{key_id}")
                    report["temporary_key_revoked"] = revoked.is_success
                    if not revoked.is_success:
                        report["passed"] = False
                http.post("/api/auth/logout")
    except subprocess.TimeoutExpired:
        report["error"] = (
            "External command exceeded its bounded timeout; no key rotation or automatic retry was attempted"
        )
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        config_path.unlink(missing_ok=True)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        REPORT.write_text(
            json.dumps(redact(report, sensitive), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    for check in report["checks"]:
        print("PASS", check["name"])
    print(
        "PASS" if report["passed"] else "FAIL",
        "external verification; see .runtime/external-agent-report.json",
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("External client setup failed:", type(exc).__name__)
        sys.exit(1)
