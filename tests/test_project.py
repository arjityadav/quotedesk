"""Project setup: Python version, packaging, dependencies, deploy files."""

import sys
import tomllib
from pathlib import Path

import quotedesk

ROOT = Path(__file__).resolve().parents[1]


def test_python_version() -> None:
    assert sys.version_info >= (3, 12)


def test_package_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert quotedesk.__version__ == pyproject["project"]["version"]


def test_dependencies_importable() -> None:
    import lxml  # noqa: F401
    import pydantic  # noqa: F401


def test_compose_file_present() -> None:
    assert (ROOT / "deploy" / "compose" / "compose.yaml").is_file()
