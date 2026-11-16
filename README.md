# SI-MCP-Doctor

Universal **1-Click Windows Auto-Fixer & Live Ollama 64K Context Proxy** for **Claude Desktop, Claude Code, Antigravity, Cursor, Windsurf, VS Code & Local Ollama**.

Stop debugging cryptic Windows JSON errors by hand. **SI-MCP-Doctor** scans every AI client on your PC, creates a SHA-256 snapshot backup, repairs Windows-specific failures in one click, and runs a local Context Guard Proxy (`127.0.0.1:11435 -> 127.0.0.1:11434`) to prevent local model truncation crashes.

## Real Universal Windows Bugs Fixed Automatically

| Bug | Root Cause on Windows | SI-MCP-Doctor 1-Click Fix |
| --- | --- | --- |
| **`spawn npx ENOENT`** | GUI apps fail to run `npx` because on Windows it is `npx.cmd` | Automatically wraps raw `npx` / `npm` entries with `cmd.exe /c npx -y ...` |
| **Broken JSON `\` & Node `ERR_UNSUPPORTED_ESM_URL_SCHEME`** | Single Windows backslashes break JSON parsing and ESM file loads | Recovers broken backslash JSON files and normalizes script path separators cleanly |
| **Silently Ignored Claude Code MCPs** | Placing `mcpServers` inside `~/.claude/settings.json` instead of `~/.claude.json` | Detects misplaced servers and migrates them cleanly into `~/.claude.json` |
| **Missing GUI Environment Variables** | GUI apps strip custom terminal variables when launching MCP subprocesses | Attaches explicit `env` blocks automatically to every MCP entry |
| **`"no user query found in messages"` & 4K Context Drops** | Default 2K/4K Ollama windows truncate large AI coding system prompts | Live **64K Context Proxy** (`:11435`) enforces `num_ctx: 65536` and repairs empty user turns in-flight + sets `OLLAMA_CONTEXT_LENGTH=65536` |

## Quick Start

Double-click **`Launch_SI_MCP_Doctor.bat`** or run:

```powershell
# Launch 1-Click Dark Desktop UI + Live 64K Context Proxy
python si_mcp_doctor.py web

# Native Desktop Window (no browser needed)
python si_mcp_doctor.py tk

# CLI Audit, 1-Click Fix & SHA-256 Rollback
python si_mcp_doctor.py audit
python si_mcp_doctor.py repair
python si_mcp_doctor.py rollback
```

## Automated Tests

```powershell
python -m unittest test_mcp_doctor.py -v
```
