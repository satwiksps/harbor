"""Prepare private Kubernetes Secret manifests on Linux without printing credentials."""

import argparse
import getpass
import json
import os
import secrets
from pathlib import Path

from local_settings import NAMESPACE, POSTGRES_DATABASE, POSTGRES_USER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default=NAMESPACE)
    parser.add_argument("--postgres-user", default=POSTGRES_USER)
    parser.add_argument("--postgres-database", default=POSTGRES_DATABASE)
    parser.add_argument("--output-dir", type=Path, default=Path(".secrets"))
    parser.add_argument("--admin-email", default="admin@harbor.local")
    parser.add_argument("--wordpress-admin-user", default="storeadmin")
    parser.add_argument("--prompt-gemini", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        args.output_dir.chmod(0o700)
    credential_file = args.output_dir / "development.json"
    if credential_file.exists():
        credentials = json.loads(credential_file.read_text(encoding="utf-8-sig"))
    else:
        if any(args.output_dir.glob("k8s-*-secret.json")):
            parser.error(
                "Secret manifests already exist without development.json. "
                "Restore the private credential file instead of generating replacement passwords."
            )
        credentials = {
            "admin_email": args.admin_email,
            "wordpress_admin_user": args.wordpress_admin_user,
            **{
                name: secrets.token_hex(32)
                for name in (
                    "admin_password",
                    "wordpress_admin_password",
                    "postgres_password",
                    "mariadb_password",
                    "mariadb_root_password",
                    "session_secret",
                    "upstream_woo",
                    "upstream_policies",
                    "upstream_shipping",
                )
            },
            "woo_key": "ck_" + secrets.token_hex(20),
            "woo_secret": "cs_" + secrets.token_hex(20),
        }
        credential_file.write_text(json.dumps(credentials, indent=2), encoding="utf-8")
    gemini_file = args.output_dir / "gemini-api-key.txt"
    if args.prompt_gemini:
        gemini_file.write_text(
            getpass.getpass("Gemini API key (hidden): ").strip(), encoding="utf-8"
        )
    app_keys = (
        "admin_password",
        "wordpress_admin_password",
        "postgres_password",
        "mariadb_password",
        "mariadb_root_password",
        "session_secret",
        "woo_key",
        "woo_secret",
        "upstream_woo",
        "upstream_policies",
        "upstream_shipping",
    )
    app_data = {key: credentials[key] for key in app_keys}
    app_data["database_url"] = (
        "postgresql+psycopg://"
        + args.postgres_user
        + ":"
        + credentials["postgres_password"]
        + "@postgres:5432/"
        + args.postgres_database
    )
    app_data["gemini_api_key"] = gemini_file.read_text().strip() if gemini_file.exists() else ""
    documents = {
        "k8s-app-secret.json": ("harbor-app-secrets", app_data),
        "k8s-upstream-secret.json": (
            "harbor-upstream-credentials",
            {key: credentials["upstream_" + key] for key in ("woo", "policies", "shipping")},
        ),
    }
    for filename, (name, data) in documents.items():
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "Opaque",
            "metadata": {"name": name, "namespace": args.namespace},
            "stringData": data,
        }
        (args.output_dir / filename).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Private manifests prepared in {args.output_dir}. No credentials were printed.")
    print(
        "Preserve this directory. Regenerating database passwords does not rotate existing databases."
    )


if __name__ == "__main__":
    main()
