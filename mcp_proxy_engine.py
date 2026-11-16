"""
Core Windows MCP Config Doctor, Multi-Client Sync, Ollama Environment Fixer,
and Live Context/Message Repair Proxy (127.0.0.1:11435 -> 127.0.0.1:11434).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import winreg
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_PROXY_PORT = 11435
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_CTX_TOKENS = 65536


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_backup_root(custom_backup_dir: Path | None = None) -> Path:
    if custom_backup_dir is not None:
        return custom_backup_dir
    local_app = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return local_app / "SI-MCP-Doctor" / "backups"


def _read_hkcu_env(name: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            val, _ = winreg.QueryValueEx(k, name)
            return str(val)
    except OSError:
        return None


def _write_hkcu_env(name: str, value: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(value))


def tolerant_parse_json(raw_text: str) -> tuple[dict[str, Any] | None, bool, str | None]:
    """
    Parse MCP JSON files while recovering unescaped Windows backslash paths (e.g. C:\\Users\\foo -> valid JSON).
    Returns (parsed_dict, was_backslash_repaired, error_msg).
    """
    try:
        data = json.loads(raw_text)
        return (data if isinstance(data, dict) else {}, False, None)
    except json.JSONDecodeError as orig_err:
        # Repair invalid backslash escapes (anything other than standard JSON escapes \" \\ \/ \b \f \n \r \t \uXXXX)
        repaired_text = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", raw_text)
        try:
            data = json.loads(repaired_text)
            return (data if isinstance(data, dict) else {}, True, None)
        except json.JSONDecodeError:
            return (None, False, f"{orig_err.msg} (line {orig_err.lineno})")


def discover_ai_clients(
    custom_home: Path | None = None,
    custom_appdata: Path | None = None,
) -> list[dict[str, Any]]:
    """Locate Windows MCP client configuration files cleanly."""
    home = custom_home or Path.home()
    appdata = custom_appdata or Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))

    candidates = [
        ("claude_desktop", "Claude Desktop", appdata / "Claude" / "claude_desktop_config.json", "mcpServers"),
        ("claude_code_root", "Claude Code (Root ~/.claude.json)", home / ".claude.json", "mcpServers"),
        ("claude_code_settings", "Claude Code Settings (~/.claude/settings.json)", home / ".claude" / "settings.json", "env_or_misplaced"),
        ("antigravity_cfg", "Antigravity IDE (mcp_config.json)", home / ".gemini" / "config" / "mcp_config.json", "mcpServers"),
        ("antigravity_legacy", "Antigravity Legacy (~/.gemini/antigravity/mcp_config.json)", home / ".gemini" / "antigravity" / "mcp_config.json", "mcpServers"),
        ("cursor", "Cursor IDE", home / ".cursor" / "mcp.json", "mcpServers"),
        ("windsurf", "Windsurf IDE", home / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers"),
        ("vscode", "VS Code MCP", appdata / "Code" / "User" / "mcp.json", "servers"),
    ]
    out: list[dict[str, Any]] = []
    for cid, label, path, schema_key in candidates:
        out.append({
            "id": cid,
            "label": label,
            "path": str(path),
            "exists": path.exists(),
            "schema_key": schema_key,
            "size_bytes": path.stat().st_size if path.exists() else 0,
        })
    return out


def _looks_like_npx(cmd: str) -> bool:
    base = Path(cmd.strip().strip('"')).name.lower()
    return base in ("npx", "npx.cmd", "npm", "npm.cmd")


def _extract_servers_dict(data: dict[str, Any], schema_key: str) -> dict[str, Any]:
    if schema_key == "servers" and isinstance(data.get("servers"), dict):
        return data["servers"]
    if isinstance(data.get("mcpServers"), dict):
        return data["mcpServers"]
    return {}


def audit_all_clients(
    custom_home: Path | None = None,
    custom_appdata: Path | None = None,
) -> dict[str, Any]:
    """Audit every detected Windows AI client config for ENOENT, backslash breaks, misplaced MCPs & missing GUI env."""
    clients = discover_ai_clients(custom_home=custom_home, custom_appdata=custom_appdata)
    issues: list[dict[str, Any]] = []
    audited_clients: list[dict[str, Any]] = []
    total_servers = 0

    for cl in clients:
        p = Path(cl["path"])
        cl_issues: list[dict[str, str]] = []
        servers: dict[str, Any] = {}
        if cl["exists"]:
            raw = p.read_text(encoding="utf-8", errors="replace")
            data, was_backslash_fixed, err = tolerant_parse_json(raw)
            if data is None:
                cl_issues.append({
                    "id": "INVALID_JSON_SYNTAX",
                    "severity": "HIGH",
                    "server": "(file)",
                    "detail": f"Broken JSON syntax in {p.name}: {err}",
                })
            else:
                if was_backslash_fixed:
                    cl_issues.append({
                        "id": "BACKSLASH_PATH_ESCAPE",
                        "severity": "HIGH",
                        "server": "(file)",
                        "detail": f"Single Windows backslashes '\\' broke JSON escapes in {p.name}; auto-repairable.",
                    })

                if cl["id"] == "claude_code_settings" and isinstance(data.get("mcpServers"), dict) and data["mcpServers"]:
                    cl_issues.append({
                        "id": "CLAUDE_CODE_MISPLACED_MCP",
                        "severity": "HIGH",
                        "server": ", ".join(list(data["mcpServers"].keys())[:4]),
                        "detail": "Claude Code ignores mcpServers placed inside ~/.claude/settings.json — must be migrated to ~/.claude.json.",
                    })

                servers = _extract_servers_dict(data, cl["schema_key"])
                total_servers += len(servers)
                for srv_name, cfg in servers.items():
                    if not isinstance(cfg, dict):
                        continue
                    cmd = str(cfg.get("command", "") or "").strip()
                    args = cfg.get("args", []) or []
                    if cmd and _looks_like_npx(cmd):
                        cl_issues.append({
                            "id": "NPX_ENOENT_TRAP",
                            "severity": "HIGH",
                            "server": srv_name,
                            "detail": f"Uses raw '{cmd}' on Windows, triggering 'spawn npx ENOENT' in GUI apps instead of cmd.exe /c npx.",
                        })
                    # Check ESM raw Windows path passed to node/npx without file:// or normalized slashes
                    for a in args:
                        if isinstance(a, str) and re.match(r"^[A-Za-z]:\\[^\\].*", a) and a.lower().endswith((".mjs", ".js")):
                            cl_issues.append({
                                "id": "ESM_WINDOWS_PATH_RISK",
                                "severity": "MEDIUM",
                                "server": srv_name,
                                "detail": f"Raw Windows path argument '{a}' can trigger ERR_UNSUPPORTED_ESM_URL_SCHEME in Node ESM.",
                            })
                            break
                    # Check GUI missing env block when local/ollama/node is configured
                    if cmd and "env" not in cfg:
                        cl_issues.append({
                            "id": "MISSING_GUI_ENV_BLOCK",
                            "severity": "LOW",
                            "server": srv_name,
                            "detail": f"No explicit 'env' block on '{srv_name}' (Windows GUI clients drop user terminal env vars).",
                        })

        issues.extend([{**i, "client": cl["label"], "path": cl["path"]} for i in cl_issues])
        audited_clients.append({**cl, "server_count": len(servers), "issues": cl_issues})

    return {
        "ok": True,
        "clients": audited_clients,
        "existing_count": sum(1 for c in audited_clients if c["exists"]),
        "total_servers": total_servers,
        "issue_count": len(issues),
        "issues": issues,
    }


def _snapshot_backup(files_to_backup: list[Path], backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snap_dir = backup_dir / f"snapshot_{stamp}"
    snap_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"created_at": datetime.now(timezone.utc).isoformat(), "files": []}
    for src in files_to_backup:
        if src.exists():
            safe_name = hashlib.sha1(str(src).encode("utf-8")).hexdigest()[:8] + "_" + src.name
            dst = snap_dir / safe_name
            shutil.copy2(src, dst)
            manifest["files"].append({
                "original_path": str(src),
                "backup_file": safe_name,
                "sha256": sha256_file(src),
            })
    manifest_path = snap_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (backup_dir / "latest.txt").write_text(str(snap_dir), encoding="utf-8")
    return snap_dir


def rollback_latest_backup(custom_backup_dir: Path | None = None) -> dict[str, Any]:
    broot = get_backup_root(custom_backup_dir)
    latest_ptr = broot / "latest.txt"
    if not latest_ptr.exists():
        return {"ok": False, "error": "No SI-MCP-Doctor backup snapshot found."}
    snap_dir = Path(latest_ptr.read_text(encoding="utf-8").strip())
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "error": "Backup manifest missing."}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for meta in manifest.get("files", []):
        src = snap_dir / meta["backup_file"]
        dst = Path(meta["original_path"])
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if sha256_file(dst) != meta["sha256"]:
                return {"ok": False, "error": f"SHA-256 verification failed on {dst.name}"}
            restored.append(str(dst))
    return {"ok": True, "restored": restored, "snapshot": str(snap_dir)}


def _repair_server_entry(cfg: dict[str, Any], inject_env: bool = True, normalize_paths: bool = True) -> tuple[dict[str, Any], list[str]]:
    fixes: list[str] = []
    out = dict(cfg)
    cmd = str(out.get("command", "") or "").strip()
    args = list(out.get("args", []) or [])

    if cmd and _looks_like_npx(cmd):
        base = Path(cmd.strip().strip('"')).name.lower()
        clean_tool = "npm" if "npm" in base else "npx"
        out["command"] = "cmd.exe"
        out["args"] = ["/c", clean_tool, "-y", *[a for a in args if a != "-y"]] if clean_tool == "npx" else ["/c", clean_tool, *args]
        fixes.append(f"wrapped '{cmd}' in 'cmd.exe /c {clean_tool}' (fixes spawn ENOENT)")

    if normalize_paths and args:
        new_args = []
        changed = False
        for a in args:
            if isinstance(a, str) and re.match(r"^[A-Za-z]:\\", a):
                new_args.append(a.replace("\\", "/"))
                changed = True
            else:
                new_args.append(a)
        if changed:
            out["args"] = new_args
            fixes.append("normalized Windows path args to forward slashes (fixes JSON/ESM errors)")

    if inject_env and cmd:
        env = dict(out.get("env") or {})
        changed_env = False
        if "OLLAMA_HOST" not in env:
            env.setdefault("PYTHONIOENCODING", "utf-8")
            changed_env = True
        if changed_env or "env" not in out:
            out["env"] = env
            fixes.append("attached explicit GUI env block")

    return out, fixes


def repair_all_clients(
    options: dict[str, Any] | None = None,
    custom_home: Path | None = None,
    custom_appdata: Path | None = None,
    custom_backup_dir: Path | None = None,
) -> dict[str, Any]:
    """Apply Yes/No fixes across all detected Windows AI clients cleanly with SHA-256 backup."""
    opts = {
        "fix_npx_enoent": True,
        "fix_json_and_paths": True,
        "migrate_claude_misplaced": True,
        "inject_gui_env": True,
        **(options or {}),
    }
    clients = discover_ai_clients(custom_home=custom_home, custom_appdata=custom_appdata)
    existing_paths = [Path(c["path"]) for c in clients if c["exists"]]
    snap_dir = _snapshot_backup(existing_paths, get_backup_root(custom_backup_dir)) if existing_paths else None

    fixed_actions: list[dict[str, Any]] = []
    parsed_by_id: dict[str, tuple[Path, dict[str, Any], str]] = {}

    for cl in clients:
        p = Path(cl["path"])
        if not cl["exists"]:
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        data, was_bs_repaired, err = tolerant_parse_json(raw)
        if data is None:
            fixed_actions.append({"client": cl["label"], "ok": False, "detail": f"Skipped unparseable JSON: {err}"})
            continue
        if was_bs_repaired and opts["fix_json_and_paths"]:
            fixed_actions.append({"client": cl["label"], "ok": True, "detail": "Repaired broken backslash escapes in JSON"})
        parsed_by_id[cl["id"]] = (p, data, cl["schema_key"])

    # Migrate misplaced mcpServers from ~/.claude/settings.json -> ~/.claude.json
    if opts["migrate_claude_misplaced"] and "claude_code_settings" in parsed_by_id:
        sp, sdata, _ = parsed_by_id["claude_code_settings"]
        misplaced = sdata.pop("mcpServers", None)
        if isinstance(misplaced, dict) and misplaced:
            home = custom_home or Path.home()
            root_path = home / ".claude.json"
            if "claude_code_root" in parsed_by_id:
                rp, rdata, _ = parsed_by_id["claude_code_root"]
            else:
                rp = root_path
                rdata = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
                parsed_by_id["claude_code_root"] = (rp, rdata, "mcpServers")
            rmcp = rdata.setdefault("mcpServers", {})
            for k, v in misplaced.items():
                rmcp.setdefault(k, v)
            fixed_actions.append({"client": "Claude Code", "ok": True, "detail": f"Migrated {len(misplaced)} MCP servers from settings.json -> ~/.claude.json"})

    # Repair server entries inside each client
    for cid, (p, data, skey) in parsed_by_id.items():
        key = "servers" if (skey == "servers" and isinstance(data.get("servers"), dict)) else ("mcpServers" if isinstance(data.get("mcpServers"), dict) else None)
        if key:
            srvs = data[key]
            for sname, cfg in list(srvs.items()):
                if not isinstance(cfg, dict):
                    continue
                if opts["fix_npx_enoent"]:
                    repaired, msgs = _repair_server_entry(
                        cfg,
                        inject_env=bool(opts["inject_gui_env"]),
                        normalize_paths=bool(opts["fix_json_and_paths"]),
                    )
                    srvs[sname] = repaired
                    for m in msgs:
                        fixed_actions.append({"client": cid, "server": sname, "ok": True, "detail": m})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "snapshot": str(snap_dir) if snap_dir else None,
        "actions_applied": len(fixed_actions),
        "details": fixed_actions,
        "post_audit": audit_all_clients(custom_home=custom_home, custom_appdata=custom_appdata),
    }


def _probe_ollama_live(base_url: str = DEFAULT_OLLAMA_URL) -> dict[str, Any]:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models = [m.get("name", "") for m in payload.get("models", [])]
        return {"reachable": True, "models": models, "model_count": len(models)}
    except Exception as exc:
        return {"reachable": False, "models": [], "model_count": 0, "error": str(exc)}


def inspect_ollama_environment() -> dict[str, Any]:
    """Audit Ollama port 11434, context length env var, models path drift, and Node/uvx runtimes."""
    runtimes = {tool: shutil.which(tool) for tool in ("node", "npx", "python", "uvx", "ollama", "claude")}
    ctx_env = _read_hkcu_env("OLLAMA_CONTEXT_LENGTH") or os.environ.get("OLLAMA_CONTEXT_LENGTH")
    models_env = _read_hkcu_env("OLLAMA_MODELS") or os.environ.get("OLLAMA_MODELS")
    default_models_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Ollama"
    live = _probe_ollama_live(DEFAULT_OLLAMA_URL)

    ctx_ok = bool(ctx_env and int(str(ctx_env)) >= 32768) if (ctx_env and str(ctx_env).isdigit()) else False
    return {
        "ok": True,
        "runtimes": {k: {"installed": bool(v), "path": v} for k, v in runtimes.items()},
        "ollama_live": live,
        "ollama_context_length": ctx_env or "Default (4096/8192 — TRUNCATION RISK)",
        "ollama_context_ok": ctx_ok,
        "ollama_models_env": models_env or f"Default ({default_models_dir})",
        "models_drive_mismatch": bool(models_env and default_models_dir.exists()),
    }


def wire_claude_code_to_smart_proxy(
    proxy_port: int = DEFAULT_PROXY_PORT,
    num_ctx: int = DEFAULT_CTX_TOKENS,
    set_windows_env: bool = True,
    custom_home: Path | None = None,
    custom_backup_dir: Path | None = None,
) -> dict[str, Any]:
    """Configure Windows OLLAMA_CONTEXT_LENGTH + ~/.claude/settings.json env to route cleanly through our Context Proxy."""
    home = custom_home or Path.home()
    settings_path = home / ".claude" / "settings.json"
    if settings_path.exists():
        _snapshot_backup([settings_path], get_backup_root(custom_backup_dir))

    data: dict[str, Any] = {}
    if settings_path.exists():
        parsed, _, _ = tolerant_parse_json(settings_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(parsed, dict):
            data = parsed

    env = dict(data.get("env") or {})
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{proxy_port}"
    env["ANTHROPIC_API_KEY"] = "ollama"
    env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(num_ctx)
    data["env"] = env

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if set_windows_env:
        _write_hkcu_env("OLLAMA_CONTEXT_LENGTH", str(num_ctx))

    return {
        "ok": True,
        "settings_path": str(settings_path),
        "proxy_url": f"http://127.0.0.1:{proxy_port}",
        "num_ctx": num_ctx,
        "hkcu_env_set": set_windows_env,
    }


def sanitize_ollama_payload(path: str, payload: dict[str, Any], default_num_ctx: int = DEFAULT_CTX_TOKENS) -> tuple[dict[str, Any], dict[str, bool]]:
    """
    Fix the #1 Ollama + Claude Code / IDE bugs on the wire:
      1) Force options.num_ctx >= default_num_ctx so 2K/4K truncation stops immediately.
      2) Synthesize a clean 'user' turn if truncation dropped all user messages ('no user query found in messages').
      3) Pass through clean Anthropic/OpenAI compatible fields without mutating real user prompts.
    """
    out = dict(payload)
    flags = {"ctx_enforced": False, "empty_user_fixed": False}

    # Inject num_ctx cleanly for Ollama /api/chat, /api/generate, and OpenAI/Anthropic compat endpoints
    opts = dict(out.get("options") or {})
    cur_ctx = opts.get("num_ctx")
    if not isinstance(cur_ctx, int) or cur_ctx < default_num_ctx:
        opts["num_ctx"] = int(default_num_ctx)
        out["options"] = opts
        flags["ctx_enforced"] = True

    if path.startswith(("/api/chat", "/v1/chat/completions", "/v1/messages")):
        msgs = out.get("messages")
        if not isinstance(msgs, list) or len(msgs) == 0:
            sys_prompt = str(out.get("system") or "Proceed with the workspace task.")
            out["messages"] = [{"role": "user", "content": sys_prompt[:1200]}]
            flags["empty_user_fixed"] = True
        else:
            has_user = any(isinstance(m, dict) and m.get("role") == "user" and m.get("content") not in (None, "", []) for m in msgs)
            if not has_user:
                out["messages"] = [{"role": "user", "content": "Continue solving the active task based on tool results."}] + list(msgs)
                flags["empty_user_fixed"] = True

    return out, flags


class OllamaSmartProxy:
    """Live local HTTP proxy preventing 'no user query found in messages' and 4K context drops."""

    def __init__(self, upstream_url: str = DEFAULT_OLLAMA_URL, listen_port: int = DEFAULT_PROXY_PORT, default_num_ctx: int = DEFAULT_CTX_TOKENS) -> None:
        self.upstream_url = upstream_url.rstrip("/")
        self.listen_port = listen_port
        self.default_num_ctx = default_num_ctx
        self.lock = threading.Lock()
        self.stats: dict[str, Any] = {
            "running": False,
            "listen_url": f"http://127.0.0.1:{listen_port}",
            "upstream_url": self.upstream_url,
            "default_num_ctx": default_num_ctx,
            "requests_total": 0,
            "ctx_enforced_count": 0,
            "empty_user_repaired": 0,
            "upstream_errors": 0,
            "last_model": "none",
            "recent_events": [],
        }
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def get_stats(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.stats, recent_events=list(self.stats["recent_events"]))

    def start(self) -> int:
        if self._server is not None:
            return self.listen_port
        parent = self

        class ProxyHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def _forward(self, method: str) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length > 0 else b""
                send_body = raw_body
                flags = {"ctx_enforced": False, "empty_user_fixed": False}
                model_name = None

                if method == "POST" and raw_body:
                    try:
                        parsed = json.loads(raw_body.decode("utf-8"))
                        if isinstance(parsed, dict):
                            model_name = parsed.get("model")
                            sanitized, flags = sanitize_ollama_payload(self.path, parsed, parent.default_num_ctx)
                            send_body = json.dumps(sanitized).encode("utf-8")
                    except Exception:
                        pass

                with parent.lock:
                    parent.stats["requests_total"] += 1
                    if flags["ctx_enforced"]:
                        parent.stats["ctx_enforced_count"] += 1
                    if flags["empty_user_fixed"]:
                        parent.stats["empty_user_repaired"] += 1
                    if model_name:
                        parent.stats["last_model"] = str(model_name)
                    ev = f"{method} {self.path} | ctx_enforced={flags['ctx_enforced']} | user_fixed={flags['empty_user_fixed']}"
                    parent.stats["recent_events"] = (parent.stats["recent_events"] + [ev])[-12:]

                target = f"{parent.upstream_url}{self.path}"
                fwd_headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
                if self.headers.get("Authorization"):
                    fwd_headers["Authorization"] = self.headers["Authorization"]
                if self.headers.get("x-api-key"):
                    fwd_headers["x-api-key"] = self.headers["x-api-key"]

                req = urllib.request.Request(target, data=send_body if method == "POST" else None, headers=fwd_headers, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=180) as up:
                        self.send_response(up.status)
                        ct = up.headers.get("Content-Type", "application/json")
                        self.send_header("Content-Type", ct)
                        self.end_headers()
                        while True:
                            chunk = up.read(4096)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except urllib.error.HTTPError as he:
                    err_body = he.read()
                    self.send_response(he.code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err_body)))
                    self.end_headers()
                    self.wfile.write(err_body)
                except Exception as exc:
                    with parent.lock:
                        parent.stats["upstream_errors"] += 1
                    err_json = json.dumps({"error": f"Upstream Ollama unreachable at {parent.upstream_url}: {exc}"}).encode("utf-8")
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err_json)))
                    self.end_headers()
                    self.wfile.write(err_json)

            def do_GET(self) -> None:
                self._forward("GET")

            def do_POST(self) -> None:
                self._forward("POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", self.listen_port), ProxyHandler)
        self.listen_port = int(self._server.server_address[1])
        with self.lock:
            self.stats["running"] = True
            self.stats["listen_url"] = f"http://127.0.0.1:{self.listen_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.listen_port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        with self.lock:
            self.stats["running"] = False
