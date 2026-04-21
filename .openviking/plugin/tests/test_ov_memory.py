"""Tests for ov_memory.detect_backend — the boundary that decides where memories land.

Run from the jura repo root:

    ~/.local/pipx/venvs/openviking/bin/python3 \\
        .openviking/plugin/tests/test_ov_memory.py -v

(The dotted `-m unittest .openviking…` form can't be used because the leading
dot in the path is parsed as a relative-import prefix.)

These tests exercise the security-relevant "HTTP required, no silent local
fallback" contract added in v3.5. If a ghost ./data/ dir ever shows up at a
consuming workspace's root again, the regression should land here first.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ov_memory  # noqa: E402


class DetectBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _conf(self, **storage: object) -> dict:
        return {
            "server": {"host": "127.0.0.1", "port": 1934},
            "storage": storage,
        }

    def test_http_mode_when_server_healthy(self) -> None:
        with patch.object(ov_memory, "_health_check", return_value=True):
            backend = ov_memory.detect_backend(self.project_dir, self._conf())
        self.assertEqual(backend.mode, "http")
        self.assertEqual(backend.url, "http://127.0.0.1:1934")
        self.assertEqual(backend.local_data_path, "")

    def test_raises_server_unreachable_when_health_check_fails(self) -> None:
        """Critical: must raise, never silently fall back. See v3.5 in README."""
        with patch.object(ov_memory, "_health_check", return_value=False):
            with self.assertRaises(ov_memory.ServerUnreachable) as ctx:
                ov_memory.detect_backend(self.project_dir, self._conf())
        self.assertEqual(ctx.exception.url, "http://127.0.0.1:1934")

    def test_local_mode_when_no_server_configured(self) -> None:
        conf = {"storage": {}}
        # _health_check must not even be called when server is absent.
        with patch.object(ov_memory, "_health_check", side_effect=AssertionError):
            backend = ov_memory.detect_backend(self.project_dir, conf)
        self.assertEqual(backend.mode, "local")
        self.assertEqual(
            backend.local_data_path,
            str(self.project_dir / ".openviking" / "data-local"),
        )

    def test_local_mode_honors_explicit_vectordb_path(self) -> None:
        conf = {"storage": {"vectordb": {"path": "custom/location"}}}
        backend = ov_memory.detect_backend(self.project_dir, conf)
        self.assertEqual(
            backend.local_data_path, str(self.project_dir / "custom" / "location")
        )


class BuildServerUrlTests(unittest.TestCase):
    def test_bare_host(self) -> None:
        self.assertEqual(ov_memory._build_server_url("127.0.0.1", 1934), "http://127.0.0.1:1934")

    def test_rewrites_unroutable_bind_addresses(self) -> None:
        for unroutable in ("0.0.0.0", "::", "[::]"):
            self.assertEqual(
                ov_memory._build_server_url(unroutable, 1934),
                "http://127.0.0.1:1934",
            )

    def test_respects_explicit_scheme_and_port(self) -> None:
        self.assertEqual(
            ov_memory._build_server_url("https://ov.example.com", 8443),
            "https://ov.example.com:8443",
        )

    def test_does_not_double_port_when_host_already_has_one(self) -> None:
        self.assertEqual(
            ov_memory._build_server_url("http://ov.example.com:9000", 1934),
            "http://ov.example.com:9000",
        )


class CmdSessionStartFailureTests(unittest.TestCase):
    """cmd_session_start must leave a clean, inactive state on unreachable server.

    This prevents subsequent hooks in the same Claude session from trying to
    ingest into a half-created backend and from materializing a ghost dir.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project_dir = Path(self.tmp.name)
        ov_dir = self.project_dir / ".openviking"
        ov_dir.mkdir()
        (ov_dir / "ov.conf").write_text(
            json.dumps({"server": {"host": "127.0.0.1", "port": 1934}, "storage": {}})
        )
        self.state_file = self.project_dir / "state.json"

    def test_writes_inactive_state_and_does_not_create_any_data_dir(self) -> None:
        class Args:
            pass

        args = Args()
        args.project_dir = str(self.project_dir)
        args.state_file = str(self.state_file)

        with patch.object(ov_memory, "_health_check", return_value=False):
            result = ov_memory.cmd_session_start(args)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "server_unreachable")

        state = json.loads(self.state_file.read_text())
        self.assertFalse(state["active"])
        self.assertEqual(state["last_error"], "server_unreachable")
        self.assertEqual(state["last_error_url"], "http://127.0.0.1:1934")

        # The whole point: no ghost workspace at project root or anywhere else.
        for suspect in ("data", "vectordb", "viking"):
            self.assertFalse(
                (self.project_dir / suspect).exists(),
                f"ghost dir '{suspect}' materialized at project root",
            )
        self.assertFalse((self.project_dir / ".openviking" / "data-local").exists())


if __name__ == "__main__":
    unittest.main()
