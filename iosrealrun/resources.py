"""Helpers for locating resources bundled with the installed application."""

from importlib.resources import files
from pathlib import Path


def packaged_path(name: str) -> Path:
    """Return the filesystem path for a bundled data resource."""
    return Path(files("iosrealrun").joinpath("data", name))
