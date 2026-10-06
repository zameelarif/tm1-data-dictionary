"""'tm1dd run': every step for one or more environments (no TM1 needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from tm1_data_dictionary import cli

ALL = ["bootstrap", "ti", "rules", "elements", "views"]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str | None, str]]:
    """Replace every step with a recorder; returns the (environment, step) calls."""
    seen: list[tuple[str | None, str]] = []

    def make(step: str) -> Any:
        def runner(ctx: click.Context, config: str, env: str | None, quiet: bool) -> None:
            seen.append((env, step))

        return runner

    monkeypatch.setattr(cli, "STEP_RUNNERS", {s: make(s) for s in ALL})
    return seen


def _run(*args: str) -> Any:
    return CliRunner().invoke(cli.main, ["run", *args])


def test_default_runs_every_step_in_order(calls: list) -> None:
    result = _run("--env", "dev")
    assert result.exit_code == 0, result.output
    assert calls == [("dev", s) for s in ALL]
    assert "All steps completed." in result.output


def test_only_keeps_fixed_order(calls: list) -> None:
    result = _run("--env", "dev", "--only", "views,ti")
    assert result.exit_code == 0, result.output
    assert calls == [("dev", "ti"), ("dev", "views")]


def test_skip(calls: list) -> None:
    assert _run("--env", "dev", "--skip", "elements,views").exit_code == 0
    assert [s for _, s in calls] == ["bootstrap", "ti", "rules"]


@pytest.mark.parametrize(
    "args",
    [
        ["--only", "ti", "--skip", "rules"],
        ["--only", "nonsense"],
        ["--skip", ",".join(ALL)],
    ],
)
def test_bad_step_options_are_usage_errors(calls: list, args: list[str]) -> None:
    result = _run("--env", "dev", *args)
    assert result.exit_code == 2
    assert calls == []


def test_several_environments_in_order_without_repeats(calls: list) -> None:
    result = _run("--env", "a", "--env", "b", "--env", "a", "--only", "ti")
    assert result.exit_code == 0, result.output
    assert calls == [("a", "ti"), ("b", "ti")]


def test_no_env_uses_default(calls: list) -> None:
    assert _run("--only", "ti").exit_code == 0
    assert calls == [(None, "ti")]


def test_all_envs_reads_config_order(calls: list, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "environments:\n  zeta:\n    connection:\n      port: 1\n  alpha:\n    connection:\n"
        "      port: 2\n",
        encoding="utf-8",
    )
    result = _run("--config", str(config), "--all-envs", "--only", "views")
    assert result.exit_code == 0, result.output
    assert calls == [("zeta", "views"), ("alpha", "views")]


def test_all_envs_and_env_together_is_an_error(calls: list) -> None:
    assert _run("--all-envs", "--env", "dev").exit_code == 2


def test_failed_step_skips_rest_of_that_env_but_not_next_env(
    calls: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ctx: click.Context, config: str, env: str | None, quiet: bool) -> None:
        calls.append((env, "rules"))
        if env == "a":
            raise click.ClickException("cube read failed")

    monkeypatch.setitem(cli.STEP_RUNNERS, "rules", boom)
    result = _run("--env", "a", "--env", "b")
    assert result.exit_code == 1
    assert [s for e, s in calls if e == "a"] == ["bootstrap", "ti", "rules"]
    assert [s for e, s in calls if e == "b"] == ALL
    assert "SKIPPED" in result.output and "FAILED" in result.output
    assert "cube read failed" in result.output


def test_keep_going_runs_remaining_steps(calls: list, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ctx: click.Context, config: str, env: str | None, quiet: bool) -> None:
        raise RuntimeError("unexpected")

    monkeypatch.setitem(cli.STEP_RUNNERS, "ti", boom)
    result = _run("--env", "a", "--keep-going")
    assert result.exit_code == 1
    assert [s for _, s in calls] == ["bootstrap", "rules", "elements", "views"]
    assert "RuntimeError: unexpected" in result.output


def test_quiet_is_passed_to_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def runner(ctx: click.Context, config: str, env: str | None, quiet: bool) -> None:
        seen.append(quiet)

    monkeypatch.setattr(cli, "STEP_RUNNERS", {s: runner for s in ALL})
    assert _run("--env", "a", "--only", "ti,rules", "--quiet").exit_code == 0
    assert seen == [True, True]


def test_bootstrap_step_stops_when_a_rebuild_is_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Client:
        def __init__(self, cfg: Any) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    class _Check:
        cubes_to_rebuild = ["}Meta_Rule_Cube"]

    invoked: list[Any] = []
    monkeypatch.setattr(cli, "_load", lambda path, env: type("C", (), {"environment": env})())
    monkeypatch.setattr(cli, "TM1Client", _Client)
    monkeypatch.setattr(cli, "check_schema", lambda client: _Check())
    ctx = click.Context(cli.main)
    monkeypatch.setattr(ctx, "invoke", lambda *a, **k: invoked.append(a))
    with pytest.raises(cli.StepFailed, match="--rebuild-cube"):
        cli._step_bootstrap(ctx, "config.yaml", "prod", False)
    assert invoked == []  # never runs bootstrap itself


def test_every_step_is_also_its_own_command() -> None:
    for name in ("bootstrap", "extract", "extract-rules", "extract-elements", "create-views"):
        assert name in cli.main.commands
