"""Automated unit and live HTTP proxy integration suite for SI-MCP-Doctor."""

import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp_proxy_engine import (
    OllamaSmartProxy,
    audit_all_clients,
    repair_all_clients,
    rollback_latest_backup,
    wire_claude_code_to_smart_proxy,
)
from si_mcp_doctor import start_web_server


class SiMcpDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="si_mcp_doc_"))
        self.home = self.tmp / "home"
        self.appdata = self.tmp / "appdata"
        self.backup_dir = self.tmp / "backups"
        (self.appdata / "Claude").mkdir(parents=True)
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".cursor").mkdir(parents=True)

        # Broken raw Windows backslashes + raw npx ENOENT + missing env block
        broken_claude_desktop = (
            '{\n  "mcpServers": {\n    "playwright": {\n'
            '      "command": "npx",\n      "args": ["-y", "@playwright/mcp@latest"]\n    },\n'
            '    "custom-esm": {\n      "command": "node",\n      "args": ["C:\\Users\\Demo\\my_mcp.mjs"]\n    }\n'
            '  }\n}\n'
        )
        (self.appdata / "Claude" / "claude_desktop_config.json").write_text(broken_claude_desktop, encoding="utf-8")

        # Misplaced mcpServers inside ~/.claude/settings.json instead of ~/.claude.json
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"mcpServers": {"my_local": {"command": "npx", "args": ["my-pkg"]}}}),
            encoding="utf-8",
        )
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_audit_repair_and_sha256_rollback(self) -> None:
        audit = audit_all_clients(custom_home=self.home, custom_appdata=self.appdata)
        ids = {i["id"] for i in audit["issues"]}
        self.assertIn("BACKSLASH_PATH_ESCAPE", ids)
        self.assertIn("NPX_ENOENT_TRAP", ids)
        self.assertIn("CLAUDE_CODE_MISPLACED_MCP", ids)
        self.assertIn("ESM_WINDOWS_PATH_RISK", ids)

        res = repair_all_clients(
            custom_home=self.home,
            custom_appdata=self.appdata,
            custom_backup_dir=self.backup_dir,
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["post_audit"]["issue_count"], 0)

        cd_cfg = json.loads((self.appdata / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
        self.assertEqual(cd_cfg["mcpServers"]["playwright"]["command"], "cmd.exe")
        self.assertEqual(cd_cfg["mcpServers"]["playwright"]["args"][:2], ["/c", "npx"])
        self.assertEqual(cd_cfg["mcpServers"]["custom-esm"]["args"][0], "C:/Users/Demo/my_mcp.mjs")
        self.assertIn("env", cd_cfg["mcpServers"]["playwright"])

        root_claude = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertIn("my_local", root_claude["mcpServers"])
        self.assertEqual(root_claude["mcpServers"]["my_local"]["command"], "cmd.exe")

        rb = rollback_latest_backup(custom_backup_dir=self.backup_dir)
        self.assertTrue(rb["ok"])
        post_rb = audit_all_clients(custom_home=self.home, custom_appdata=self.appdata)
        self.assertGreaterEqual(post_rb["issue_count"], 4)

    def test_live_ollama_proxy_enforces_64k_and_fixes_empty_user(self) -> None:
        received_bodies: list[dict] = []

        class MockOllama(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                ln = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(ln).decode("utf-8"))
                received_bodies.append(body)
                out = json.dumps({"ok": True, "echo_ctx": body.get("options", {}).get("num_ctx")}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        up_srv = ThreadingHTTPServer(("127.0.0.1", 0), MockOllama)
        up_port = up_srv.server_address[1]
        threading.Thread(target=up_srv.serve_forever, daemon=True).start()

        proxy = OllamaSmartProxy(upstream_url=f"http://127.0.0.1:{up_port}", listen_port=0, default_num_ctx=65536)
        px_port = proxy.start()
        try:
            # Simulate a truncated Claude Code -> Ollama payload with 2048 ctx and NO user role turn
            bad_req = json.dumps({
                "model": "qwen2.5-coder:14b",
                "options": {"num_ctx": 2048},
                "system": "Build feature safely",
                "messages": [{"role": "assistant", "content": "Calling tool..."}],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{px_port}/v1/messages",
                data=bad_req,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                rjson = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(rjson["echo_ctx"], 65536)
            self.assertTrue(any(m.get("role") == "user" for m in received_bodies[0]["messages"]))
            st = proxy.get_stats()
            self.assertEqual(st["ctx_enforced_count"], 1)
            self.assertEqual(st["empty_user_repaired"], 1)
        finally:
            proxy.stop()
            up_srv.shutdown()
            up_srv.server_close()

    def test_wire_claude_settings_and_web_endpoints(self) -> None:
        wres = wire_claude_code_to_smart_proxy(
            proxy_port=11435,
            num_ctx=65536,
            set_windows_env=False,
            custom_home=self.home,
            custom_backup_dir=self.backup_dir,
        )
        self.assertTrue(wres["ok"])
        settings = json.loads((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:11435")
        self.assertEqual(settings["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "65536")

        srv, px = start_web_server(port=0, proxy_port=0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            for ep in ("/", "/api/state", "/api/proxy"):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{ep}", timeout=5) as r:
                    self.assertEqual(r.status, 200)
        finally:
            srv.shutdown()
            srv.server_close()
            px.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
