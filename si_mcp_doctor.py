"""
SI-MCP-Doctor v1.0 — 1-Click Windows AI, MCP & Local Ollama Auto-Fixer + Context Proxy UI
Fixes real Windows AI failures across Claude Desktop, Claude Code, Cursor, Antigravity, VS Code & Ollama:
  1. 'spawn npx ENOENT' on Windows (wraps npx/npm cleanly in cmd.exe /c)
  2. Broken Windows JSON backslashes & Node ESM 'c:' path scheme crashes
  3. Misplaced Claude Code mcpServers in ~/.claude/settings.json vs ~/.claude.json
  4. Ollama 'no user query found in messages' & 4K context window truncation via live :11435 proxy
  5. Missing GUI env variables & SHA-256 verified 1-click backup/rollback
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any
from urllib.parse import urlparse

from mcp_proxy_engine import (
    DEFAULT_CTX_TOKENS,
    DEFAULT_PROXY_PORT,
    OllamaSmartProxy,
    audit_all_clients,
    inspect_ollama_environment,
    repair_all_clients,
    rollback_latest_backup,
    wire_claude_code_to_smart_proxy,
)

APP_VERSION = "1.0.0"

WEB_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SI-MCP-Doctor — 1-Click AI & MCP Windows Fixer + Ollama 64K Proxy</title>
<style>
:root {
  --bg: #070b14; --panel: #0d1524; --card: #121d30; --border: #233654;
  --accent: #00e5a3; --blue: #38bdf8; --amber: #f59e0b; --red: #ef4444;
  --text: #eef4ff; --muted: #8ea5c8;
}
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
body { background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
.topbar {
  height: 60px; background: #0a101c; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 22px;
}
.brand { display: flex; align-items: center; gap: 12px; font-weight: 800; font-size: 19px; }
.pill { background: rgba(0,229,163,0.14); color: var(--accent); border: 1px solid rgba(0,229,163,0.45); padding: 4px 11px; border-radius: 999px; font-size: 12px; font-weight: 700; }
.pill.blue { background: rgba(56,189,248,0.14); color: var(--blue); border-color: rgba(56,189,248,0.45); }
.main { display: flex; flex: 1; overflow: hidden; }
.sidebar {
  width: 315px; background: var(--panel); border-right: 1px solid var(--border);
  padding: 16px 14px; display: flex; flex-direction: column; gap: 8px;
}
.nav-btn {
  text-align: left; padding: 13px 14px; border-radius: 10px; border: 1px solid transparent;
  background: transparent; color: var(--muted); font-size: 14px; font-weight: 600; cursor: pointer;
  display: flex; justify-content: space-between; align-items: center;
}
.nav-btn.active { background: #16253f; color: #fff; border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
.content { flex: 1; padding: 20px 26px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px; }
.card h2 { font-size: 18px; margin-bottom: 8px; color: #fff; }
.sub { font-size: 13.5px; color: var(--muted); margin-bottom: 12px; line-height: 1.45; }
.grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.metric-val { font-size: 26px; font-weight: 800; color: var(--accent); margin: 3px 0; }
.metric-lbl { font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px; }
.btn { padding: 10px 15px; border-radius: 8px; border: none; font-weight: 700; font-size: 13.5px; cursor: pointer; background: var(--accent); color: #041b12; }
.btn.blue { background: var(--blue); color: #041926; }
.btn.warn { background: var(--amber); color: #1d1202; }
.btn.sec { background: #1c2e4a; color: #dbeafe; border: 1px solid #355280; }
.toggle-row {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding: 11px 14px; background: #0c1524; border: 1px solid #223554; border-radius: 10px; margin-bottom: 8px;
}
.toggle-row input[type="checkbox"] { width: 20px; height: 20px; accent-color: var(--accent); cursor: pointer; }
.issue-row {
  background: #0c1524; border: 1px solid #263959; border-left: 4px solid var(--amber);
  border-radius: 10px; padding: 11px 14px; margin-bottom: 8px;
}
.issue-row.HIGH { border-left-color: var(--red); }
.issue-row.FIXED { border-left-color: var(--accent); }
.tag { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11.5px; font-weight: 700; margin-right: 6px; }
.tag.HIGH { background: rgba(239,68,68,0.2); color: #fca5a5; }
.tag.MEDIUM { background: rgba(245,158,11,0.2); color: #fcd34d; }
.tag.LOW { background: rgba(56,189,248,0.18); color: #7dd3fc; }
.tag.FIXED { background: rgba(0,229,163,0.18); color: var(--accent); }
.toast { padding: 11px 16px; border-radius: 10px; font-size: 13.5px; font-weight: 600; display: none; }
.toast.ok { display: block; background: rgba(0,229,163,0.14); border: 1px solid var(--accent); color: var(--accent); }
.toast.err { display: block; background: rgba(239,68,68,0.16); border: 1px solid var(--red); color: #fca5a5; }
</style>
</head>
<body>
<div class="topbar">
  <div class="brand">
    <span style="color:var(--accent)">&#9889; SI-MCP-DOCTOR</span>
    <span class="pill">UNIVERSAL WINDOWS AI FIXER</span>
    <span class="pill blue" id="proxyPill">64K CONTEXT PROXY: ON (:11435)</span>
  </div>
  <div style="color:var(--muted);font-size:12.5px">github.com/Grokeador/si-mcp-doctor</div>
</div>

<div class="main">
  <div class="sidebar">
    <button class="nav-btn active" onclick="showTab(0)" id="nav0">Step 1: AI Clients Scan <span class="tag FIXED" id="badgeClients">0</span></button>
    <button class="nav-btn" onclick="showTab(1)" id="nav1">Step 2: Yes / No Auto-Fix</button>
    <button class="nav-btn" onclick="showTab(2)" id="nav2">Step 3: Audit & Proof <span class="tag HIGH" id="badgeIssues">0 BUGS</span></button>
    <button class="nav-btn" onclick="showTab(3)" id="nav3">Bonus: Live Ollama Proxy</button>

    <div style="margin-top:auto;background:#0a1220;border:1px solid #223556;border-radius:12px;padding:13px">
      <div style="font-size:11.5px;color:var(--muted);margin-bottom:8px">UNIVERSAL 1-CLICK CONTROLS</div>
      <button class="btn" style="width:100%;margin-bottom:8px" onclick="repairAll()">&#9889; 1-Click Fix All Clients</button>
      <button class="btn blue" style="width:100%;margin-bottom:8px" onclick="wireClaudeProxy()">&#128279; Link Claude to 64K Proxy</button>
      <button class="btn sec" style="width:100%;margin-bottom:8px" onclick="fetchState()">&#128269; Re-Scan PC Now</button>
      <button class="btn warn" style="width:100%" onclick="rollbackAll()">&#8634; Restore SHA-256 Backup</button>
    </div>
  </div>

  <div class="content">
    <div id="toastBox" class="toast"></div>
    <div id="tabContent"></div>
  </div>
</div>

<script>
let currentTab = 0;
let stateData = null;
const opts = {
  fix_npx_enoent: true,
  fix_json_and_paths: true,
  migrate_claude_misplaced: true,
  inject_gui_env: true
};

function setToast(msg, ok=true) {
  const b = document.getElementById('toastBox');
  b.textContent = msg;
  b.className = 'toast ' + (ok ? 'ok' : 'err');
}

function showTab(idx) {
  currentTab = idx;
  for (let i = 0; i < 4; i++) document.getElementById('nav' + i).className = 'nav-btn' + (i === idx ? ' active' : '');
  renderTab();
}

async function fetchState() {
  const res = await fetch('/api/state');
  stateData = await res.json();
  document.getElementById('badgeClients').textContent = stateData.audit.existing_count + ' APPS';
  const cnt = stateData.audit.issue_count;
  const bi = document.getElementById('badgeIssues');
  bi.textContent = cnt === 0 ? '0 BUGS' : cnt + ' BUGS';
  bi.className = 'tag ' + (cnt === 0 ? 'FIXED' : 'HIGH');
  const px = stateData.proxy;
  document.getElementById('proxyPill').textContent = px.running ? ('64K CONTEXT PROXY: ON (:' + px.listen_url.split(':').pop() + ')') : 'PROXY STOPPED';
  renderTab();
}

async function pollProxy() {
  try {
    const r = await fetch('/api/proxy');
    const px = await r.json();
    if (stateData) {
      stateData.proxy = px;
      if (currentTab === 3) renderTab();
    }
  } catch (e) {}
}

async function repairAll() {
  const res = await fetch('/api/repair', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({options: opts})
  });
  const d = await res.json();
  setToast(d.ok ? ('Repaired ' + d.actions_applied + ' issues across AI apps! SHA-256 backup saved.') : ('Error: ' + d.error), d.ok);
  await fetchState();
}

async function wireClaudeProxy() {
  const res = await fetch('/api/wire-proxy', {method: 'POST'});
  const d = await res.json();
  setToast(d.ok ? ('Claude Code & OLLAMA_CONTEXT_LENGTH=65536 wired to ' + d.proxy_url + '!') : ('Failed: ' + d.error), d.ok);
  await fetchState();
}

async function rollbackAll() {
  const res = await fetch('/api/rollback', {method: 'POST'});
  const d = await res.json();
  setToast(d.ok ? ('Restored original SHA-256 snapshots: ' + d.restored.length + ' files') : d.error, d.ok);
  await fetchState();
}

function renderTab() {
  if (!stateData) return;
  const a = stateData.audit;
  const ol = stateData.ollama;
  const px = stateData.proxy;
  const el = document.getElementById('tabContent');

  if (currentTab === 0) {
    const clHtml = a.clients.map(c => `
      <div class="issue-row ${c.issues.length ? 'HIGH' : (c.exists ? 'FIXED' : '')}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><strong>${c.label}</strong> <span style="color:var(--blue);font-family:Consolas;font-size:12px">${c.path}</span></div>
          <span class="tag ${c.exists ? (c.issues.length ? 'HIGH' : 'FIXED') : 'LOW'}">${c.exists ? (c.server_count + ' MCPs • ' + c.issues.length + ' bugs') : 'NOT INSTALLED'}</span>
        </div>
      </div>`).join('');

    el.innerHTML = `
      <div class="grid-4">
        <div class="card"><div class="metric-lbl">AI Apps Found</div><div class="metric-val">${a.existing_count}</div><div class="metric-lbl">${a.total_servers} MCP Servers Scanned</div></div>
        <div class="card"><div class="metric-lbl">Windows Config Bugs</div><div class="metric-val" style="color:${a.issue_count ? '#f87171' : 'var(--accent)'}">${a.issue_count}</div><div class="metric-lbl">ENOENT / Paths / Env</div></div>
        <div class="card"><div class="metric-lbl">Ollama Local Server</div><div class="metric-val" style="font-size:21px;color:${ol.ollama_live.reachable ? 'var(--accent)' : '#fcd34d'}">${ol.ollama_live.reachable ? ('ONLINE (' + ol.ollama_live.model_count + ')') : 'STANDBY'}</div><div class="metric-lbl">Port 11434 Probe</div></div>
        <div class="card"><div class="metric-lbl">Context Guard</div><div class="metric-val" style="font-size:21px;color:${ol.ollama_context_ok ? 'var(--accent)' : '#f87171'}">${ol.ollama_context_ok ? '65,536 OK' : '4K RISK'}</div><div class="metric-lbl">Anti-Truncation Shield</div></div>
      </div>
      <div class="card">
        <h2>Step 1: Universal Scan Across All AI Apps on This PC</h2>
        <p class="sub">Finds Claude Desktop, Claude Code, Antigravity, Cursor, Windsurf & VS Code configs automatically — zero hunting for hidden AppData JSONs.</p>
        ${clHtml}
      </div>`;
  } else if (currentTab === 1) {
    const row = (k, t, d) => `
      <div class="toggle-row">
        <div><strong>${t}</strong><div class="sub" style="margin:2px 0 0">${d}</div></div>
        <input type="checkbox" ${opts[k] ? 'checked' : ''} onchange="opts['${k}']=this.checked">
      </div>`;
    el.innerHTML = `
      <div class="card">
        <h2>Step 2: Choose 4 Universal Yes / No Repairs</h2>
        <p class="sub">Every repair runs with an automatic SHA-256 backup so you can undo anytime.</p>
        ${row('fix_npx_enoent', 'Fix "spawn npx ENOENT" & Missing .cmd Crashes on Windows', 'Converts raw command: "npx" into Windows-safe cmd.exe /c npx automatically across all AI clients.')}
        ${row('fix_json_and_paths', 'Fix Broken JSON Backslashes & Node ESM File Path Errors', 'Repairs invalid Windows backslash escapes inside JSON and converts ESM script paths cleanly.')}
        ${row('migrate_claude_misplaced', 'Fix Misplaced Claude Code MCPs (~/.claude/settings.json -> ~/.claude.json)', 'Migrates MCP servers placed inside settings.json to the root file where Claude Code actually reads them.')}
        ${row('inject_gui_env', 'Inject Explicit GUI Environment Blocks for Desktop AI Apps', 'Stops servers from working in PowerShell yet crashing silently when started from Claude Desktop or IDE GUIs.')}
      </div>`;
  } else if (currentTab === 2) {
    const issHtml = a.issues.length ? a.issues.map(i => `
      <div class="issue-row ${i.severity}">
        <div><span class="tag ${i.severity}">${i.severity}</span><strong>${i.id}</strong> <span style="color:var(--blue)">[${i.client} -> ${i.server}]</span></div>
        <div class="sub" style="margin:4px 0 0">${i.detail}</div>
      </div>`).join('') : `<div class="issue-row FIXED"><span class="tag FIXED">CLEAN</span><strong>0 MCP Config Issues Left</strong> — Every detected Windows AI app passes ENOENT, JSON path, and env checks.</div>`;
    el.innerHTML = `
      <div class="card">
        <h2>Step 3: Live Config Bug Report & Verification (${a.issue_count} Issues)</h2>
        <p class="sub">Inspect exact Windows errors caught across your AI apps before and after clicking Fix All.</p>
        ${issHtml}
      </div>`;
  } else if (currentTab === 3) {
    el.innerHTML = `
      <div class="grid-3">
        <div class="card"><div class="metric-lbl">Proxy Endpoint</div><div class="metric-val" style="font-size:21px">${px.listen_url}</div><div class="metric-lbl">Forwards to ${px.upstream_url}</div></div>
        <div class="card"><div class="metric-lbl">64K Context Enforced</div><div class="metric-val">${px.ctx_enforced_count}</div><div class="metric-lbl">Prevents silent 2K/4K drops</div></div>
        <div class="card"><div class="metric-lbl">'No User Query' Prevented</div><div class="metric-val">${px.empty_user_repaired}</div><div class="metric-lbl">Auto-repairs pruned turns</div></div>
      </div>
      <div class="card">
        <h2>Bonus: Live Ollama 64K Context & Empty-User Repair Proxy</h2>
        <p class="sub">Fixes the #1 GitHub crash ("no user query found in messages") by enforcing <code>num_ctx = 65536</code> and preserving user turns automatically at zero token cost.</p>
        <pre style="background:#070c14;border:1px solid #223554;border-radius:10px;padding:14px;font-family:Consolas,monospace;font-size:13px;color:#cbd5e1">${JSON.stringify(px, null, 2)}</pre>
      </div>`;
  }
}

const _up = new URLSearchParams(window.location.search);
if (_up.has('tab')) {
  const _t = parseInt(_up.get('tab'), 10);
  if (!Number.isNaN(_t) && _t >= 0 && _t <= 3) showTab(_t);
}
fetchState();
setInterval(pollProxy, 1000);
</script>
</body>
</html>
"""


def start_web_server(port: int = 8793, proxy_port: int = DEFAULT_PROXY_PORT, proxy_upstream: str = "http://127.0.0.1:11434") -> tuple[ThreadingHTTPServer, OllamaSmartProxy]:
    """Start live OllamaSmartProxy + SI-MCP-Doctor Control Center HTTP server."""
    proxy = OllamaSmartProxy(upstream_url=proxy_upstream, listen_port=proxy_port, default_num_ctx=DEFAULT_CTX_TOKENS)
    proxy.start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, data: dict[str, Any], code: int = 200) -> None:
            payload = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                html = WEB_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            if parsed.path == "/api/state":
                self._send_json({
                    "ok": True,
                    "audit": audit_all_clients(),
                    "ollama": inspect_ollama_environment(),
                    "proxy": proxy.get_stats(),
                })
                return
            if parsed.path == "/api/proxy":
                self._send_json(proxy.get_stats())
                return
            self.send_error(404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if self.path == "/api/repair":
                self._send_json(repair_all_clients(options=body.get("options")))
                return
            if self.path == "/api/wire-proxy":
                self._send_json(wire_claude_code_to_smart_proxy(proxy_port=proxy.listen_port))
                return
            if self.path == "/api/rollback":
                self._send_json(rollback_latest_backup())
                return
            self.send_error(404)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler), proxy


def launch_tk_gui() -> None:
    """Zero-browser native Tkinter Desktop GUI for SI-MCP-Doctor."""
    root = tk.Tk()
    root.title(f"SI-MCP-Doctor v{APP_VERSION} — Universal Windows AI & MCP Auto-Fixer")
    root.geometry("980x680")
    root.configure(bg="#070b14")

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TFrame", background="#070b14")
    style.configure("TLabel", background="#070b14", foreground="#eef4ff", font=("Segoe UI", 10))
    style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"), foreground="#00e5a3")
    style.configure("TCheckbutton", background="#070b14", foreground="#e2ecff", font=("Segoe UI", 10))

    top = ttk.Frame(root, padding=14)
    top.pack(fill=tk.X)
    ttk.Label(top, text="SI-MCP-Doctor — 1-Click Fix for Claude, Cursor, Antigravity & Ollama", style="Header.TLabel").pack(anchor=tk.W)

    chks = ttk.Frame(root, padding=(14, 4))
    chks.pack(fill=tk.X)
    v_npx = tk.BooleanVar(value=True)
    v_paths = tk.BooleanVar(value=True)
    v_mig = tk.BooleanVar(value=True)
    v_env = tk.BooleanVar(value=True)
    ttk.Checkbutton(chks, text="YES: Fix 'spawn npx ENOENT' on Windows (cmd.exe /c npx)", variable=v_npx).pack(anchor=tk.W)
    ttk.Checkbutton(chks, text="YES: Repair broken JSON backslashes & Node ESM paths", variable=v_paths).pack(anchor=tk.W)
    ttk.Checkbutton(chks, text="YES: Migrate misplaced Claude Code MCPs from settings.json -> ~/.claude.json", variable=v_mig).pack(anchor=tk.W)
    ttk.Checkbutton(chks, text="YES: Inject explicit GUI env variables for desktop AI apps", variable=v_env).pack(anchor=tk.W)

    box = scrolledtext.ScrolledText(root, bg="#0b1322", fg="#cde2ff", font=("Consolas", 10), height=22)
    box.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)

    def _show(data: Any) -> None:
        box.delete("1.0", tk.END)
        box.insert(tk.END, json.dumps(data, indent=2))

    def _fix() -> None:
        res = repair_all_clients(options={
            "fix_npx_enoent": v_npx.get(),
            "fix_json_and_paths": v_paths.get(),
            "migrate_claude_misplaced": v_mig.get(),
            "inject_gui_env": v_env.get(),
        })
        _show(res)
        messagebox.showinfo("SI-MCP-Doctor", f"Applied {res['actions_applied']} fixes cleanly with SHA-256 backup!")

    btns = ttk.Frame(root, padding=14)
    btns.pack(fill=tk.X)
    ttk.Button(btns, text="1. Scan All AI Apps", command=lambda: _show({"audit": audit_all_clients(), "ollama": inspect_ollama_environment()})).pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="2. 1-Click Fix All MCP Configs", command=_fix).pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="3. Link Claude to 64K Proxy", command=lambda: _show(wire_claude_code_to_smart_proxy())).pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="4. Restore Backup", command=lambda: _show(rollback_latest_backup())).pack(side=tk.LEFT, padx=4)

    _show({"audit": audit_all_clients(), "ollama": inspect_ollama_environment()})
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="SI-MCP-Doctor: Universal Windows AI, MCP & Ollama Fixer")
    parser.add_argument("command", nargs="?", default="web", choices=["web", "tk", "audit", "repair", "wire-proxy", "rollback", "proxy"])
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    args = parser.parse_args()

    if args.command == "audit":
        print(json.dumps({"audit": audit_all_clients(), "ollama": inspect_ollama_environment()}, indent=2))
    elif args.command == "repair":
        print(json.dumps(repair_all_clients(), indent=2))
    elif args.command == "wire-proxy":
        print(json.dumps(wire_claude_code_to_smart_proxy(proxy_port=args.proxy_port), indent=2))
    elif args.command == "rollback":
        res = rollback_latest_backup()
        print(json.dumps(res, indent=2))
        if not res.get("ok"):
            sys.exit(1)
    elif args.command == "proxy":
        px = OllamaSmartProxy(listen_port=args.proxy_port)
        px.start()
        print(f"Ollama 64K Context Proxy listening at http://127.0.0.1:{px.listen_port} -> :11434", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            px.stop()
    elif args.command == "tk":
        launch_tk_gui()
    elif args.command == "web":
        srv, px = start_web_server(port=args.port, proxy_port=args.proxy_port)
        url = f"http://127.0.0.1:{args.port}/"
        print(f"SI-MCP-Doctor Control Center live at {url} | Proxy :{px.listen_port}", flush=True)
        for browser in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ):
            if os.path.exists(browser):
                threading.Thread(target=lambda b=browser: subprocess.Popen([b, f"--app={url}"]), daemon=True).start()
                break
        try:
            srv.serve_forever()
        finally:
            px.stop()


if __name__ == "__main__":
    main()
