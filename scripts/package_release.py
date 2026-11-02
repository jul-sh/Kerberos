#!/usr/bin/env python3
"""Package the validated fonts into releases/Kerberos-<version>.zip.

    make release      (after make build && make test)
"""
from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fontTools.ttLib import TTFont

from build import VARIABLE, Style

ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # stable archive bytes across rebuilds



def semver(version: str) -> str:
    """Font version X.YZZ (Google Fonts style) as release version X.Y.Z, e.g. 1.100 -> 1.1.0."""
    major, minor = version.split(".")
    return f"{int(major)}.{int(minor) // 100}.{int(minor) % 100}"


def write_archive(out: Path, version: str) -> int:
    """Write a deterministic release archive for ``version`` to ``out`` and return file count."""
    root = f"Kerberos-{version}"
    files = [(path, f"{root}/{path.relative_to(ROOT / 'fonts')}")
             for sub in ("variable", "serifonly", "static", "web")
             for path in sorted((ROOT / "fonts" / sub).glob("*")) if path.is_file()]
    files += [(ROOT / "OFL.txt", f"{root}/OFL.txt"), (ROOT / "README.md", f"{root}/README.md")]
    out.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(out, "w", ZIP_DEFLATED) as bundle:
        for path, name in files:
            info = ZipInfo(name, FIXED_TIME)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, path.read_bytes())
    return len(files)


def main() -> None:
    validation = json.loads((ROOT / "build/validation.json").read_text())
    if validation.get("status") != "passed":
        raise SystemExit("Fonts must pass `make test` before packaging.")
    with TTFont(VARIABLE[Style.ROMAN], lazy=True) as font:
        version = font["name"].getDebugName(5).split()[1].split(";")[0]
    ver = semver(version)
    out = ROOT / "releases" / f"Kerberos-{ver}.zip"
    file_count = write_archive(out, ver)
    print(json.dumps({"release": str(out.relative_to(ROOT)), "files": file_count,
                      "bytes": out.stat().st_size}))


if __name__ == "__main__":
    main()
