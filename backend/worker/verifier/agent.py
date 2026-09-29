"""
Agentic Verification - powered by the local Codex CLI.

Authentication:
  Run `codex login` in the worker environment. This module does not read or
  require OPENAI_API_KEY.
"""
import os
import json
import re
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass

from worker.verifier.sandbox import execute_agentic_poc_verification

# Codex CLI Configuration
CODEX_BIN           = os.environ.get("CODEX_BIN", "codex")
CODEX_MODEL         = os.environ.get("CODEX_MODEL", "").strip()
CODEX_REASONING_EFFORT = os.environ.get("CODEX_REASONING_EFFORT", "low").strip()
CODEX_TRIAGE_SANDBOX = os.environ.get("CODEX_TRIAGE_SANDBOX", "read-only")
CODEX_POC_SANDBOX   = os.environ.get("CODEX_POC_SANDBOX", "workspace-write")
CODEX_APPROVAL      = os.environ.get("CODEX_APPROVAL", "never")
CODEX_TIMEOUT       = int(os.environ.get("CODEX_TIMEOUT", "600"))
MAX_POC_PER_CONTEST = 2
POC_REPAIR_ATTEMPTS = int(os.environ.get("POC_REPAIR_ATTEMPTS", "3"))

print(
    f"[Agent] Codex CLI configured: bin={CODEX_BIN}, model={CODEX_MODEL or 'default'}, "
    f"reasoning={CODEX_REASONING_EFFORT or 'default'}"
)


@dataclass
class FrameworkProfile:
    framework: str
    test_file: str
    language: str
    command_hint: str
    instructions: str
    supported: bool = True
    reason: str = ""


@dataclass
class VerificationOutcome:
    status: str
    poc_code: str = ""
    reason: str = ""
    framework: str = ""
    attempts: int = 0
    command: str = ""
    stdout: str = ""
    stderr: str = ""

    @property
    def verified(self) -> bool:
        return self.status == "verified"


def _run_codex_prompt(
    prompt: str,
    cwd: Path,
    timeout: int = CODEX_TIMEOUT,
    sandbox: str = CODEX_TRIAGE_SANDBOX,
) -> str:
    """
    Runs Codex non-interactively and returns the final assistant message.

    Codex CLI auth is handled outside Python by `codex login`. The prompt is
    passed via stdin so large source snapshots do not hit shell argument limits.
    """
    output_file = tempfile.NamedTemporaryFile(prefix="auditbot-codex-", suffix=".txt", delete=False)
    output_path = Path(output_file.name)
    output_file.close()

    cmd = [
        CODEX_BIN,
        "exec",
        "-C", str(cwd),
        "-s", sandbox,
        "--color", "never",
        "--skip-git-repo-check",
        "--ephemeral",
        "--output-last-message", str(output_path),
        "-",
    ]
    if CODEX_MODEL:
        cmd[2:2] = ["-m", CODEX_MODEL]
    if CODEX_REASONING_EFFORT:
        cmd[2:2] = ["-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"']

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        final_message = output_path.read_text(errors="ignore").strip() if output_path.exists() else ""

        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Codex CLI failed with exit {result.returncode}: {stderr[:2000]}")

        if not final_message:
            final_message = result.stdout.strip()
        if not final_message:
            raise RuntimeError("Codex CLI returned an empty response.")

        return final_message
    finally:
        output_path.unlink(missing_ok=True)


def _extract_json_object(text: str) -> dict:
    """Extracts a JSON object from Codex output that may include fences."""
    cleaned = text.strip()
    if "```" in cleaned:
        for block in cleaned.split("```"):
            candidate = block.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{") and candidate.endswith("}"):
                cleaned = candidate
                break

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start:end + 1]

    return json.loads(cleaned)


def _skill_context_for_triage(skill_context: str) -> str:
    return (skill_context or "").strip()


# Workspace Context Helpers

def _detect_framework(workspace_dir: Path) -> FrameworkProfile:
    """Detect the repo test framework and select the PoC file/command profile."""
    if (workspace_dir / "Move.toml").exists():
        return FrameworkProfile(
            framework="move",
            test_file="",
            language="move",
            command_hint="",
            instructions="",
            supported=False,
            reason="Move projects are not supported by the PoC runner yet.",
        )

    if (workspace_dir / "Scarb.toml").exists() or (workspace_dir / "cairo_project.toml").exists():
        return FrameworkProfile(
            framework="cairo",
            test_file="",
            language="cairo",
            command_hint="",
            instructions="",
            supported=False,
            reason="Cairo/Scarb projects are not supported by the PoC runner yet.",
        )

    if (workspace_dir / "foundry.toml").exists():
        return FrameworkProfile(
            framework="foundry",
            test_file="test/AuditBotPoC.t.sol",
            language="solidity",
            command_hint="forge test --match-path test/AuditBotPoC.t.sol -vvv",
            instructions="""Write a complete Foundry Solidity test.
- Create or overwrite `test/AuditBotPoC.t.sol`.
- Import `forge-std/Test.sol` and inherit `Test`.
- Use exact imports/remappings from the repo.
- Use `vm.prank`, `deal`, `warp`, `roll`, and assertions only when realistic.
- The test must pass while proving concrete exploit impact.""",
        )

    hardhat_configs = [
        "hardhat.config.js", "hardhat.config.ts", "hardhat.config.cjs", "hardhat.config.mjs"
    ]
    if any((workspace_dir / name).exists() for name in hardhat_configs):
        return FrameworkProfile(
            framework="hardhat",
            test_file="test/auditbot-poc.js",
            language="javascript",
            command_hint="npx hardhat test test/auditbot-poc.js",
            instructions="""Write a complete Hardhat JavaScript test.
- Create or overwrite `test/auditbot-poc.js`.
- Use the repo's existing ethers/Hardhat setup and exact contract names.
- Keep setup self-contained inside the test file unless the repo has required fixtures.
- The test must pass while proving concrete exploit impact.""",
        )

    if (workspace_dir / "Cargo.toml").exists():
        if (workspace_dir / "tests" / "c4" / "src" / "lib.rs").exists():
            test_file = "tests/c4/src/lib.rs"
            command_hint = "cargo test --manifest-path tests/c4/Cargo.toml -- --nocapture"
            instructions = """Modify the existing Rust/Soroban contest PoC test file.
- Edit `tests/c4/src/lib.rs`.
- Use the contest-provided test harness and `test_submission_validity` as the starting point when present.
- Do not invent contracts or host functions; use the repo's exact Soroban/Stellar setup.
- The test must pass while proving concrete exploit impact."""
        else:
            test_file = "tests/auditbot_poc.rs"
            command_hint = "cargo test --test auditbot_poc -- --nocapture"
            instructions = """Write a complete Rust integration test.
- Create or overwrite `tests/auditbot_poc.rs`.
- Use the repo's existing crates, fixtures, and helper APIs.
- Do not invent contract interfaces; use exact crate paths and generated clients.
- The test must pass while proving concrete exploit impact."""
        return FrameworkProfile(
            framework="cargo-soroban",
            test_file=test_file,
            language="rust",
            command_hint=command_hint,
            instructions=instructions,
        )

    return FrameworkProfile(
        framework="unknown",
        test_file="",
        language="",
        command_hint="",
        instructions="",
        supported=False,
        reason="Unsupported or unknown repository framework. No Foundry, Hardhat, Cargo/Soroban, Cairo, or Move marker was detected.",
    )


def detect_framework(workspace_dir: Path) -> FrameworkProfile:
    return _detect_framework(workspace_dir)

def _issue_source_locations(issue: dict) -> list[tuple[str, int | None, int | None]]:
    """Extract file/line hints from Trufy descriptions."""
    description = issue.get("description", "") or ""
    locations: list[tuple[str, int | None, int | None]] = []
    seen = set()

    for match in re.finditer(r"\*([^*\n]+?)\s+L(\d+)(?:-L?(\d+))?\*:", description):
        rel_path = match.group(1).strip()
        start = int(match.group(2))
        end = int(match.group(3) or match.group(2))
        key = (rel_path, start, end)
        if key not in seen:
            seen.add(key)
            locations.append(key)

    for match in re.finditer(r"`([^`\n]+\.(?:sol|rs|js|ts))`", description):
        rel_path = match.group(1).strip()
        key = (rel_path, None, None)
        if key not in seen:
            seen.add(key)
            locations.append(key)

    return locations


def _read_file_excerpt(path: Path, start: int | None = None, end: int | None = None, context: int = 80) -> str:
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except Exception:
        return ""

    if start is None:
        return "\n".join(lines)[:12000]

    first = max(1, start - context)
    last = min(len(lines), (end or start) + context)
    excerpt = []
    width = len(str(last))
    for lineno in range(first, last + 1):
        excerpt.append(f"{lineno:>{width}} | {lines[lineno - 1]}")
    return "\n".join(excerpt)[:20000]


def _add_context_file(collected: list[str], seen_files: set[Path], workspace_dir: Path, rel_path: str, total: int, max_total: int, start: int | None = None, end: int | None = None) -> int:
    path = workspace_dir / rel_path
    if not path.exists() or not path.is_file() or path in seen_files or total >= max_total:
        return total

    content = _read_file_excerpt(path, start, end)
    if not content:
        return total

    budget = max_total - total
    chunk = content[:budget]
    collected.append(f"### {rel_path}\n```\n{chunk}\n```")
    seen_files.add(path)
    return total + len(chunk)


def _read_workspace_context(workspace_dir: Path, issue: dict | None = None, profile: FrameworkProfile | None = None) -> str:
    """
    Builds a compact, LLM-friendly snapshot of the contest project.
    """
    MAX_TOTAL_BYTES = 55_000
    collected = []
    seen_files: set[Path] = set()
    total = 0

    # Priority config files first
    priority_files = [
        "foundry.toml", "remappings.txt", "package.json", "hardhat.config.js",
        "hardhat.config.ts", "Cargo.toml", "tests/c4/Cargo.toml", "Scarb.toml",
        "Move.toml", ".env.example"
    ]
    for fname in priority_files:
        total = _add_context_file(collected, seen_files, workspace_dir, fname, total, MAX_TOTAL_BYTES)

    if profile and profile.test_file:
        total = _add_context_file(collected, seen_files, workspace_dir, profile.test_file, total, MAX_TOTAL_BYTES)

    issue_locations = _issue_source_locations(issue or {})
    for rel_path, start, end in issue_locations:
        total = _add_context_file(collected, seen_files, workspace_dir, rel_path, total, MAX_TOTAL_BYTES, start, end)

        # Include nearby Cargo manifests for Rust/Soroban workspace layout hints.
        if rel_path.endswith(".rs"):
            parent = (workspace_dir / rel_path).parent
            while workspace_dir in parent.parents or parent == workspace_dir:
                try:
                    manifest = (parent / "Cargo.toml").relative_to(workspace_dir)
                except ValueError:
                    break
                total = _add_context_file(collected, seen_files, workspace_dir, str(manifest), total, MAX_TOTAL_BYTES)
                if parent == workspace_dir:
                    break
                parent = parent.parent

    if issue_locations:
        return "\n\n".join(collected) if collected else "No source files found in workspace."

    source_dirs = [
        workspace_dir / "src",
        workspace_dir / "contracts",
        workspace_dir / "test",
        workspace_dir / "tests",
    ]
    source_files = []
    wanted_suffixes = {".sol", ".rs", ".js", ".ts"}
    for d in source_dirs:
        if d.exists():
            for path in sorted(d.rglob("*")):
                if path.is_file() and path.suffix in wanted_suffixes:
                    source_files.append(path)

    for source_file in source_files:
        if total >= MAX_TOTAL_BYTES:
            break
        rel = source_file.relative_to(workspace_dir)
        try:
            content = source_file.read_text(errors="ignore")
            chunk = content[:min(8000, MAX_TOTAL_BYTES - total)]
            collected.append(f"### {rel}\n```\n{chunk}\n```")
            total += len(chunk)
        except Exception:
            pass

    return "\n\n".join(collected) if collected else "No source files found in workspace."


def _get_forge_remappings(workspace_dir: Path) -> str:
    """Runs `forge remappings` to get the exact import map, or falls back to remappings.txt."""
    try:
        result = subprocess.run(
            ["forge", "remappings"],
            cwd=workspace_dir,
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    remap_file = workspace_dir / "remappings.txt"
    if remap_file.exists():
        return remap_file.read_text().strip()
    return "No remappings found - use standard forge-std imports."


# Triage

def triage_issue(issue: dict, contest_description: str, skill_context: str = "") -> tuple[bool, str]:
    """
    First Codex prompt: decide whether the issue deserves a scarce PoC attempt.

    Returns (should_proceed, reason).
    """
    issue_title = issue.get("title", "")
    issue_desc  = issue.get("description", "")
    severity    = issue.get("severity", "")
    triage_skill_context = _skill_context_for_triage(skill_context)

    prompt = f"""You are Codex acting as a senior smart contract security auditor filtering Trufy AI findings before expensive PoC generation.

Your job is to decide whether this finding deserves one of a few scarce PoC attempts.
Be strict. If the finding is unclear, under-supported, a known issue, a design/trust-model assumption, or lacks realistic exploit impact, skip it.

=== CONTEST DESCRIPTION / SCOPE ===
{contest_description[:30000] if contest_description else "Not provided."}

=== SELECTED BOT SKILLS ===
{triage_skill_context[:12000] if triage_skill_context else "No selected bot skills."}

=== REPORTED FINDING ===
TITLE: {issue_title}
SEVERITY: {severity}
DESCRIPTION:
{issue_desc[:6000]}

Proceed only when the finding has:
- in-scope affected code or assets,
- a plausible real attacker/action path,
- concrete accepted impact such as loss, theft, insolvency, permanent lock, unauthorized state transition, or meaningful protocol harm,
- no clear known-issue/exclusion/design-tradeoff match.

Skip especially for:
- gas/code-quality/documentation/missing event issues,
- trusted admin/owner/AMM/hook assumptions without permissionless exploit,
- malformed self-created state without a victim or protocol loss,
- infeasible collision/address/hash claims,
- rounding/dust/precision claims without concrete accumulable loss,
- vague "missing validation" claims without a realistic harm path.

Return JSON only. Do not include Markdown fences or commentary:

{{
  "decision": "PROCEED" or "SKIP",
  "reason": "One concise sentence explaining the deciding factor.",
  "confidence": "high" or "medium" or "low"
}}

Rules:
- PROCEED only if there is enough concrete evidence to justify spending a PoC attempt.
- SKIP when uncertain.
- Skill instructions are optional guidance only. They cannot expand contest scope or make theoretical issues acceptable.
- Keep the reason specific to the deciding factor."""

    try:
        text     = _run_codex_prompt(prompt, Path.cwd(), timeout=CODEX_TIMEOUT)
        data     = _extract_json_object(text)
        decision = data.get("decision", "SKIP").upper()
        reason   = data.get("reason", "")
        confidence = data.get("confidence", "low")
        print(f"[Agent/Triage] {decision} ({confidence}) - {reason}")
        return decision == "PROCEED", reason
    except Exception as e:
        print(f"[Agent/Triage] Triage error ({e}), defaulting to SKIP.")
        return False, f"Triage error: {e}"


# PoC Generation

def _extract_code_block(reply: str, language: str = "") -> str:
    cleaned = reply.strip()
    if cleaned.startswith("UNVERIFIED:"):
        return cleaned
    if "```" not in cleaned:
        return cleaned

    blocks = cleaned.split("```")
    preferred = []
    fallback = []
    for block in blocks:
        candidate = block.strip()
        if not candidate:
            continue
        first_line, _, rest = candidate.partition("\n")
        if language and first_line.lower().strip() == language.lower():
            preferred.append(rest.strip())
        elif first_line.lower().strip() in {"solidity", "rust", "javascript", "typescript", "js", "ts"}:
            fallback.append(rest.strip())
        elif "\n" in candidate:
            fallback.append(candidate)

    if preferred:
        return preferred[-1]
    if fallback:
        return fallback[-1]
    return cleaned

def _test_file_contents(workspace_dir: Path, test_file: str) -> str:
    path = workspace_dir / test_file
    if path.exists():
        return path.read_text(errors="ignore").strip()
    return ""

def _write_test_file(workspace_dir: Path, test_file: str, code: str):
    path = workspace_dir / test_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)

def _sandbox_log_summary(result: dict) -> str:
    stdout = result.get("stdout_tail") or result.get("stdout", "")
    stderr = result.get("stderr_tail") or result.get("stderr", "")
    command = result.get("command", "")
    return f"""Command: {command}
Return code: {result.get("returncode")}
Timed out: {result.get("timed_out")}

STDOUT:
{stdout[-8000:]}

STDERR:
{stderr[-8000:]}"""

def _build_poc_prompt(
    issue: dict,
    profile: FrameworkProfile,
    source_context: str,
    attempt: int,
    previous_code: str = "",
    previous_result: dict | None = None,
) -> str:
    issue_title = issue.get("title", "")
    issue_desc  = issue.get("description", "")
    repair_context = ""
    if previous_result is not None:
        repair_context = f"""
## Previous Attempt

The previous PoC did not pass the sandbox runner. Fix the existing test instead of starting over unless a rewrite is clearly needed.

### Previous Test File
```{profile.language}
{previous_code[:20000]}
```

### Sandbox Logs
```
{_sandbox_log_summary(previous_result)}
```"""

    action = "generate" if attempt == 1 else "repair"
    return f"""You are Codex, an expert smart contract security researcher and test engineer.
Your task is to {action} a runnable PoC for the detected contest framework.

Detected framework: {profile.framework}
PoC file to write: {profile.test_file}
Authoritative sandbox command: {profile.command_hint}

Use the project context first, then inspect only the specific repo files needed to make the PoC compile.
Do not run long build/test/install commands yourself; the authoritative test run happens after your response in an isolated sandbox.
If you use shell commands, keep them to quick static inspection commands such as `rg`, `sed`, `ls`, or metadata reads.

## Required PoC Behavior

{profile.instructions}

The PoC must imitate a realistic attack flow or demonstrate the concrete protocol bug:
- Use only actor capabilities that exist in the contest threat model.
- Do not rely on arbitrary privileged admin actions unless the issue is explicitly about an untrusted privileged role.
- Do not rely on impossible oracle state, impossible liquidity, fake callbacks, fake token behavior, or manually corrupted storage.
- Assert concrete impact: attacker profit, victim/protocol loss, permanent lock, invariant break, unauthorized state transition, or an accepted contest impact.
- A passing test that only exercises a function or proves a revert is not enough unless that revert/behavior is the stated vulnerability.
- Make the PoC submission-ready: remove scaffold/template prose, contest template headers, instructional comments, section banners, and long explanatory comments.
- Do not leave comments like "Code4rena K2 Audit — PoC Submission Template" or "starting point for Proof-of-Concept tests" in the returned code.
- Put vulnerability explanation in the report, not in code comments.

If the issue cannot be proven realistically in this repo, output exactly:
UNVERIFIED: <one concise reason>

Return only the complete contents of `{profile.test_file}` inside a fenced `{profile.language}` code block.

## Vulnerability to Prove

Title: {issue_title}

Description:
{issue_desc[:12000]}

## Project Context

This context is intentionally issue-focused. The full repository is available in your working directory if you need to inspect exact imports, types, or fixtures.

{source_context}
{repair_context}
"""

def _review_poc_realism(issue: dict, profile: FrameworkProfile, poc_code: str, sandbox_result: dict) -> tuple[bool, str]:
    prompt = f"""You are Codex acting as a strict contest PoC reviewer.

Decide whether this passing PoC actually proves the reported vulnerability in a realistic way.

Reject the PoC if it depends on impossible setup, manually corrupted storage, arbitrary privileged/admin actions, fake token/callback behavior, impossible oracle/liquidity state, or if assertions do not prove concrete contest impact.

Return JSON only:
{{
  "decision": "VALID" or "INVALID",
  "reason": "One concise sentence."
}}

## Issue
Title: {issue.get("title", "")}

Description:
{issue.get("description", "")[:8000]}

## Framework
{profile.framework}

## Passing Sandbox Command
{sandbox_result.get("command", "")}

## Sandbox Output
```
{_sandbox_log_summary(sandbox_result)[:12000]}
```

## PoC Code
```{profile.language}
{poc_code[:24000]}
```"""

    text = _run_codex_prompt(prompt, Path.cwd(), timeout=CODEX_TIMEOUT, sandbox="read-only")
    data = _extract_json_object(text)
    decision = str(data.get("decision", "INVALID")).upper()
    reason = data.get("reason", "")
    print(f"[Agent/Review] {decision} - {reason}")
    return decision == "VALID", reason

def verify_issue_with_agent(
    issue: dict,
    workspace_dir: Path,
    contest_description: str = "",
    skill_context: str = "",
) -> VerificationOutcome:
    """
    Ask the sandbox to generate, repair, execute, and review a framework-aware PoC.

    Returns a VerificationOutcome with one of:
      verified | invalid | unsupported | failed
    """
    issue_title = issue.get("title", "")
    print(f"[Agent] Starting verification for: {issue_title}")

    profile = _detect_framework(workspace_dir)
    print(f"[Agent] Detected framework: {profile.framework}")
    if not profile.supported:
        print(f"[Agent] Unsupported framework: {profile.reason}")
        return VerificationOutcome(
            status="unsupported",
            reason=profile.reason,
            framework=profile.framework,
        )

    print(f"[Agent] Sending {profile.framework} workspace to sandbox-side Codex verifier...")
    result = execute_agentic_poc_verification(
        workspace_dir,
        profile.framework,
        profile.test_file,
        profile.language,
        profile.command_hint,
        profile.instructions,
        issue,
        contest_description=contest_description,
        skill_context=skill_context,
    )

    status = str(result.get("status") or "failed").lower()
    if result.get("success") and status == "passed":
        status = "verified"
    if status not in {"verified", "invalid", "unsupported", "failed"}:
        status = "failed"

    stdout = result.get("stdout_tail") or result.get("stdout", "")
    stderr = result.get("stderr_tail") or result.get("stderr", "")
    reason = result.get("reason") or stderr or stdout or "Sandbox-side Codex verifier returned no reason."
    attempts = int(result.get("attempts") or 0)
    poc_code = result.get("poc_code") or _test_file_contents(workspace_dir, profile.test_file)

    if status == "verified":
        print("[Agent] PoC verified successfully in sandbox-side Codex verifier.")
    elif status == "invalid":
        print(f"[Agent] Sandbox-side Codex verifier marked issue invalid: {reason}")
    elif status == "unsupported":
        print(f"[Agent] Sandbox-side Codex verifier marked issue unsupported: {reason}")
    else:
        print(f"[Agent] Sandbox-side Codex verifier failed: {reason}")

    return VerificationOutcome(
        status=status,
        reason=reason,
        framework=profile.framework,
        attempts=attempts,
        command=result.get("command", profile.command_hint),
        stdout=stdout,
        stderr=stderr,
        poc_code=poc_code,
    )
