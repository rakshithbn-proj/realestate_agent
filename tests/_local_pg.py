"""Throwaway Postgres from portable binaries — the Windows fallback for local
tests (pgserver has no Windows wheels).

Resolution order for the binaries: ATLAS_PG_BIN env var → initdb on PATH →
<repo>/.pgbin/pgsql/bin (drop the EDB "windows-x64-binaries" zip there; the
directory is gitignored). Needs stock Postgres 16 only — the Phase-0 schema
deliberately requires just pg_trgm, which is bundled contrib.
"""
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_EXE = ".exe" if sys.platform == "win32" else ""


def find_pg_bin() -> Path | None:
    env = os.environ.get("ATLAS_PG_BIN")
    if env and (Path(env) / f"initdb{_EXE}").exists():
        return Path(env)
    on_path = shutil.which("initdb")
    if on_path:
        return Path(on_path).parent
    local = REPO_ROOT / ".pgbin" / "pgsql" / "bin"
    if (local / f"initdb{_EXE}").exists():
        return local
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalPostgres:
    def __init__(self, bindir: Path, datadir: Path):
        self.bindir = bindir
        self.datadir = datadir
        self.port: int | None = None

    def start(self) -> str:
        subprocess.run(
            [str(self.bindir / f"initdb{_EXE}"), "-D", str(self.datadir),
             "-U", "postgres", "-A", "trust", "-E", "UTF8",
             "--no-locale"],
            check=True, capture_output=True, text=True,
        )
        self.port = _free_port()
        # No capture_output here: the spawned postmaster inherits the pipes,
        # so waiting for pipe EOF would hang forever (notably on Windows).
        subprocess.run(
            [str(self.bindir / f"pg_ctl{_EXE}"), "-D", str(self.datadir),
             "-w", "-l", str(self.datadir / "server.log"),
             "-o", f'-p {self.port} -c listen_addresses=127.0.0.1',
             "start"],
            check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return f"postgresql+psycopg://postgres@127.0.0.1:{self.port}/postgres"

    def stop(self) -> None:
        subprocess.run(
            [str(self.bindir / f"pg_ctl{_EXE}"), "-D", str(self.datadir),
             "-m", "fast", "stop"],
            check=False, capture_output=True, text=True,
        )
