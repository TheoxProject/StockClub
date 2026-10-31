"""Run current application tests without dotenv, production data or outbound access.

Usage: python -I tools/run_isolated_tests.py [--engine postgresql] [pytest args]
PostgreSQL needs .test-artifacts/postgres-test.json from the local test cluster.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("sqlite", "postgresql"), default="sqlite")
    options, pytest_args = parser.parse_known_args()
    runtime = ROOT / ".test-artifacts" / ("run_" + uuid.uuid4().hex[:12])
    runtime.mkdir(parents=True)
    database_url = ""
    pg_port = None
    if options.engine == "postgresql":
        config = json.loads((ROOT / ".test-artifacts/postgres-test.json").read_text())
        if config.get("purpose") != "stockclub-isolated-tests":
            raise SystemExit("Refusing an unmarked PostgreSQL configuration")
        from urllib.parse import urlparse
        parsed = urlparse(config["url"])
        if parsed.hostname != "127.0.0.1" or not parsed.path.startswith("/stockclub_test_"):
            raise SystemExit("Only the dedicated loopback test database is allowed")
        database_url, pg_port = config["url"], parsed.port

    # Never inherit application, cloud or database credentials from this process.
    keep = {key: os.environ[key] for key in
            ("SystemRoot", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP") if key in os.environ}
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update({
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "AWS_EC2_METADATA_DISABLED": "true",
        "DATABASE_URL": "sqlite:///" + (runtime / "fallback.db").as_posix(),
        "SECRET_KEY": "fictitious-test-secret-never-for-deployment-123456",
        "FLASK_DEBUG": "0", "COOKIE_SECURE": "0", "REQUIRE_OBJECT_STORAGE": "0",
        "TRUST_PROXY": "0", "S3_BUCKET": "", "S3_ENDPOINT_URL": "",
        "S3_ACCESS_KEY_ID": "", "S3_SECRET_ACCESS_KEY": "", "S3_PUBLIC_BASE_URL": "",
        "AWS_CONFIG_FILE": str(runtime / "no-aws-config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(runtime / "no-aws-credentials"),
    })
    if database_url:
        os.environ["STOCKCLUB_TEST_DATABASE_URL"] = database_url

    def guard(event, args):
        if event == "socket.connect":
            address = args[1]
            if not (isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}
                    and pg_port is not None and address[1] == pg_port):
                raise RuntimeError("Test isolation: outbound connection refused")
        if event == "sqlite3.connect" and args and str(args[0]) != ":memory:":
            if not Path(args[0]).resolve().is_relative_to(runtime):
                raise RuntimeError("Test isolation: SQLite outside runtime refused")
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.name.startswith(".env") or path.is_relative_to(ROOT / "instance"):
                raise RuntimeError("Test isolation: secret/original instance read refused")

    sys.addaudithook(guard)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    from flask import Flask
    original_init = Flask.__init__

    def isolated_init(self, *args, **kwargs):
        kwargs.setdefault("instance_path", str(runtime / "instance"))
        if kwargs["instance_path"] is None:
            kwargs["instance_path"] = str(runtime / "instance")
        original_init(self, *args, **kwargs)

    Flask.__init__ = isolated_init
    import pytest
    junit = runtime / "results.xml"
    print(f"ISOLATED_TESTS engine={options.engine}; current source={ROOT}; artifacts={runtime}", flush=True)
    result = int(pytest.main(["-p", "no:cacheprovider", "--basetemp", str(runtime / "tmp"),
                             "--junitxml", str(junit), *(pytest_args or ["tests", "-q"])]))
    (runtime / "result.json").write_text(json.dumps({
        "engine": options.engine, "exit_code": result, "junit": str(junit),
        "source": str(ROOT), "dotenv": "disabled", "network": "dedicated PostgreSQL only",
    }, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
