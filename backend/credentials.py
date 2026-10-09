import asyncio
import base64
import json
import os
from pathlib import Path


class CredentialStore:
    """Only this dedicated named Secret is accessible to the gateway service account."""

    def __init__(self, settings):
        self.settings = settings
        self.lock = asyncio.Lock()
        self.api = None
        if settings.secret_backend not in {"file", "kubernetes"}:
            raise ValueError("SECRET_BACKEND must be kubernetes or file")

    def _api(self):
        if self.api is None:
            from kubernetes import client, config

            config.load_incluster_config()
            self.api = client.CoreV1Api()
        return self.api

    def _read_file(self):
        path = Path(self.settings.secret_file)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    async def get(self, key: str | None) -> str | None:
        if not key:
            return None
        if self.settings.secret_backend == "file":
            return self._read_file().get(key)

        def read():
            secret = self._api().read_namespaced_secret(
                self.settings.upstream_credential_secret,
                self.settings.pod_namespace,
                _request_timeout=5,
            )
            value = (secret.data or {}).get(key)
            return base64.b64decode(value).decode() if value else None

        return await asyncio.to_thread(read)

    async def put(self, key: str, value: str | None):
        async with self.lock:
            if self.settings.secret_backend == "file":
                data = self._read_file()
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
                path = Path(self.settings.secret_file)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(data), encoding="utf-8")
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
                return

            def patch():
                from kubernetes.client.exceptions import ApiException

                for attempt in range(3):
                    api = self._api()
                    secret = api.read_namespaced_secret(
                        self.settings.upstream_credential_secret,
                        self.settings.pod_namespace,
                        _request_timeout=5,
                    )
                    encoded = (
                        base64.b64encode(value.encode()).decode() if value is not None else None
                    )
                    try:
                        api.patch_namespaced_secret(
                            self.settings.upstream_credential_secret,
                            self.settings.pod_namespace,
                            {
                                "metadata": {"resourceVersion": secret.metadata.resource_version},
                                "data": {key: encoded},
                            },
                            _request_timeout=5,
                        )
                        return
                    except ApiException as exc:
                        if exc.status != 409 or attempt == 2:
                            raise

            # Cancelling to_thread only cancels its awaiter; the SDK write and its
            # conflict retries keep running. Drain that worker while both this lock
            # and the caller's registry lock are held, so a later edit cannot be
            # overwritten by an older cancelled request. SDK calls retain their
            # five-second timeouts and the bounded three-attempt conflict retry.
            worker = asyncio.create_task(asyncio.to_thread(patch))
            cancelled = False
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                if not worker.cancelled():
                    worker.exception()  # Retrieve failures without masking cancellation.
                raise asyncio.CancelledError
            worker.result()
