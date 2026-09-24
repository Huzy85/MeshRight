"""Send to slicer: open the model straight in a slicer installed on this
computer.

Only slicers found in their usual install places are offered, and only
those programs are ever started. The model is saved as a 3MF in MeshRight's
own folder first, then the slicer is asked to open it.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

KEEP_FILES = 10


@dataclass(frozen=True)
class Slicer:
    id: str
    name: str
    windows: tuple[str, ...] = ()   # paths under Program Files, globs allowed
    mac: tuple[str, ...] = ()       # app bundle names
    linux: tuple[str, ...] = ()     # commands on the PATH
    flatpak: str = ""


KNOWN = (
    Slicer("bambu", "Bambu Studio", ("Bambu Studio/bambu-studio.exe",), ("BambuStudio.app",),
           ("bambu-studio",), "com.bambulab.BambuStudio"),
    Slicer("orca", "OrcaSlicer", ("OrcaSlicer/orca-slicer.exe",), ("OrcaSlicer.app",),
           ("orca-slicer",), "io.github.softfever.OrcaSlicer"),
    Slicer("prusa", "PrusaSlicer", ("Prusa3D/PrusaSlicer/prusa-slicer.exe",),
           ("PrusaSlicer.app", "Original Prusa Drivers/PrusaSlicer.app"), ("prusa-slicer",), "com.prusa3d.PrusaSlicer"),
    Slicer("cura", "UltiMaker Cura", ("UltiMaker Cura */UltiMaker-Cura.exe", "Ultimaker Cura */Cura.exe"),
           ("UltiMaker Cura.app", "Ultimaker Cura.app"), ("cura",), "com.ultimaker.cura"),
    Slicer("creality", "Creality Print", ("Creality/Creality Print*/CrealityPrint.exe",), ("Creality Print.app",)),
)


def _windows_roots(env) -> list[Path]:
    names = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
    return [Path(env[n]) for n in names if env.get(n)]


def locate(slicer: Slicer, platform: str | None = None, env=None, home: Path | None = None) -> list[str] | None:
    """The command that opens a file in this slicer (the file goes last), or
    None when it is not installed."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()
    if platform == "win32":
        for root in _windows_roots(env):
            for pattern in slicer.windows:
                found = sorted(glob.glob(str(root / pattern)))
                if found:
                    return [found[-1]]  # the newest version sorts last
    elif platform == "darwin":
        for folder in (Path("/Applications"), home / "Applications"):
            for app in slicer.mac:
                if (folder / app).is_dir():
                    return ["open", "-a", str(folder / app)]
    else:
        for command in slicer.linux:
            path = shutil.which(command, path=env.get("PATH"))
            if path:
                return [path]
        if slicer.flatpak:
            for folder in (Path("/var/lib/flatpak/app"), home / ".local/share/flatpak/app"):
                if (folder / slicer.flatpak).is_dir():
                    return ["flatpak", "run", slicer.flatpak]
    return None


def installed(**kwargs) -> list[Slicer]:
    return [s for s in KNOWN if locate(s, **kwargs)]


def outbox() -> Path:
    from .settings import config_dir

    folder = config_dir() / "for-slicer"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def send(slicer_id: str, filename: str, data: bytes, **kwargs) -> Path:
    """Save the 3MF and open it in the slicer. Returns where it was saved."""
    slicer = next((s for s in KNOWN if s.id == slicer_id), None)
    command = locate(slicer, **kwargs) if slicer else None
    if not command:
        raise ValueError("That slicer is not installed on this computer.")
    folder = outbox()
    # Keep the folder small: only the most recent files stay.
    old = sorted(folder.glob("*.3mf"), key=lambda p: p.stat().st_mtime)
    for stale in old[: max(0, len(old) - KEEP_FILES + 1)]:
        stale.unlink(missing_ok=True)
    path = folder / filename
    path.write_bytes(data)
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen([*command, str(path)], **options)  # noqa: S603 - fixed, known programs only
    except OSError as exc:
        raise ValueError(f"Could not start {slicer.name}: {exc.strerror or exc}") from None
    return path
