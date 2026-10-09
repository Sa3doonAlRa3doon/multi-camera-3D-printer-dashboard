"""Multi Camera Printer Dashboard application package."""

from pathlib import Path


def _installed_version() -> str:
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "1.2.0"


__version__ = _installed_version()
