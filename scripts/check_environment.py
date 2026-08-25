"""TM1 Data Dictionary — environment diagnostic.

Runs five checks and prints a clear pass/fail summary. Fix red rows before
starting real development.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tm1_data_dictionary.credentials import default_provider

console = Console()
_results: list[tuple[str, str, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, "PASS" if ok else "FAIL", detail))
    style = "green" if ok else "red"
    icon = "PASS" if ok else "FAIL"
    console.print(f"[{style}]{icon} {name}[/{style}] — {detail}")


def check_python_version() -> bool:
    v = sys.version_info
    ok = v.major == 3 and v.minor >= 10
    _record("Python version", ok, f"{v.major}.{v.minor}.{v.micro} (need 3.10+)")
    return ok


def check_config_files() -> dict | None:
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    cfg_path = root / "config.yaml"
    if not cfg_path.exists():
        _record("Config files", False, "config.yaml not found (copy from config.yaml.example)")
        return None
    with open(cfg_path) as f:
        cfg: dict = yaml.safe_load(f) or {}
    needed_env = ["TM1_ADDRESS", "TM1_PORT", "TM1_USER"]
    missing = [v for v in needed_env if not os.getenv(v)]
    if missing:
        _record("Config files", False, f"missing env vars: {', '.join(missing)}")
        return None
    _record("Config files", True, "config.yaml + .env loaded")
    return cfg


def check_tm1_connection():  # noqa: ANN201
    try:
        from TM1py.Services import TM1Service
    except ImportError as e:
        _record("TM1py import", False, str(e))
        return None
    try:
        tm1 = TM1Service(
            address=os.getenv("TM1_ADDRESS"),
            port=int(os.getenv("TM1_PORT", "8010")),
            ssl=os.getenv("TM1_SSL", "true").lower() == "true",
            user=os.getenv("TM1_USER"),
            password=default_provider().get_secret("TM1_METADICT_PWD"),
            namespace=os.getenv("TM1_NAMESPACE") or None,
        )
        t0 = time.time()
        server_name = tm1.server.get_server_name()
        version = tm1.server.get_product_version()
        elapsed = int((time.time() - t0) * 1000)
        _record(
            "TM1 connection",
            True,
            f"{server_name} v{version} — {elapsed}ms round trip",
        )
        return tm1
    except Exception as e:  # noqa: BLE001
        _record("TM1 connection", False, f"{type(e).__name__}: {e}")
        return None


def check_tm1_permissions(tm1) -> None:  # noqa: ANN001
    if tm1 is None:
        _record("TM1 permissions", False, "skipped — no connection")
        return
    try:
        from TM1py.Objects import Dimension, Element, Hierarchy

        proc_count = len(tm1.processes.get_all_names())
        cube_count = len(tm1.cubes.get_all_names())

        dim_name = "}Meta_ConnCheck_Scratch"
        hier = Hierarchy(
            name=dim_name,
            dimension_name=dim_name,
            elements=[Element("Test", "Numeric")],
        )
        dim = Dimension(name=dim_name, hierarchies=[hier])

        if tm1.dimensions.exists(dim_name):
            tm1.dimensions.delete(dim_name)
        tm1.dimensions.create(dim)
        tm1.dimensions.delete(dim_name)

        _record(
            "TM1 permissions",
            True,
            f"read {proc_count} processes, {cube_count} cubes; write test OK",
        )
    except Exception as e:  # noqa: BLE001
        _record("TM1 permissions", False, f"{type(e).__name__}: {e}")


def check_log_access() -> None:
    log_path = os.getenv("TM1_LOG_PATH")
    if not log_path:
        _record("Log file access", False, "TM1_LOG_PATH not set in .env")
        return
    p = Path(log_path)
    if not p.exists():
        _record("Log file access", False, f"not found: {log_path}")
        return
    try:
        size_mb = p.stat().st_size / (1024 * 1024)
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - 1024))
            f.read(1024).decode("utf-8", errors="replace")
        _record(
            "Log file access",
            True,
            f"{log_path} — {size_mb:.1f} MB, last KB readable",
        )
    except Exception as e:  # noqa: BLE001
        _record("Log file access", False, str(e))


def _summary() -> int:
    console.print()
    tbl = Table(title="Environment check summary", show_lines=True)
    tbl.add_column("Check", style="bold")
    tbl.add_column("Status")
    tbl.add_column("Detail", overflow="fold")
    all_ok = True
    for name, status, detail in _results:
        style = "green" if status == "PASS" else "red"
        tbl.add_row(name, f"[{style}]{status}[/{style}]", detail)
        if status == "FAIL":
            all_ok = False
    console.print(tbl)
    if all_ok:
        console.print(
            Panel.fit(
                "[bold green]All checks passed. Ready to start building.[/bold green]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                "[bold red]One or more checks failed. Fix and re-run.[/bold red]",
                border_style="red",
            )
        )
    return 0 if all_ok else 1


def main() -> int:
    console.print(
        Panel.fit(
            "[bold]TM1 Data Dictionary — Environment Check[/bold]",
            border_style="cyan",
        )
    )
    if not check_python_version():
        return _summary()
    cfg = check_config_files()
    if cfg is None:
        return _summary()
    tm1 = check_tm1_connection()
    check_tm1_permissions(tm1)
    check_log_access()
    if tm1:
        tm1.logout()
    return _summary()


if __name__ == "__main__":
    sys.exit(main())
