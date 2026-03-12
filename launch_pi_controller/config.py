from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DisplayConfig:
    width: int = 1920
    height: int = 480
    rotation: int = 90
    fullscreen: bool = False
    fps: int = 60
    hide_mouse: bool = False


@dataclass(slots=True)
class PreviewConfig:
    bind_host: str = "0.0.0.0"
    port: int = 6454
    net: int = 0
    cols: int = 32
    rows: int = 16
    use_sync: bool = True
    render_mode: str = "status"


@dataclass(slots=True)
class MidiDeviceConfig:
    name: str = "launchcontrol_effect"
    bind_host: str = "0.0.0.0"
    target_host: str = "127.0.0.1"
    port: int = 21931
    channel: int = 0
    heartbeat_name: str = "launchcontrol_9"
    heartbeat_target_host: str = "127.0.0.1"
    heartbeat_port: int = 22990
    heartbeat_interval_s: float = 2.0
    send_heartbeat: bool = True


@dataclass(slots=True)
class AppConfig:
    display: DisplayConfig = field(default_factory=DisplayConfig)
    preview: PreviewConfig = field(default_factory=PreviewConfig)
    effect_device: MidiDeviceConfig = field(default_factory=MidiDeviceConfig)


DEFAULT_CONFIG_NAME = "config.json"


def default_config_path(base_dir: Path) -> Path:
    return base_dir / DEFAULT_CONFIG_NAME


def _read_dataclass(data: dict[str, Any], cls: type[Any]) -> Any:
    defaults = cls()
    values: dict[str, Any] = {}
    for field_name in defaults.__dataclass_fields__:
        if field_name in data:
            values[field_name] = data[field_name]
        else:
            values[field_name] = getattr(defaults, field_name)
    return cls(**values)


def load_config(path: Path) -> AppConfig:
    defaults = AppConfig()
    if not path.exists():
        return defaults

    raw = json.loads(path.read_text(encoding="utf-8"))
    return AppConfig(
        display=_read_dataclass(raw.get("display", {}), DisplayConfig),
        preview=_read_dataclass(raw.get("preview", {}), PreviewConfig),
        effect_device=_read_dataclass(raw.get("effect_device", {}), MidiDeviceConfig),
    )


def save_config(config: AppConfig, path: Path) -> None:
    path.write_text(json.dumps(asdict(config), indent=2, sort_keys=True), encoding="utf-8")
