"""Environment diagnostics: versions, serial ports, firmware tooling, Meta credentials (presence only)."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))


def ver(cmd: list[str]) -> str:
    exe = shutil.which(cmd[0])
    if not exe:
        return "NOT FOUND"
    try:
        return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=20, check=False).stdout.strip().splitlines()[0]
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def main() -> None:
    print(f"OS            : {platform.platform()}")
    print(f"Python        : {sys.version.split()[0]}")
    for name, cmd in [("Node", ["node", "--version"]), ("pnpm", ["pnpm", "--version"]), ("Git", ["git", "--version"]), ("PlatformIO", ["pio", "--version"]), ("arduino-cli", ["arduino-cli", "version"])]:
        print(f"{name:<14}: {ver(cmd)}")
    from buildloop.config import get_settings
    from buildloop.hardware.transport import list_ports

    s = get_settings()
    print(f"Meta API key  : {'set' if s.meta_configured else 'NOT SET (Real Mode unavailable; Demo Mode works)'}")
    ports = list_ports()
    print(f"Serial ports  : {len(ports)}")
    for p in ports:
        print(f"  {p.device:<8} {p.description}  [{p.hwid}]")


if __name__ == "__main__":
    main()
