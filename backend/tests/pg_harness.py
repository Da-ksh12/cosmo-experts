"""Disposable local PostgreSQL for tests.

Finds a PostgreSQL server distribution (COSMO_PG_BIN, F:\\pgtools\\pg\\pgsql\\bin, or pg_ctl on PATH), initialises a throw-away cluster that listens ONLY on 127.0.0.1 on a
free port with trust authentication, and removes it at exit. Nothing here can reach a real database: no URL other than the one it creates is ever used. When no
PostgreSQL binaries are present the dependent tests are SKIPPED with an explicit reason; they are never silently passed.

    COSMO_PG_BIN=<dir with pg_ctl, initdb, psql>     COSMO_PG_RUN_DIR=<where to create the cluster, default F:\\pgtools\\run or the system temp>"""
from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path

CANDIDATES = [os.environ.get("COSMO_PG_BIN") or "", r"F:\pgtools\pg\pgsql\bin", "/usr/lib/postgresql/17/bin", "/usr/lib/postgresql/16/bin"]


def find_bin() -> Path | None:
    for c in CANDIDATES:
        if c and (Path(c) / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")).is_file():
            return Path(c)
    w = shutil.which("pg_ctl")
    return Path(w).parent if w else None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class PgServer:
    def __init__(self, bindir: Path):
        self.bin = bindir
        root = Path(os.environ.get("COSMO_PG_RUN_DIR") or (r"F:\pgtools\run" if os.name == "nt" and Path("F:/").exists() else tempfile.gettempdir()))
        root.mkdir(parents=True, exist_ok=True)
        self.dir = root / f"cluster-{uuid.uuid4().hex[:10]}"
        self.port = _free_port()
        self.started = False

    def _exe(self, name: str) -> str:
        return str(self.bin / (name + (".exe" if os.name == "nt" else "")))

    def _run(self, *args, timeout=180) -> int:
        """Run a PostgreSQL tool with output going to a FILE, not a pipe: pg_ctl leaves the server running with inherited handles, and a pipe would never close."""
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        log = self.dir.parent / f"{self.dir.name}.tool.log"
        with open(log, "ab") as f:
            return subprocess.run(list(args), stdout=f, stderr=f, stdin=subprocess.DEVNULL, timeout=timeout).returncode

    def _tail(self) -> str:
        log = self.dir.parent / f"{self.dir.name}.tool.log"
        return log.read_text(errors="replace")[-500:] if log.exists() else ""

    def start(self) -> "PgServer":
        if self._run(self._exe("initdb"), "-D", str(self.dir), "-U", "postgres", "--auth=trust", "-E", "UTF8", "--no-locale"):
            raise RuntimeError("initdb failed: " + self._tail())
        opts = f"-p {self.port} -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=100"
        if self._run(self._exe("pg_ctl"), "-D", str(self.dir), "-o", opts, "-l", str(self.dir / "server.log"), "-w", "start"):
            raise RuntimeError("pg_ctl start failed: " + self._tail())
        self.started = True
        atexit.register(self.stop)
        return self

    def stop(self) -> None:
        if self.started:
            self._run(self._exe("pg_ctl"), "-D", str(self.dir), "-m", "immediate", "stop", timeout=120)
            self.started = False
        shutil.rmtree(self.dir, ignore_errors=True)
        (self.dir.parent / f"{self.dir.name}.tool.log").unlink(missing_ok=True)

    def url(self, db: str = "postgres", user: str = "postgres") -> str:
        return f"postgresql://{user}@127.0.0.1:{self.port}/{db}"

    def run_sql(self, sql: str, db: str = "postgres") -> None:
        import psycopg
        with psycopg.connect(self.url(db), autocommit=True) as c:
            c.execute(sql)

    def new_database(self, prefix: str = "t") -> str:
        """Create an empty database plus the cosmo_app login role (as an operator would) and return the database name."""
        name = f"{prefix}_{uuid.uuid4().hex[:8]}"
        self.run_sql(f'create database "{name}"')
        self.run_sql("do $$ begin if not exists (select 1 from pg_roles where rolname='cosmo_app') then create role cosmo_app login nosuperuser nocreatedb nocreaterole noinherit nobypassrls; end if; end $$;")
        return name

    def drop_database(self, name: str) -> None:
        self.run_sql(f'drop database if exists "{name}" with (force)')


_server: PgServer | None = None


def get_server() -> PgServer | None:
    """The shared per-process server, started on first use; None if no PostgreSQL distribution is available."""
    global _server
    if _server is None:
        b = find_bin()
        if b is None:
            return None
        _server = PgServer(b).start()
    return _server
