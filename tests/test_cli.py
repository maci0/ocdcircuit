from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benches.complex import bench, convert, fetch
from tools import sbom


class CLIHelpTest(unittest.TestCase):
    def test_help_processes(self) -> None:
        commands = [
            ["apps.ocd", *args] for args in [
                [], *[[cmd] for cmd in (
                    "new", "run", "status", "diff", "pin", "fp", "xray",
                    "scan", "quote", "score", "lint", "doctor", "plugins", "kb")],
                *[["kb", cmd] for cmd in (
                    "list", "search", "read", "add", "fetch", "index", "ask")],
            ]
        ]
        commands.extend([[module] for module in (
            "apps.mcp", "apps.studio", "tools.atopile", "tools.mitox",
            "tools.tscircuit", "tools.easyeda_live", "tools.scanbench",
            "tools.sbom", "benches.discrete6502.bench",
            "benches.discrete6502.convert", "benches.complex.bench",
            "benches.complex.convert", "benches.complex.fetch",
            "ocdcircuit.raster", "ocdcircuit.view3d")])
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, "-B", "-m", *command, "--help"],
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn("usage", result.stdout.lower())
                self.assertNotIn("\x1b[", result.stdout)

    def test_bench_help(self) -> None:
        for flag in ("--help", "-h"):
            for args in ([flag], ["--board", "ulx3s", flag]):
                with self.subTest(args=args):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with (patch.object(sys, "argv", ["bench", *args]),
                          patch.object(bench, "run") as run,
                          contextlib.redirect_stdout(stdout),
                          contextlib.redirect_stderr(stderr)):
                        bench.main()
                    run.assert_not_called()
                    self.assertIn("python -m benches.complex.bench", stdout.getvalue())
                    self.assertIn("--board", stdout.getvalue())
                    self.assertIn("[seeds] [iters]", stdout.getvalue())
                    self.assertEqual(stderr.getvalue(), "")

    def test_convert_help_skips_conversion(self) -> None:
        for flag in ("--help", "-h"):
            for args in ([flag], ["--board", "ulx3s", flag]):
                with self.subTest(args=args):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with (patch.object(sys, "argv", ["convert", *args]),
                          patch.object(convert, "convert") as conv,
                          contextlib.redirect_stdout(stdout),
                          contextlib.redirect_stderr(stderr)):
                        convert.main()
                    conv.assert_not_called()
                    self.assertIn("python -m benches.complex.convert",
                                  stdout.getvalue())
                    self.assertIn("--board", stdout.getvalue())
                    self.assertEqual(stderr.getvalue(), "")

    def test_fetch_help_skips_download(self) -> None:
        for flag in ("--help", "-h"):
            for args in ([flag], ["--board", "ulx3s", flag]):
                with self.subTest(args=args):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with (patch.object(sys, "argv", ["fetch", *args]),
                          patch.object(fetch.urllib.request, "urlopen") as dl,
                          contextlib.redirect_stdout(stdout),
                          contextlib.redirect_stderr(stderr)):
                        fetch.main()
                    dl.assert_not_called()
                    self.assertIn("python -m benches.complex.fetch",
                                  stdout.getvalue())
                    self.assertIn("--board", stdout.getvalue())
                    self.assertEqual(stderr.getvalue(), "")

    def test_fetch_unknown_board_without_download(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.object(sys, "argv", ["fetch", "--board", "nope"]),
              patch.object(fetch.urllib.request, "urlopen") as dl,
              contextlib.redirect_stdout(stdout),
              contextlib.redirect_stderr(stderr)):
            with self.assertRaises(SystemExit) as ctx:
                fetch.main()
        dl.assert_not_called()
        self.assertIn("unknown board 'nope'", str(ctx.exception))
        self.assertIn("ulx3s", str(ctx.exception))


    def test_sbom_help_emits_usage_not_data(self) -> None:
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                stdout, stderr = io.StringIO(), io.StringIO()
                with (patch.object(sbom.sys, "argv", ["sbom", flag]),
                      patch.object(sbom, "inventory") as inv,
                      contextlib.redirect_stdout(stdout),
                      contextlib.redirect_stderr(stderr)):
                    rc = sbom.main()
                inv.assert_not_called()
                self.assertEqual(rc, 0)
                self.assertIn("python -m tools.sbom", stdout.getvalue())
                self.assertIn("CycloneDX", stdout.getvalue())
                self.assertEqual(stderr.getvalue(), "")

    def test_make_sbom_emits_only_json(self) -> None:
        result = subprocess.run(
            ["make", "--no-print-directory", "sbom"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["bomFormat"], "CycloneDX")

    def test_make_board_sweeps(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            boards = Path(tmp) / "boards"
            boards.mkdir()
            (boards / "sample.ocd").write_text(
                "board sample 20x12 2L\npart R1 R0805 1k\n"
                "part R2 R0805 2k\nN L0 :: R1.1 R2.1\n",
                encoding="utf-8")
            for target in ("farm", "fabsweep"):
                with self.subTest(target=target):
                    result = subprocess.run(
                        ["make", "--no-print-directory", "-s", "-f",
                         str(root / "Makefile"), f"PYTHON={sys.executable}",
                         target], cwd=tmp,
                        env={**os.environ, "PYTHONPATH": str(root)},
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("sample.ocd", result.stdout)
                    self.assertIn("0 errors" if target == "farm" else "jlc=0",
                                  result.stdout)

    def test_sbom_run_emits_cyclonedx_json(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.object(sbom.sys, "argv", ["sbom"]),
              contextlib.redirect_stdout(stdout),
              contextlib.redirect_stderr(stderr)):
            rc = sbom.main()
        self.assertEqual(rc, 0)
        doc = json.loads(stdout.getvalue())
        self.assertEqual(doc["bomFormat"], "CycloneDX")
        self.assertEqual(doc["specVersion"], "1.5")
        names = {c["name"] for c in doc["components"]}
        self.assertIn("rich", names)
        self.assertIn("setuptools", names)
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
