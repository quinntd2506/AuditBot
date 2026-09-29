import os
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse

app = FastAPI()

SUPPORTED_FRAMEWORKS = {"foundry", "hardhat", "cargo-soroban"}
TIMEOUT_SECONDS = int(os.environ.get("SANDBOX_TEST_TIMEOUT", "1800"))
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
CODEX_MODEL = os.environ.get("CODEX_MODEL", "").strip()
CODEX_REASONING_EFFORT = os.environ.get("CODEX_REASONING_EFFORT", "low").strip()
CODEX_AGENT_TIMEOUT = int(os.environ.get("CODEX_AGENT_TIMEOUT", "3600"))
CODEX_AGENT_BYPASS = os.environ.get("CODEX_AGENT_BYPASS", "1").lower() not in {"0", "false", "no"}
CODEX_AGENT_SANDBOX = os.environ.get("CODEX_AGENT_SANDBOX", "workspace-write")
SANDBOX_CACHE_DIR = Path(os.environ.get("SANDBOX_CACHE_DIR", "/cache"))
RPC_ENV_VARS = [
    "MAINNET_RPC_URL",
    "ETHEREUM_RPC_URL",
    "ETH_RPC_URL",
    "FOUNDRY_ETH_RPC_URL",
    "ARBITRUM_RPC_URL",
    "BASE_RPC_URL",
    "OPTIMISM_RPC_URL",
    "POLYGON_RPC_URL",
    "BSC_RPC_URL",
    "AVALANCHE_RPC_URL",
]
EVM_CHAIN_HINTS = (
    "ethereum", "mainnet", "arbitrum", "optimism", "base", "polygon",
    "bsc", "binance", "avalanche", "fantom", "gnosis", "sepolia"
)

def _safe_extract(zip_path: Path, extract_dir: Path):
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        for member in zip_ref.infolist():
            target = (extract_dir / member.filename).resolve()
            if not str(target).startswith(str(extract_dir.resolve())):
                raise ValueError(f"Unsafe zip entry: {member.filename}")
        zip_ref.extractall(extract_dir)

def _tail(text: str, limit: int = 12000) -> str:
    if not text:
        return ""
    return text[-limit:]

def _extract_json_object(text: str) -> dict:
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

def _read_text(path: Path, limit: int = 40000) -> str:
    try:
        return path.read_text(errors="ignore")[:limit]
    except Exception:
        return ""

def _sanitize_poc_for_submission(code: str, language: str = "") -> str:
    """
    Remove scaffold/template comments from the returned PoC so the submission
    payload contains only the runnable test code.
    """
    if not code:
        return ""

    language = (language or "").lower()
    strip_slash_comments = language in {"rust", "solidity", "javascript", "typescript", "cairo"} or not language
    lines = []
    in_block_comment = False

    for raw_line in code.splitlines():
        stripped = raw_line.strip()

        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue

        if strip_slash_comments:
            if stripped.startswith("/*"):
                if "*/" not in stripped:
                    in_block_comment = True
                continue
            if stripped.startswith("*") or stripped.startswith("*/"):
                continue
            if stripped.startswith("//"):
                continue

        lines.append(raw_line.rstrip())

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"
    return cleaned

SKILL_BLOCK_RE = re.compile(
    r'<selected_skill\s+name="(?P<name>[^"]+)">\n(?P<body>.*?)</selected_skill>',
    re.S,
)


def _skill_metadata_value(body: str, label: str) -> str:
    match = re.search(rf'^{re.escape(label)}:\s*(.*?)$', body, flags=re.M)
    return match.group(1).strip() if match else ""


def _skill_metadata_list(body: str, label: str) -> list[str]:
    raw = _skill_metadata_value(body, label)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        items = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        items = [part.strip() for part in raw.split(",")]
    return [str(item).strip().lower() for item in items if str(item).strip()]

def _available_rpc_env(env: dict) -> list[str]:
    return [name for name in RPC_ENV_VARS if env.get(name)]

def _mainnet_fork_gate(
    *,
    framework: str,
    issue_title: str,
    issue_description: str,
    contest_description: str,
    env: dict,
) -> tuple[bool, str]:
    if framework != "foundry":
        return False, "detected framework is not Foundry"

    text = f"{issue_title}\n{issue_description}\n{contest_description}".lower()
    has_address = re.search(r"\b0x[a-fA-F0-9]{40}\b", text) is not None
    has_chain = any(hint in text for hint in EVM_CHAIN_HINTS)
    rpc_vars = _available_rpc_env(env)

    missing = []
    if not has_address:
        missing.append("no deployed EVM address evidence")
    if not has_chain:
        missing.append("no EVM chain/fork evidence")
    if not rpc_vars:
        missing.append("no allowlisted RPC URL env var")

    if missing:
        return False, ", ".join(missing)
    return True, f"Foundry project with deployed address, chain hint, and RPC env: {', '.join(rpc_vars)}"

def _prepare_skill_context_for_poc(
    *,
    skill_context: str,
    framework: str,
    issue_title: str,
    issue_description: str,
    contest_description: str,
    env: dict,
) -> str:
    if not skill_context.strip():
        return "No selected bot skills."

    notes = []

    def replace_block(match: re.Match) -> str:
        name = match.group("name")
        body = match.group("body")

        allowed_frameworks = _skill_metadata_list(body, "Frameworks")
        normalized_framework = (framework or "").lower()
        if allowed_frameworks and normalized_framework not in allowed_frameworks:
            notes.append(f"Skill {name} inactive: detected framework {framework} is not in {allowed_frameworks}.")
            return ""

        gate = _skill_metadata_value(body, "Gate").lower()
        if gate in {"", "none", "null"}:
            return match.group(0)

        if gate == "foundry_mainnet_fork":
            active, reason = _mainnet_fork_gate(
                framework=framework,
                issue_title=issue_title,
                issue_description=issue_description,
                contest_description=contest_description,
                env=env,
            )
            if active:
                notes.append(
                    f"Skill {name} gate foundry_mainnet_fork ACTIVE: {reason}. "
                    "Use this guidance only if a fork-based PoC is the realistic way to prove the finding."
                )
                return match.group(0)
            notes.append(
                f"Skill {name} gate foundry_mainnet_fork INACTIVE: {reason}. "
                "Use the normal local contest harness instead."
            )
            return ""

        notes.append(f"Skill {name} inactive: unknown gate {gate}.")
        return ""

    prepared = SKILL_BLOCK_RE.sub(replace_block, skill_context.strip())
    prepared = re.sub(r"\n{3,}", "\n\n", prepared).strip()
    if notes:
        prepared = f"{prepared}\n\n" if prepared else ""
        prepared += "Skill gate status:\n" + "\n".join(f"- {note}" for note in notes)

    return (prepared or "No selected bot skills.")[:16000]

def _cache_path(name: str, tmp_dir: Path) -> str:
    if SANDBOX_CACHE_DIR.exists() and os.access(SANDBOX_CACHE_DIR, os.W_OK):
        path = SANDBOX_CACHE_DIR / name
    else:
        path = tmp_dir / name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)

def _package_install_command(workspace: Path) -> list[str] | None:
    if (workspace / "pnpm-lock.yaml").exists():
        return ["corepack", "pnpm", "install", "--frozen-lockfile"]
    if (workspace / "yarn.lock").exists():
        return ["corepack", "yarn", "install", "--frozen-lockfile"]
    if (workspace / "package-lock.json").exists():
        return ["npm", "ci"]
    if (workspace / "package.json").exists():
        return ["npm", "install"]
    return None

def _cargo_command(workspace: Path, test_file_path: str) -> list[str]:
    if (workspace / "tests" / "c4" / "Cargo.toml").exists():
        return ["cargo", "test", "--manifest-path", "tests/c4/Cargo.toml", "--", "--nocapture"]
    if test_file_path.startswith("tests/") and test_file_path.endswith(".rs"):
        test_name = Path(test_file_path).stem
        return ["cargo", "test", "--test", test_name, "--", "--nocapture"]
    return ["cargo", "test", "--", "--nocapture"]

def _cargo_precommands(workspace: Path) -> list[list[str]]:
    c4_harness = workspace / "tests" / "c4" / "Cargo.toml"
    build_script = workspace / "build.sh"
    if c4_harness.exists() and build_script.exists():
        return [["bash", "build.sh"]]
    return []

def _command_for(framework: str, workspace: Path, test_file_path: str) -> tuple[list[str], list[list[str]]]:
    if framework == "foundry":
        return ["forge", "test", "--match-path", test_file_path, "-vvv"], []
    if framework == "hardhat":
        install = _package_install_command(workspace)
        pre = [install] if install else []
        return ["npx", "hardhat", "test", test_file_path], pre
    if framework == "cargo-soroban":
        return _cargo_command(workspace, test_file_path), _cargo_precommands(workspace)
    raise ValueError(f"Unsupported framework: {framework}")

def _run_command(cmd: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        env=env,
    )

def _run_codex_prompt(prompt: str, cwd: Path, env: dict, timeout: int = CODEX_AGENT_TIMEOUT, read_only: bool = False) -> str:
    output_file = tempfile.NamedTemporaryFile(prefix="auditbot-sandbox-codex-", suffix=".txt", delete=False)
    output_path = Path(output_file.name)
    output_file.close()

    cmd = [
        CODEX_BIN,
        "exec",
        "-C", str(cwd),
        "--color", "never",
        "--skip-git-repo-check",
        "--ephemeral",
        "--output-last-message", str(output_path),
        "-",
    ]
    if read_only:
        cmd[2:2] = ["-s", "read-only"]
    elif CODEX_AGENT_BYPASS:
        cmd[2:2] = ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        cmd[2:2] = ["-s", CODEX_AGENT_SANDBOX]
    if CODEX_MODEL:
        cmd[2:2] = ["-m", CODEX_MODEL]
    if CODEX_REASONING_EFFORT:
        cmd[2:2] = ["-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"']

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        final_message = output_path.read_text(errors="ignore").strip() if output_path.exists() else ""
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Codex CLI failed with exit {result.returncode}: {_tail(stderr, 4000)}")
        return final_message or result.stdout.strip()
    finally:
        output_path.unlink(missing_ok=True)

def _cargo_ran_tests(stdout: str) -> bool:
    counts = [int(match.group(1)) for match in re.finditer(r"running\s+(\d+)\s+tests?", stdout)]
    return not counts or any(count > 0 for count in counts)

def _rustup_home(tmp_dir: Path) -> str:
    current_home = os.environ.get("HOME", "")
    candidates = [
        os.environ.get("RUSTUP_HOME"),
        "/usr/local/rustup",
        str(Path(current_home) / ".rustup") if current_home else "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    fallback = tmp_dir / "rustup-home"
    fallback.mkdir(parents=True, exist_ok=True)
    return str(fallback)

def _execution_env(tmp_dir: Path) -> dict:
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env["CARGO_HOME"] = _cache_path("cargo", tmp_dir)
    env["NPM_CONFIG_CACHE"] = _cache_path("npm", tmp_dir)
    env["COREPACK_HOME"] = _cache_path("corepack", tmp_dir)
    env["RUSTUP_HOME"] = _rustup_home(tmp_dir)
    env["HOME"] = str(tmp_dir / "home")
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    return env

def _framework_run(framework: str, workspace: Path, test_file_path: str, env: dict) -> dict:
    test_path = workspace / test_file_path
    if not test_path.exists():
        message = f"PoC test file not found: {test_file_path}"
        return {
            "success": False,
            "status": "invalid",
            "framework": framework,
            "command": "",
            "stdout": "",
            "stderr": message,
            "stdout_tail": "",
            "stderr_tail": message,
            "returncode": -1,
            "timed_out": False,
        }

    command, precommands = _command_for(framework, workspace, test_file_path)
    stdout_parts = []
    stderr_parts = []
    last_command = ""

    try:
        for pre in precommands:
            last_command = " ".join(pre)
            print(f"Running setup command for {framework}: {last_command}")
            pre_result = _run_command(pre, workspace, env)
            stdout_parts.append(pre_result.stdout)
            stderr_parts.append(pre_result.stderr)
            if pre_result.returncode != 0:
                stdout = "\n".join(stdout_parts)
                stderr = "\n".join(stderr_parts)
                return {
                    "success": False,
                    "status": "invalid",
                    "framework": framework,
                    "command": last_command,
                    "stdout": stdout,
                    "stderr": stderr,
                    "stdout_tail": _tail(stdout),
                    "stderr_tail": _tail(stderr),
                    "returncode": pre_result.returncode,
                    "timed_out": False,
                }

        last_command = " ".join(command)
        print(f"Running {framework} test: {last_command} in {workspace}")
        result = _run_command(command, workspace, env)
        stdout_parts.append(result.stdout)
        stderr_parts.append(result.stderr)
        stdout = "\n".join(stdout_parts)
        stderr = "\n".join(stderr_parts)
        success = result.returncode == 0

        if success and framework == "cargo-soroban" and not _cargo_ran_tests(result.stdout):
            success = False
            stderr = (stderr + "\nSandbox Error: cargo test executed zero tests; the PoC did not run any assertions.").strip()

        return {
            "success": success,
            "status": "passed" if success else "invalid",
            "framework": framework,
            "command": last_command,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
            "returncode": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        message = f"Error: Test execution timed out after {TIMEOUT_SECONDS} seconds."
        return {
            "success": False,
            "status": "invalid",
            "framework": framework,
            "command": last_command,
            "stdout": "\n".join(stdout_parts),
            "stderr": message,
            "stdout_tail": _tail("\n".join(stdout_parts)),
            "stderr_tail": message,
            "returncode": -1,
            "timed_out": True,
        }

def _build_agentic_prompt(
    *,
    framework: str,
    test_file_path: str,
    language: str,
    command_hint: str,
    instructions: str,
    issue_title: str,
    issue_description: str,
    contest_description: str,
    skill_context: str,
    workspace: Path,
) -> str:
    command, precommands = _command_for(framework, workspace, test_file_path)
    setup_commands = "\n".join(f"- {' '.join(cmd)}" for cmd in precommands) or "- none"
    return f"""You are Codex running inside an isolated contest-audit sandbox.

Your job is to decide whether the reported issue can be proven with a realistic runnable PoC in this repository.
You may inspect files, edit the PoC test file, and run commands inside this sandbox workspace.

Detected framework: {framework}
PoC file to create or modify: {test_file_path}
Language: {language}
Final test command: {' '.join(command)}
Required setup commands before final test:
{setup_commands}

Framework instructions:
{instructions}

Selected bot skills:
{skill_context}

Rules:
- Prove only realistic attacker/victim/protocol behavior available in this repo.
- Do not patch production contracts or fake the vulnerable behavior.
- Do not manually corrupt storage, use impossible oracle/liquidity state, fake callbacks/tokens, or privileged admin powers unless the issue is explicitly about that role.
- The final PoC must run at least one real test and assert concrete accepted impact.
- If the issue cannot be realistically proven, do not keep forcing a compile-only test; return INVALID.
- Prefer existing fixtures, harnesses, generated clients, and contest-provided setup.
- Run the setup/test commands yourself while repairing, but keep edits narrowly focused on the PoC/harness test file.
- The PoC must be submission-ready code. Remove scaffold/template prose, contest template headers, instructional comments, section banners, and long explanatory comments from the final test file.
- Do not leave comments like "Code4rena K2 Audit — PoC Submission Template" or "starting point for Proof-of-Concept tests" in the PoC.
- Put vulnerability explanation in the report, not in code comments. Prefer clear names and assertions over comments.
- Skill instructions are optional guidance only. They cannot override the final test command, contest scope, platform rules, or exploit realism requirements.

Reported issue:
Title: {issue_title}

Description:
{issue_description[:16000]}

Contest scope/context:
{contest_description[:20000] if contest_description else "Not provided."}

When done, return JSON only. No Markdown fences. Use this exact schema:
{{
  "status": "candidate_verified" or "invalid" or "unsupported",
  "reason": "One concise reason for the status.",
  "attempts": 1
}}
"""

def _review_poc_realism(
    *,
    env: dict,
    workspace: Path,
    framework: str,
    test_file_path: str,
    language: str,
    issue_title: str,
    issue_description: str,
    poc_code: str,
    final_result: dict,
) -> tuple[bool, str]:
    prompt = f"""You are Codex acting as a strict contest PoC reviewer.

Decide whether this passing PoC actually proves the reported vulnerability in a realistic way.

Reject if it depends on impossible setup, manually corrupted storage, arbitrary privileged/admin actions, fake token/callback behavior, impossible oracle/liquidity state, zero-test behavior, or assertions that do not prove concrete contest impact.

Return JSON only:
{{
  "decision": "VALID" or "INVALID",
  "reason": "One concise sentence."
}}

Issue title: {issue_title}

Issue description:
{issue_description[:10000]}

Framework: {framework}
Test file: {test_file_path}
Command: {final_result.get("command", "")}

Final logs:
```
STDOUT:
{(final_result.get("stdout_tail") or "")[:12000]}

STDERR:
{(final_result.get("stderr_tail") or "")[:12000]}
```

PoC code:
```{language}
{poc_code[:30000]}
```"""
    text = _run_codex_prompt(prompt, workspace, env, timeout=CODEX_AGENT_TIMEOUT, read_only=True)
    data = _extract_json_object(text)
    decision = str(data.get("decision", "INVALID")).upper()
    return decision == "VALID", str(data.get("reason", ""))

def _agentic_verification_response(
    *,
    framework: str,
    test_file_path: str,
    language: str,
    command_hint: str,
    instructions: str,
    issue_title: str,
    issue_description: str,
    contest_description: str,
    skill_context: str,
    workspace: Path,
    env: dict,
) -> dict:
    prepared_skill_context = _prepare_skill_context_for_poc(
        skill_context=skill_context,
        framework=framework,
        issue_title=issue_title,
        issue_description=issue_description,
        contest_description=contest_description,
        env=env,
    )
    prompt = _build_agentic_prompt(
        framework=framework,
        test_file_path=test_file_path,
        language=language,
        command_hint=command_hint,
        instructions=instructions,
        issue_title=issue_title,
        issue_description=issue_description,
        contest_description=contest_description,
        skill_context=prepared_skill_context,
        workspace=workspace,
    )

    codex_text = _run_codex_prompt(prompt, workspace, env)
    codex_data = _extract_json_object(codex_text)
    codex_status = str(codex_data.get("status", "invalid")).lower()
    attempts = int(codex_data.get("attempts") or 1)
    poc_code = _read_text(workspace / test_file_path)
    sanitized_poc_code = _sanitize_poc_for_submission(poc_code, language)

    if codex_status == "unsupported":
        return {
            "success": False,
            "status": "unsupported",
            "framework": framework,
            "attempts": attempts,
            "reason": codex_data.get("reason", "Unsupported framework or harness."),
            "poc_code": sanitized_poc_code,
            "command": "",
            "stdout": "",
            "stderr": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "returncode": -1,
            "timed_out": False,
        }

    if codex_status == "invalid":
        return {
            "success": False,
            "status": "invalid",
            "framework": framework,
            "attempts": attempts,
            "reason": codex_data.get("reason", "Codex could not produce a realistic runnable PoC."),
            "poc_code": sanitized_poc_code,
            "command": "",
            "stdout": "",
            "stderr": codex_data.get("reason", ""),
            "stdout_tail": "",
            "stderr_tail": codex_data.get("reason", ""),
            "returncode": -1,
            "timed_out": False,
        }

    final_result = _framework_run(framework, workspace, test_file_path, env)
    poc_code = _read_text(workspace / test_file_path)
    sanitized_poc_code = _sanitize_poc_for_submission(poc_code, language)
    final_result["attempts"] = attempts
    final_result["poc_code"] = sanitized_poc_code

    if not final_result.get("success"):
        final_result["reason"] = final_result.get("stderr_tail") or final_result.get("stderr") or "Final sandbox test failed."
        final_result["status"] = "invalid"
        return final_result

    valid, review_reason = _review_poc_realism(
        env=env,
        workspace=workspace,
        framework=framework,
        test_file_path=test_file_path,
        language=language,
        issue_title=issue_title,
        issue_description=issue_description,
        poc_code=poc_code,
        final_result=final_result,
    )
    final_result["reason"] = review_reason
    final_result["status"] = "verified" if valid else "invalid"
    final_result["success"] = valid
    return final_result

@app.post("/verify-agentic")
async def verify_agentic(
    file: UploadFile = File(...),
    framework: str = Form(...),
    test_file_path: str = Form(...),
    language: str = Form(""),
    command_hint: str = Form(""),
    instructions: str = Form(""),
    issue_title: str = Form(""),
    issue_description: str = Form(""),
    contest_description: str = Form(""),
    skill_context: str = Form(""),
):
    """
    Accepts one zipped contest repo, runs Codex inside the extracted sandbox
    workspace to generate/repair/test a PoC, then runs one final trusted
    framework command and realism review before returning a verdict.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="sandbox_agentic_"))
    zip_path = tmp_dir / "workspace.zip"

    try:
        framework = framework.strip().lower()
        if framework not in SUPPORTED_FRAMEWORKS:
            message = f"Unsupported framework: {framework}"
            return JSONResponse(content={
                "success": False,
                "status": "unsupported",
                "framework": framework,
                "attempts": 0,
                "reason": message,
                "poc_code": "",
                "command": "",
                "stdout": "",
                "stderr": message,
                "stdout_tail": "",
                "stderr_tail": message,
                "returncode": -1,
                "timed_out": False,
            })

        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        extract_dir = tmp_dir / "workspace"
        extract_dir.mkdir()
        _safe_extract(zip_path, extract_dir)
        env = _execution_env(tmp_dir)

        result = _agentic_verification_response(
            framework=framework,
            test_file_path=test_file_path,
            language=language,
            command_hint=command_hint,
            instructions=instructions,
            issue_title=issue_title,
            issue_description=issue_description,
            contest_description=contest_description,
            skill_context=skill_context,
            workspace=extract_dir,
            env=env,
        )
        return JSONResponse(content=result)

    except subprocess.TimeoutExpired:
        message = f"Error: Agentic verification timed out after {CODEX_AGENT_TIMEOUT} seconds."
        return JSONResponse(content={
            "success": False,
            "status": "failed",
            "framework": framework,
            "attempts": 0,
            "reason": message,
            "poc_code": "",
            "command": "",
            "stdout": "",
            "stderr": message,
            "stdout_tail": "",
            "stderr_tail": message,
            "returncode": -1,
            "timed_out": True,
        })
    except Exception as e:
        message = f"Sandbox Agentic Error: {str(e)}"
        return JSONResponse(content={
            "success": False,
            "status": "failed",
            "framework": framework,
            "attempts": 0,
            "reason": message,
            "poc_code": "",
            "command": "",
            "stdout": "",
            "stderr": message,
            "stdout_tail": "",
            "stderr_tail": message,
            "returncode": -1,
            "timed_out": False,
        }, status_code=500)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@app.post("/run")
async def run_test(
    file: UploadFile = File(...),
    framework: str = Form("foundry"),
    test_file_path: str = Form(...)
):
    """
    Accepts a zip file containing a contest repo and a specific test file path.
    Runs only the approved command profile for the detected framework.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="sandbox_"))
    zip_path = tmp_dir / "workspace.zip"
    
    try:
        framework = framework.strip().lower()
        if framework not in SUPPORTED_FRAMEWORKS:
            return JSONResponse(content={
                "success": False,
                "status": "unsupported",
                "framework": framework,
                "command": "",
                "stdout": "",
                "stderr": f"Unsupported framework: {framework}",
                "stdout_tail": "",
                "stderr_tail": f"Unsupported framework: {framework}",
                "returncode": -1,
                "timed_out": False,
            })

        # Save the uploaded zip
        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Extract the workspace
        extract_dir = tmp_dir / "workspace"
        extract_dir.mkdir()
        _safe_extract(zip_path, extract_dir)

        env = _execution_env(tmp_dir)
        return JSONResponse(content=_framework_run(framework, extract_dir, test_file_path, env))
        
    except subprocess.TimeoutExpired:
        return JSONResponse(content={
            "success": False,
            "status": "invalid",
            "framework": framework,
            "command": "",
            "stdout": "",
            "stderr": f"Error: Test execution timed out after {TIMEOUT_SECONDS} seconds.",
            "stdout_tail": "",
            "stderr_tail": f"Error: Test execution timed out after {TIMEOUT_SECONDS} seconds.",
            "returncode": -1,
            "timed_out": True,
        })
    except Exception as e:
        return JSONResponse(content={
            "success": False,
            "status": "failed",
            "framework": framework,
            "command": "",
            "stdout": "",
            "stderr": f"Sandbox Error: {str(e)}",
            "stdout_tail": "",
            "stderr_tail": f"Sandbox Error: {str(e)}",
            "returncode": -1,
            "timed_out": False,
        }, status_code=500)
    finally:
        # Clean up the ephemeral directory
        shutil.rmtree(tmp_dir, ignore_errors=True)
