"""The user's printer, stored on this computer.

Saved as a small JSON file in the user's settings folder, so the browser app,
batch mode and the command line all use the same printer.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# Density in g/cm³, for weight estimates.
MATERIALS = {
    "PLA": 1.24,
    "PETG": 1.27,
    "ABS": 1.04,
    "ASA": 1.07,
    "TPU": 1.21,
    "Resin": 1.15,
}


@dataclass
class Printer:
    name: str = "Not set"
    bed_x: float = 220.0
    bed_y: float = 220.0
    bed_z: float = 250.0
    nozzle_mm: float = 0.4
    technology: str = "filament"  # "filament" or "resin"
    material: str = "PLA"

    @property
    def density(self) -> float:
        return MATERIALS.get(self.material, MATERIALS["PLA"])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Printer":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        printer = cls(**clean)
        printer.validate()
        return printer

    def validate(self) -> None:
        for name in ("bed_x", "bed_y", "bed_z"):
            value = float(getattr(self, name))
            if not 10 <= value <= 5000:
                raise ValueError(f"The bed size ({name[-1].upper()}) must be between 10 and 5000 mm.")
            setattr(self, name, value)
        self.nozzle_mm = float(self.nozzle_mm)
        if not 0.05 <= self.nozzle_mm <= 3:
            raise ValueError("The nozzle size must be between 0.05 and 3 mm.")
        if self.technology not in ("filament", "resin"):
            raise ValueError("The printer type must be filament or resin.")
        if self.material not in MATERIALS:
            raise ValueError(f"The material must be one of: {', '.join(MATERIALS)}.")
        self.name = str(self.name)[:80] or "My printer"


# Common printers. Sizes are the printable volume in mm; users can edit them.
PRESETS = [
    Printer("Bambu Lab A1 mini", 180, 180, 180),
    Printer("Bambu Lab A1", 256, 256, 256),
    Printer("Bambu Lab P1S / P1P / X1 Carbon", 256, 256, 256),
    Printer("Prusa MINI+", 180, 180, 180),
    Printer("Prusa MK4 / MK4S", 250, 210, 220),
    Printer("Prusa Core One", 250, 220, 270),
    Printer("Creality Ender-3 V3 SE", 220, 220, 250),
    Printer("Creality K1 / K1C", 220, 220, 250),
    Printer("Creality K1 Max", 300, 300, 300),
    Printer("Elegoo Neptune 4 Pro", 225, 225, 265),
    Printer("Anycubic Kobra 3", 250, 250, 260),
    Printer("Resin printer (e.g. Elegoo Saturn)", 218, 122, 220, 0.05, "resin", "Resin"),
]


def config_dir() -> Path:
    override = os.environ.get("MESHRIGHT_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "MeshRight"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MeshRight"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "meshright"


def _path() -> Path:
    return config_dir() / "settings.json"


def load() -> tuple[Printer, bool]:
    """The saved printer, and whether the user has chosen one yet."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return Printer.from_dict(data.get("printer", {})), True
    except (OSError, ValueError, TypeError):
        return Printer(), False


def save(printer: Printer) -> None:
    printer.validate()
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"printer": printer.to_dict()}, indent=2), encoding="utf-8")
