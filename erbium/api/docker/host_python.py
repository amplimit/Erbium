from importlib.metadata import distributions
from os import PathLike, chmod, replace
from pathlib import Path
from re import fullmatch
from shutil import rmtree

ENTRY_POINT_PATTERN: str = r"([A-Za-z_][\w.]*)\s*:\s*([A-Za-z_][\w.]*)"
SCRIPT_NAME_PATTERN: str = r"[A-Za-z0-9][\w.+-]*"


def write_console_scripts(site_packages: str | PathLike[str], target: str | PathLike[str], *,
                          interpreter: str = "/usr/bin/python3") -> int:
    target = Path(target)
    staging = target.with_name(f".{target.name}.tmp")
    rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o755, parents=True)
    count = 0
    for distribution in distributions(path=[str(site_packages)]):
        for entry_point in distribution.entry_points:
            if entry_point.group != "console_scripts" or not fullmatch(SCRIPT_NAME_PATTERN, entry_point.name):
                continue
            match = fullmatch(ENTRY_POINT_PATTERN, entry_point.value)
            if not match:
                continue
            module, attribute = match.groups()
            head = attribute.split(".", 1)[0]
            script = staging / entry_point.name
            script.write_text(f"#!{interpreter}\nimport sys\nfrom {module} import {head}\nsys.exit({attribute}())\n")
            chmod(script, 0o755)
            count += 1
    if target.exists():
        rmtree(target)
    replace(staging, target)
    return count
