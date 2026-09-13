import os
import subprocess
import sys
from pathlib import Path

import iosrealrun
from iosrealrun.resources import packaged_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_iosrealrun_package_importable():
    assert iosrealrun.__version__ == "0.2.0"


def test_console_entrypoint_help():
    entrypoint = Path(sys.executable).with_name("iosrealrun")
    result = subprocess.run(
        [str(entrypoint), "--help"],
        cwd=Path("/tmp"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Simulate an iOS location along a route" in result.stdout


def test_packaged_default_resources_exist():
    assert packaged_path("config.yaml").is_file()
    assert packaged_path("HNroute.txt").is_file()


def test_config_and_default_route_resolve_outside_repository(tmp_path):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    code = """
import config
from init import route

assert config.config.routeConfig.endswith("HNroute.txt")
assert route.get_route()
print(config.config.routeConfig)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "HNroute.txt" in result.stdout
