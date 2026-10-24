"""Export every }Meta_* cube to CSV, read-only, for building the demo script.

Run from the folder that holds config.yaml, after ``tm1dd run`` and ``tm1dd create-views``:

    python scripts\\export_meta_cubes.py --env dev
    python scripts\\export_meta_cubes.py --env dev --cube "}Meta_Process_Element"

Each cube is read through its public ``tm1dd All`` view - the same view PAfE opens - so
the export matches what you see on screen. A cube without that view falls back to a
generated MDX query (and says so). Writes one CSV per cube into
meta_export_<env>_<timestamp>\\ and zips the folder. Each CSV is in long format: one
column per dimension, then Measure and Value, non-empty cells only.

Nothing in TM1 is written, created or changed. Use the demo model only: the export
contains process, cube and element names.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from tm1_data_dictionary.config import load_config
from tm1_data_dictionary.tm1_client import TM1Client

PREFIX = "}Meta_"
VIEW = "tm1dd All"


def _bracket(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _all(dim: str) -> str:
    return f"{{TM1SUBSETALL({_bracket(dim)}.{_bracket(dim)})}}"


def _parse_unique(name: str) -> tuple[str, str]:
    """``[Dim].[Hier].[Elem]`` -> (Dim, Elem); doubled ]] inside names is handled."""
    parts: list[str] = []
    current: list[str] = []
    inside = False
    i = 0
    while i < len(name):
        ch = name[i]
        if not inside:
            if ch == "[":
                inside = True
                current = []
        elif ch == "]":
            if i + 1 < len(name) and name[i + 1] == "]":
                current.append("]")
                i += 1
            else:
                inside = False
                parts.append("".join(current))
        else:
            current.append(ch)
        i += 1
    return (parts[0], parts[-1]) if parts else ("", name)


def fallback_mdx(cube: str, dims: list[str]) -> str:
    """Every cell of the cube, no suppression (used only when the view is missing)."""
    *rows, measure = dims
    row_set = _all(rows[0])
    for nxt in rows[1:]:
        row_set = f"CROSSJOIN({row_set}, {_all(nxt)})"
    return f"SELECT {_all(measure)} ON 0, {row_set} ON 1 FROM {_bracket(cube)}"


def read_cells(service: Any, cube: str, dims: list[str]) -> tuple[dict, str]:
    """Return (cellset, source) - from the tm1dd All view when it exists."""
    if service.views.exists(cube, VIEW, private=False):
        raw = service.cells.execute_view(cube, VIEW, private=False, cell_properties=["Value"])
        return raw, f"view '{VIEW}'"
    raw = service.cells.execute_mdx(fallback_mdx(cube, dims), cell_properties=["Value"])
    return raw, "generated MDX (no tm1dd All view - run tm1dd create-views)"


def export_cube(service: Any, cube: str, folder: Path) -> tuple[int, str]:
    dims = list(service.cubes.get_dimension_names(cube))
    raw, source = read_cells(service, cube, dims)
    path = folder / (cube.lstrip("}") + ".csv")
    rows = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([d.lstrip("}") for d in dims[:-1]] + ["Measure", "Value"])
        for coords, cell in raw.items():
            value = cell.get("Value") if isinstance(cell, dict) else cell
            if value in (None, "", 0):
                continue
            by_dim = {d.lower(): e for d, e in (_parse_unique(str(c)) for c in coords)}
            keys = [by_dim.get(d.lower(), "") for d in dims[:-1]]
            writer.writerow(keys + [by_dim.get(dims[-1].lower(), ""), value])
            rows += 1
    return rows, source


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--env", default=None, help="Environment in config.yaml.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--cube", action="append", default=None, help="Only these cubes.")
    args = parser.parse_args()

    cfg = load_config(Path(args.config), environment=args.env)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    folder = Path(f"meta_export_{cfg.environment or 'default'}_{stamp}")
    folder.mkdir()
    with TM1Client(cfg) as client:
        service = client.service
        cubes = sorted(c for c in service.cubes.get_all_names() if c.startswith(PREFIX))
        if args.cube:
            wanted = {c.lower() for c in args.cube}
            cubes = [c for c in cubes if c.lower() in wanted]
        for cube in cubes:
            try:
                count, source = export_cube(service, cube, folder)
                print(f"  {cube:<40} {count:>7} cells  ({source})")
            except Exception as exc:  # noqa: BLE001 - one cube must not stop the export
                print(f"  {cube:<40} FAILED: {type(exc).__name__}: {exc}")
    archive = shutil.make_archive(str(folder), "zip", root_dir=folder)
    print(f"\nWrote {archive}")


if __name__ == "__main__":
    main()
