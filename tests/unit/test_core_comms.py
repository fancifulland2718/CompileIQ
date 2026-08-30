import json
from unittest.mock import MagicMock

import pytest

from compileiq.core import core_comms
from compileiq.core.core_comms import CoreIPC
from compileiq.core.verify_core import sha256_file, with_core_lock


def _write_manifest(root, rel_path, binary):
    manifest = root / "core-manifest.json"
    manifest_data = {
        "schema_version": 2,
        "core_commit": "test",
        "files": {rel_path: f"sha256:{sha256_file(binary)}"},
    }
    manifest.write_text(json.dumps(with_core_lock(manifest_data)), encoding="utf-8")
    return manifest


def _write_platform_manifest(root, files):
    manifest = root / "core-manifest.json"
    manifest_data = {
        "schema_version": 2,
        "core_commit": "test",
        "files": {rel_path: f"sha256:{sha256_file(path)}" for rel_path, path in files.items()},
    }
    manifest.write_text(json.dumps(with_core_lock(manifest_data)), encoding="utf-8")
    return manifest


def test_core_ipc_honors_core_binary_override(monkeypatch, tmp_path):
    binary = tmp_path / "core"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        process = MagicMock()
        process.poll.return_value = 0
        return process

    socket = MagicMock()
    socket.getsockname.return_value = ("127.0.0.1", 1234)
    monkeypatch.setenv("CIQ_CORE_BINARY", str(binary))
    monkeypatch.setattr(core_comms.subprocess, "Popen", fake_popen)

    with pytest.warns(RuntimeWarning, match="CIQ_CORE_BINARY"):
        CoreIPC().start(socket, "main.config")

    assert calls[0][0][:2] == [str(binary), "-c"]


def test_core_ipc_verifies_bundled_binary(monkeypatch, tmp_path):
    root = tmp_path / "executable"
    rel_path = "linux/x86_64/bin/core"
    binary = root / rel_path
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"core")
    _write_manifest(root, rel_path, binary)

    monkeypatch.setattr(core_comms, "EXECUTABLE_DIR", root)
    monkeypatch.setattr(core_comms, "MANIFEST_PATH", root / "core-manifest.json")
    monkeypatch.setattr(core_comms.sys, "platform", "linux")
    monkeypatch.setattr(core_comms.platform, "machine", lambda: "x86_64")

    assert CoreIPC()._resolve_core_binary() == binary


def test_core_ipc_locks_opaque_search_to_exact_bundled_manifest(monkeypatch, tmp_path):
    root = tmp_path / "executable"
    rel_path = "linux/x86_64/bin/core"
    binary = root / rel_path
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"core")
    manifest_path = _write_manifest(root, rel_path, binary)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(core_comms, "EXECUTABLE_DIR", root)
    monkeypatch.setattr(core_comms, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(core_comms.sys, "platform", "linux")
    monkeypatch.setattr(core_comms.platform, "machine", lambda: "x86_64")

    ipc = CoreIPC(
        required_core_commit=manifest["core_commit"],
        required_core_lock=manifest["core_lock"],
    )
    assert ipc._resolve_core_binary() == binary
    assert ipc.resolved_core_provenance["core_commit"] == manifest["core_commit"]
    assert ipc.resolved_core_provenance["core_lock"] == manifest["core_lock"]


@pytest.mark.parametrize("override_name", ("CIQ_CORE_BINARY", "CIQ_CORE_MANIFEST"))
def test_core_ipc_rejects_overrides_for_opaque_search(monkeypatch, tmp_path, override_name):
    override = tmp_path / "override"
    override.write_bytes(b"override")
    monkeypatch.setenv(override_name, str(override))

    ipc = CoreIPC(
        required_core_commit="exact-core",
        required_core_lock="sha256:" + "1" * 64,
    )
    with pytest.raises(RuntimeError, match="overrides are disabled"):
        ipc._resolve_core_binary()


def test_core_ipc_rejects_bundled_manifest_different_from_opaque_domain(monkeypatch, tmp_path):
    root = tmp_path / "executable"
    rel_path = "linux/x86_64/bin/core"
    binary = root / rel_path
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"core")
    manifest_path = _write_manifest(root, rel_path, binary)

    monkeypatch.setattr(core_comms, "EXECUTABLE_DIR", root)
    monkeypatch.setattr(core_comms, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(core_comms.sys, "platform", "linux")
    monkeypatch.setattr(core_comms.platform, "machine", lambda: "x86_64")

    ipc = CoreIPC(
        required_core_commit="different-core",
        required_core_lock="sha256:" + "1" * 64,
    )
    with pytest.raises(RuntimeError, match="core commit does not match"):
        ipc._resolve_core_binary()


def test_core_ipc_verifies_bundled_platform_dependencies(monkeypatch, tmp_path):
    root = tmp_path / "executable"
    binary = root / "linux/x86_64/bin/core"
    dependency = root / "linux/x86_64/bin/_core"
    library = root / "linux/x86_64/lib/libciq.so"
    for path in (binary, dependency, library):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    _write_platform_manifest(
        root,
        {
            "linux/x86_64/bin/core": binary,
            "linux/x86_64/bin/_core": dependency,
            "linux/x86_64/lib/libciq.so": library,
        },
    )
    dependency.write_bytes(b"modified")

    monkeypatch.setattr(core_comms, "EXECUTABLE_DIR", root)
    monkeypatch.setattr(core_comms, "MANIFEST_PATH", root / "core-manifest.json")
    monkeypatch.setattr(core_comms.sys, "platform", "linux")
    monkeypatch.setattr(core_comms.platform, "machine", lambda: "x86_64")

    with pytest.raises(RuntimeError, match="linux/x86_64/bin/_core"):
        CoreIPC()._resolve_core_binary()


def test_core_ipc_rejects_modified_bundled_binary(monkeypatch, tmp_path):
    root = tmp_path / "executable"
    rel_path = "linux/x86_64/bin/core"
    binary = root / rel_path
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"core")
    _write_manifest(root, rel_path, binary)
    binary.write_bytes(b"modified")

    monkeypatch.setattr(core_comms, "EXECUTABLE_DIR", root)
    monkeypatch.setattr(core_comms, "MANIFEST_PATH", root / "core-manifest.json")
    monkeypatch.setattr(core_comms.sys, "platform", "linux")
    monkeypatch.setattr(core_comms.platform, "machine", lambda: "x86_64")

    with pytest.raises(RuntimeError, match="does not match"):
        CoreIPC()._resolve_core_binary()


def test_core_ipc_verifies_override_when_manifest_is_set(monkeypatch, tmp_path):
    root = tmp_path / "artifact"
    rel_path = "linux/x86_64/bin/core"
    binary = root / rel_path
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"core")
    manifest = _write_manifest(root, rel_path, binary)

    monkeypatch.setenv("CIQ_CORE_BINARY", str(binary))
    monkeypatch.setenv("CIQ_CORE_MANIFEST", str(manifest))

    assert CoreIPC()._resolve_core_binary() == binary
