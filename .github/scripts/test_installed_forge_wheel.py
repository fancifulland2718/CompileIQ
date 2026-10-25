"""Run the existing Forge contracts against an installed wheel, never the checkout.

Requires pytest on a supported Python 3.11-3.14 interpreter. The retained output contains the
installed-package provenance, copied contract fixtures, and a JUnit result.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


CONTRACT_FILES = (
    "test_forge_support.py",
    "test_forge_search_v2.py",
    "test_forge_report.py",
    "test_forge_recipe_pages.py",
)
SCHEMA_PATH = "schemas/opaque-optimization-report-v1.schema.json"


def main() -> int:
    # -I excludes both the caller's checkout/PYTHONPATH and user-site packages.
    # Keep this entry point safe even when invoked from the repository root.
    if not sys.flags.isolated:
        return subprocess.call([sys.executable, "-I", __file__, *sys.argv[1:]])

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[2]

    from packaging.version import Version

    distribution = importlib.metadata.distribution("compileiq")
    recorded_files = {str(path) for path in distribution.files or ()}
    origins = {}
    for name in (
        "compileiq.ciq",
        "compileiq.forge_support",
        "compileiq.forge_search_v2",
        "compileiq.forge_report",
        "compileiq._forge_engine",
    ):
        module = importlib.import_module(name)
        relative = name.replace(".", "/") + ".py"
        expected = Path(distribution.locate_file(relative)).resolve()
        actual = Path(module.__file__).resolve()
        if relative not in recorded_files or actual != expected or actual.is_relative_to(source):
            raise RuntimeError(f"Not an installed wheel module: {name}: {actual}")
        origins[name] = str(actual)

    from compileiq._forge_engine import scheduler_identity
    from compileiq.forge_support import forge_recipe_search_capability

    capability = forge_recipe_search_capability().as_dict()
    if Version(distribution.version) != Version(capability["package_version"]):
        raise RuntimeError("Installed metadata and Forge capability package versions differ")
    identity = scheduler_identity()
    if identity["source_lock"] != capability["core_lock"]:
        raise RuntimeError("Scheduler source identity mismatch")
    if SCHEMA_PATH not in recorded_files:
        raise RuntimeError("The installed wheel does not contain the optimization report schema")

    fixture_root = output / "fixtures"
    tests = fixture_root / "tests" / "unit"
    tests.mkdir(parents=True)
    # Paging contracts reuse helpers from the staged search fixture. Make the
    # copied tree a package so isolated pytest imports that sibling, not checkout
    # code or an unrelated site-packages/tests module.
    (tests.parent / "__init__.py").write_text("", encoding="utf-8")
    (tests / "__init__.py").write_text("", encoding="utf-8")
    for filename in CONTRACT_FILES:
        shutil.copy2(source / "tests" / "unit" / filename, tests / filename)
    # These two fixtures are consumed by existing source/metadata consistency
    # tests. Use the *installed* schema, not a checkout copy that could mask a
    # missing or stale wheel payload. Do not copy the Ray-starting conftest.py.
    shutil.copy2(source / "pyproject.toml", fixture_root / "pyproject.toml")
    schema = fixture_root / SCHEMA_PATH
    schema.parent.mkdir()
    shutil.copy2(distribution.locate_file(SCHEMA_PATH), schema)
    provenance = {
        "python": sys.version,
        "executable": sys.executable,
        "package_version": distribution.version,
        "module_origins": origins,
        "core_lock": capability["core_lock"],
        "execution_engine": capability["execution_engine"],
        "scheduler_source_files": identity["files"],
        "contracts": list(CONTRACT_FILES),
    }
    (output / "environment.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2), flush=True)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if sys.platform == "win32":
        environment.setdefault("CIQ_PROCESS_MODE", "spawn")
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "pytest",
            "tests/unit",
            "-q",
            "-o",
            "addopts=",
            f"--basetemp={output / 'pytest-temp'}",
            f"--junitxml={output / 'contracts.xml'}",
        ],
        cwd=fixture_root,
        env=environment,
        check=True,
    )
    # Retain the existing actual amber smoke alongside the broader contracts.
    subprocess.run(
        [sys.executable, "-I", str(source / ".github/scripts/smoke_test_compileiq_wheel.py")],
        cwd=output,
        env=environment,
        check=True,
    )
    print("Installed Forge contracts and amber search passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
