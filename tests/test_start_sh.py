import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def test_start_sh_valid_syntax():
    """Test that start.sh has valid shell syntax."""
    result = subprocess.run(
        ["sh", "-n", "scripts/start.sh"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
    )
    assert result.returncode == 0, f"Syntax check failed: {result.stderr.decode()}"


def test_start_sh_unset_brain_type():
    """Test that start.sh exits non-zero when BRAIN_TYPE is unset."""
    env = os.environ.copy()
    env.pop("BRAIN_TYPE", None)  # Ensure it's unset

    result = subprocess.run(
        ["sh", "scripts/start.sh"],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, "Should fail when BRAIN_TYPE is unset"


def test_start_sh_bogus_brain_type():
    """Test that start.sh exits non-zero with invalid BRAIN_TYPE."""
    env = os.environ.copy()
    env["BRAIN_TYPE"] = "bogus"

    result = subprocess.run(
        ["sh", "scripts/start.sh"],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0, "Should fail with unknown BRAIN_TYPE"
    # Verify it fails at the directory check, before reaching alembic/uvicorn
    assert b"unknown BRAIN_TYPE" in result.stderr, (
        f"Error message should mention unknown BRAIN_TYPE, got: {result.stderr.decode()}"
    )
