"""The interpreter version is written once, in `.python-version`, and held everywhere else.

`uv venv` and `uv sync` read `.python-version` with no flag, and `actions/setup-python` reads it
through `python-version-file:`. So a build worktree and CI resolve one number from one file
instead of agreeing by hand. The sites that cannot read the file are held to it by the checks
below. Modelled on the orchestrator's `tests/architecture/test_interpreter_agreement.py`,
which records why each check has the shape it has.

ruff's `target-version` is held to EQUALITY. At 3.14 ruff writes PEP 758 `except A, B:`, which
is a SyntaxError before 3.14. A target below the pin would stop that rewrite and claim an older
interpreter can run the tree; a target above it would write syntax the image cannot parse.
"""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / ".python-version"
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOWS = ROOT / ".github" / "workflows"
DOCKERFILE = ROOT / "Dockerfile"

MAJOR_MINOR = re.compile(r"^\d+\.\d+$")
BARE_FLOOR = re.compile(r"^>=\d+\.\d+$")


def pinned_version() -> str:
    return VERSION_FILE.read_text().strip()


def test_the_version_file_holds_exactly_one_bare_major_minor() -> None:
    assert MAJOR_MINOR.match(pinned_version()), pinned_version()


def test_ruff_targets_the_pinned_version() -> None:
    """This repository has no [project] table and so no requires-python; ruff's target is named
    in [tool.ruff] instead. Without it ruff falls back to its own default target."""
    ruff = tomllib.loads(PYPROJECT.read_text())["tool"]["ruff"]
    assert ruff.get("target-version") == "py" + pinned_version().replace(".", ""), (
        f"[tool.ruff] target-version is {ruff.get('target-version')!r}; expected the "
        f"pinned {pinned_version()}"
    )


def test_both_dockerfile_base_tags_name_the_pinned_version() -> None:
    tags = re.findall(r"^FROM python:(\S+?)-slim", DOCKERFILE.read_text(), re.MULTILINE)
    assert len(tags) == 2, f"expected a builder and a runtime stage, found {tags}"
    assert set(tags) == {pinned_version()}, (
        f"Dockerfile base tags {sorted(set(tags))} disagree with .python-version"
    )


def test_pyright_checks_the_pinned_version() -> None:
    pyright = tomllib.loads(PYPROJECT.read_text())["tool"]["pyright"]
    assert pyright.get("pythonVersion") == pinned_version(), (
        "[tool.pyright] pythonVersion must equal .python-version; do not delete the key -- "
        "without it pyright assumes whatever python3 is on PATH"
    )


def _workflows() -> list[Path]:
    return sorted(p for suffix in ("*.yml", "*.yaml") for p in WORKFLOWS.glob(suffix))


def _jobs() -> list[tuple[Path, dict]]:
    return [
        (path, job)
        for path in _workflows()
        for job in (yaml.safe_load(path.read_text()) or {}).get("jobs", {}).values()
    ]


def test_every_setup_python_step_reads_the_version_file() -> None:
    steps = [
        (path, step)
        for path, job in _jobs()
        for step in job.get("steps", []) or []
        if str(step.get("uses", "")).startswith("actions/setup-python")
    ]
    assert steps, "no setup-python steps found; this guard would pass vacuously"
    for path, step in steps:
        where = f"{path.name}: {step.get('name', step['uses'])}"
        assert step.get("with", {}).get("python-version-file") == ".python-version", where
        assert "python-version" not in step.get("with", {}), where
        assert not re.search(r"\d", str(step.get("name", ""))), f"{where}: version in label"


# `python`/`python3` in a `run:` block is the RUNNER's interpreter unless a setup-python step
# ran earlier in the same job. `.venv/bin/python`, `uv run` and `uvx` resolve through
# `.python-version` themselves, and `docker` names another machine's interpreter.
#
# A job with no `actions/checkout` is exempt: it has no copy of this repository, so its
# `python3` cannot be running this repository's code. ci.yml's `deploy` job is that case. It
# parses JSON responses with `python3 -c` and the standard library. Adding a setup step there
# would move the rollout workflow's blob, which the orchestrator transcribes and deploy policy
# pins, and it would protect nothing.
RUNNER_PYTHON = re.compile(r"(?<![\w.-])python(?:3(?:\.\d+)?)?(?![\w:.-])")
DERIVES_ITS_OWN = re.compile(r"(?<![\w-])(uvx?|docker)(?![\w-])|\.venv/bin/$")
COMMAND_SEPARATOR = re.compile(r"&&|\|\||[;|]")


def runner_python_lines(script: str) -> list[str]:
    found = []
    for line in script.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for command in COMMAND_SEPARATOR.split(line):
            match = RUNNER_PYTHON.search(command)
            if match and not DERIVES_ITS_OWN.search(command[: match.start()]):
                found.append(line.strip())
                break
    return found


def test_no_job_runs_runner_python_before_setting_it_up() -> None:
    offenders = []
    scanned = 0
    for path, job in _jobs():
        steps = job.get("steps", []) or []
        if not any(str(step.get("uses", "")).startswith("actions/checkout") for step in steps):
            continue
        prepared = False
        for step in steps:
            if str(step.get("uses", "")).startswith("actions/setup-python"):
                prepared = True
                continue
            run = str(step.get("run", "") or "")
            if not run:
                continue
            scanned += 1
            if not prepared:
                offenders += [f"{path.name}: {line}" for line in runner_python_lines(run)]
    assert scanned, "no `run:` steps were scanned; this guard would pass vacuously"
    assert not offenders, "runner python with no setup-python before it:\n  " + "\n  ".join(
        offenders
    )


def test_the_line_classifier() -> None:
    for line in (
        "python3 scripts/x.py",
        'X=$(python3 -c "1")',
        "uv sync && python3 x.py",
        "/usr/bin/python3 x.py",
    ):
        assert runner_python_lines(line), line
    for line in (".venv/bin/python -m pytest", "uv run python x.py", "uvx ruff check ."):
        assert not runner_python_lines(line), line
