from pathlib import Path

import yaml

from iosrealrun.resources import packaged_path


def _config_path() -> Path:
    """Use a checkout config when present, otherwise the bundled config."""
    source_config = Path(__file__).with_name("config.yaml")
    if source_config.is_file():
        return source_config
    return packaged_path("config.yaml")


def _route_path(value: str) -> str:
    route_path = Path(value).expanduser()
    if route_path.is_absolute() or route_path.is_file():
        return str(route_path)
    bundled_route = packaged_path(route_path.name)
    if bundled_route.is_file():
        return str(bundled_route)
    return str(route_path)


class Config:
    def __init__(self):
        with _config_path().open(encoding="utf-8") as f:
            config = yaml.safe_load(f)
        for i in config:
            setattr(self, i, config[i])
        self.routeConfig = _route_path(self.routeConfig)


config = Config()
