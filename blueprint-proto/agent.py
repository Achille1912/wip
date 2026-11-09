"""Runs the coding agent on a worktree through the Claude Agent SDK. What the agent may do without asking is decided by decide();
anything else becomes a permission REQUEST: a file in <state>/perm/ that the viewer shows as a notification. The agent waits for the
engineer's answer there (allow once / allow for this run / deny); no answer within WAIT seconds counts as a denial."""
import asyncio
import json
import re
import secrets
import time
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow, PermissionResultDeny, ResultMessage

WAIT = 900
# never asked, always refused: destructive commands, network access, history rewriting, installing packages
DENY_CMD = re.compile(r"(?i)(^|[\s;&|(])(rm|rmdir|del|rd|remove-item|curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|shutdown|reg)(\s|$)|git\s+(push|reset|clean|remote)|(pip|npm)\s+install")
PATH_KEYS = ("file_path", "path", "notebook_path")
SAFE_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write", "MultiEdit", "NotebookEdit", "TodoWrite")


def decide(tool, inp, wt):
    """'allow', 'deny: <reason>' or 'ask'. wt = the resolved worktree: nothing outside it is touched."""
    for k in PATH_KEYS:
        if isinstance(inp.get(k), str) and not (wt / inp[k]).resolve().is_relative_to(wt):
            return "deny: that path is outside the worktree"
    if tool in ("WebFetch", "WebSearch"):
        return "deny: there is no network access during a run"
    if tool == "Bash":
        m = DENY_CMD.search(inp.get("command", ""))
        return f"deny: `{m.group(0).strip()}` is never allowed during a run" if m else "ask"
    return "allow" if tool in SAFE_TOOLS else "ask"


def script_head(cmd, wt):
    """If the command runs a Python script of the worktree, its first lines: the engineer should see what he is allowing."""
    m = re.search(r"python[\d.]*\s+(?:-[\w-]+\s+)*([\w./\\-]+\.py)", cmd)
    p = (wt / m.group(1)).resolve() if m else None
    if p and p.is_file() and p.is_relative_to(wt):
        return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[:60])[:3000]
    return ""


async def ask_user(perm_dir, tool, inp, wt):
    rid = secrets.token_hex(4)
    req, res = perm_dir / f"{rid}.req.json", perm_dir / f"{rid}.res.json"
    text = inp.get("command", "") if tool == "Bash" else json.dumps(inp, ensure_ascii=False)
    req.write_text(json.dumps({"id": rid, "tool": tool, "text": str(text)[:800], "script": script_head(text, wt) if tool == "Bash" else "", "at": time.time()}), encoding="utf-8")
    try:
        for _ in range(WAIT):
            await asyncio.sleep(1)
            if res.exists():
                try:
                    return json.loads(res.read_text(encoding="utf-8")).get("decision")
                except ValueError:  # being written
                    continue
        return "timeout"
    finally:
        req.unlink(missing_ok=True)
        res.unlink(missing_ok=True)


async def run_agent(prompt, wt, state, model="sonnet"):
    """Returns (the agent's final text, permission log). Raises RuntimeError if the agent run fails."""
    wt, perm_dir, log, granted = Path(wt).resolve(), Path(state) / "perm", [], set()
    perm_dir.mkdir(parents=True, exist_ok=True)
    for p in perm_dir.glob("*"):
        p.unlink()

    async def can_use_tool(tool, inp, ctx):
        d = decide(tool, inp, wt)
        text = str(inp.get("command") if tool == "Bash" else json.dumps(inp, ensure_ascii=False))[:300]
        if d == "allow":
            return PermissionResultAllow()
        if d.startswith("deny"):
            log.append({"tool": tool, "text": text, "decision": "denied automatically: " + d[6:]})
            return PermissionResultDeny(message=d[6:])
        first = re.match(r"\s*([\w.\\/:-]+)", text)
        key = (tool, first.group(1) if tool == "Bash" and first else "")  # "for this run" = this tool, and for Bash this program
        if key in granted:
            log.append({"tool": tool, "text": text, "decision": "allowed (granted earlier in this run)"})
            return PermissionResultAllow()
        ans = await ask_user(perm_dir, tool, inp, wt)
        if ans in ("once", "run"):
            if ans == "run":
                granted.add(key)
            log.append({"tool": tool, "text": text, "decision": "allowed once" if ans == "once" else "allowed for this run"})
            return PermissionResultAllow()
        log.append({"tool": tool, "text": text, "decision": "timed out" if ans == "timeout" else "denied"})
        return PermissionResultDeny(message="The engineer did not grant this request." if ans != "timeout" else "The engineer did not answer in time.")

    opts = ClaudeAgentOptions(cwd=wt, model=model, permission_mode="acceptEdits", allowed_tools=["Read", "Glob", "Grep", "TodoWrite"],
                              can_use_tool=can_use_tool, setting_sources=["project"])  # project settings: the repo's own CLAUDE.md
    final = None
    async with ClaudeSDKClient(opts) as client:
        await client.query(prompt)
        async for m in client.receive_response():
            if isinstance(m, ResultMessage):
                final = m
    if final is None or final.is_error:
        raise RuntimeError(((final and final.result) or "the agent ended without a result")[:300])
    return final.result or "", log
