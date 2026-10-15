"""Exercise ten real concurrent password checks in a disposable 512 MiB gateway.

Build harbor:dev first. This creates only synthetic local data, uses
an already built image, and removes its own container even when a check fails.
"""

import argparse
import json
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def docker(*arguments):
    return subprocess.run(
        ["docker", *arguments], check=True, capture_output=True, text=True, timeout=60
    ).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="harbor:dev")
    args = parser.parse_args()
    name = "harbor-auth-budget-" + secrets.token_hex(4)
    created = False
    try:
        docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--publish",
            "127.0.0.1::8000",
            "--env",
            "DATABASE_URL=sqlite:////tmp/auth-budget.db",
            "--env",
            "TESTING=true",
            "--env",
            "SECRET_BACKEND=file",
            "--env",
            "SECRET_FILE=/tmp/upstream-credentials.json",
            "--env",
            "SESSION_SECRET=" + secrets.token_urlsafe(48),
            "--env",
            "BOOTSTRAP_ADMIN_EMAIL=budget@example.test",
            "--env",
            "BOOTSTRAP_ADMIN_PASSWORD=" + secrets.token_urlsafe(24),
            args.image,
        )
        created = True
        container = json.loads(docker("inspect", name))[0]
        port = container["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
        base = "http://127.0.0.1:" + port
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, body=None):
            req = urllib.request.Request(
                base + path,
                data=json.dumps(body).encode() if body else None,
                headers={"Content-Type": "application/json"},
            )
            try:
                with opener.open(req, timeout=5) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        for _ in range(100):
            try:
                if request("/health/ready") == 200:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError("Disposable gateway did not become ready")

        barrier = threading.Barrier(11)

        def login():
            barrier.wait(timeout=10)
            return request(
                "/api/auth/login",
                {"email": "budget@example.test", "password": "incorrect-password"},
            )

        latencies = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            attempts = [executor.submit(login) for _ in range(10)]
            barrier.wait(timeout=10)
            while not all(attempt.done() for attempt in attempts):
                started = time.monotonic()
                assert request("/health/live") == 200
                latencies.append(time.monotonic() - started)
                time.sleep(0.02)
            statuses = [attempt.result(timeout=5) for attempt in attempts]
        assert set(statuses) <= {401, 503}, statuses
        assert 503 in statuses and 401 in statuses, statuses
        assert max(latencies, default=0) < 2, latencies
        peak = int(docker("exec", name, "cat", "/sys/fs/cgroup/memory.peak"))
        state = json.loads(docker("inspect", name))[0]["State"]
        assert state["Running"] and not state["OOMKilled"]
        assert peak < 512 * 1024 * 1024
        print(
            json.dumps(
                {
                    "passed": True,
                    "concurrent_requests": len(statuses),
                    "invalid_password_responses": statuses.count(401),
                    "overload_responses": statuses.count(503),
                    "peak_memory_mib": round(peak / 1024 / 1024, 2),
                    "maximum_health_latency_ms": round(max(latencies, default=0) * 1000, 2),
                    "oom_killed": state["OOMKilled"],
                },
                indent=2,
            )
        )
    finally:
        if created:
            docker("rm", "--force", name)


if __name__ == "__main__":
    main()
