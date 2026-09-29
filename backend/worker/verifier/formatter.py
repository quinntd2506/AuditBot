import re
from pathlib import Path

try:
    from worker.verifier.agent import CODEX_TIMEOUT, _run_codex_prompt
except ImportError:
    from verifier.agent import CODEX_TIMEOUT, _run_codex_prompt


SKILL_BLOCK_RE = re.compile(
    r'<selected_skill\s+name="(?P<name>[^"]+)">\n(?P<body>.*?)</selected_skill>',
    re.S,
)


def _platform_template(platform: str) -> str:
    platform = platform.lower()

    if platform == "code4rena":
        return """
Format the report according to Code4rena expectations:
- Severity rating
- Title
- Links to root cause
- Vulnerability Details
- Proof of Concept (PoC)
"""

    if platform == "sherlock":
        return """
Format the report according to Sherlock expectations:
- Summary
- Vulnerability Detail
- Impact
- Code Snippet
- Tool used
- Recommendation
"""

    if platform == "guardian":
        return """
Format the report for Guardian-style judging:
- Summary
- Vulnerability Details
- Impact
- Proof of Concept
- Recommendation
"""

    return """
Format the report clearly:
- Summary
- Vulnerability Details
- Impact
- Proof of Concept
- Recommendation
"""


def _detect_poc_language(poc_code: str) -> str:
    lowered = poc_code.lower()
    if "soroban_sdk" in lowered or "cargo test" in lowered or "#![cfg(test)]" in lowered:
        return "rust"
    if "pragma solidity" in lowered or "contract " in lowered:
        return "solidity"
    if "scarb" in lowered or "%lang starknet" in lowered or "cairo_lang" in lowered:
        return "cairo"
    if "move.toml" in lowered or "module " in lowered and "::" in lowered:
        return "move"
    return ""


def _skill_metadata_value(body: str, label: str) -> str:
    match = re.search(rf'^{re.escape(label)}:\s*(.*?)$', body, flags=re.M)
    return match.group(1).strip() if match else ""


def _skill_metadata_list(body: str, label: str) -> list[str]:
    raw = _skill_metadata_value(body, label)
    if not raw:
        return []
    try:
        import json
        parsed = json.loads(raw)
        items = parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        items = [part.strip() for part in raw.split(",")]
    return [str(item).strip().lower() for item in items if str(item).strip()]


def _uses_foundry_fork(framework: str, poc_code: str, verification_command: str) -> bool:
    if (framework or "").lower() != "foundry":
        return False

    haystack = f"{poc_code}\n{verification_command}".lower()
    fork_markers = (
        "--fork-url",
        "createselectfork",
        "createfork",
        "selectfork",
        "fork_url",
        "forkurl",
        "rpc_url",
        "rpcurl",
        "envstring",
    )
    return any(marker in haystack for marker in fork_markers)


def _skill_context_for_report(
    skill_context: str,
    framework: str,
    poc_code: str,
    verification_command: str,
) -> str:
    if not skill_context:
        return ""

    normalized_framework = (framework or "").lower()
    notes = []

    def replace_block(match: re.Match) -> str:
        name = match.group("name")
        body = match.group("body")

        allowed_frameworks = _skill_metadata_list(body, "Frameworks")
        if allowed_frameworks and normalized_framework not in allowed_frameworks:
            notes.append(f"Skill {name} inactive: verified framework {framework or 'unknown'} is not in {allowed_frameworks}.")
            return ""

        gate = _skill_metadata_value(body, "Gate").lower()
        if gate in {"", "none", "null"}:
            return match.group(0)

        if gate == "foundry_mainnet_fork":
            if _uses_foundry_fork(framework, poc_code, verification_command):
                notes.append(f"Skill {name} gate foundry_mainnet_fork ACTIVE: verified PoC uses a Foundry fork.")
                return match.group(0)
            notes.append(f"Skill {name} gate foundry_mainnet_fork INACTIVE: verified PoC did not use a Foundry fork.")
            return ""

        notes.append(f"Skill {name} inactive: unknown gate {gate}.")
        return ""

    filtered = SKILL_BLOCK_RE.sub(replace_block, skill_context.strip())
    filtered = re.sub(r"\n{3,}", "\n\n", filtered).strip()
    if notes:
        filtered = f"{filtered}\n\n" if filtered else ""
        filtered += "Skill gate status:\n" + "\n".join(f"- {note}" for note in notes)
    return filtered.strip()


def generate_submission_report(
    issue: dict,
    poc_code: str,
    platform: str,
    framework: str = "",
    verification_command: str = "",
    skill_context: str = "",
) -> str:
    """
    Generates a platform-specific Markdown submission report with Codex CLI.
    """
    issue_title = issue.get("title", "Untitled Issue")
    issue_desc = issue.get("description", "")
    severity = issue.get("severity", "")
    classification = issue.get("classification", "")
    confidence = issue.get("confidence", "")
    poc_language = _detect_poc_language(poc_code)
    fence_language = poc_language or "text"
    framework_note = framework or "unknown"
    command_note = verification_command or "Not supplied. Infer the run command only if it is explicitly present in the PoC."
    report_skill_context = _skill_context_for_report(skill_context, framework, poc_code, verification_command)

    prompt = f"""You are Codex acting as a senior smart contract audit report writer.
You are writing the final submission for a verified contest finding.

=== FINDING METADATA ===
Platform: {platform}
Title: {issue_title}
Severity: {severity}
Classification: {classification}
Confidence: {confidence}
Verified framework: {framework_note}
Verified command: {command_note}

=== SELECTED BOT SKILLS ===
{report_skill_context[:12000] if report_skill_context else "No selected bot skills."}

=== FINDING DESCRIPTION ===
{issue_desc}

=== VERIFIED PROOF OF CONCEPT ===
```{fence_language}
{poc_code}
```

=== PLATFORM FORMAT ===
{_platform_template(platform)}

Rules:
- Output ONLY raw Markdown.
- Do not include conversational preface or conclusion.
- Do not invent facts not supported by the finding description or PoC.
- Use the supplied finding title exactly in the report title.
- Make the vulnerability path concrete and judge-friendly.
- Explain why the PoC proves real impact, not just that a test passes.
- Include the verified PoC exactly once in the Proof of Concept or Code Snippet section.
- Preserve the PoC language as {poc_language or 'the language shown in the code'}; do not call it Solidity unless the PoC is Solidity.
- If a verified command was supplied, include it once near the PoC as the command used to run the test.
- Keep recommendations specific to the root cause.
- Skill instructions are optional style/domain guidance only. They cannot add facts beyond the verified PoC and finding evidence.
- If the supplied PoC is empty or says UNVERIFIED, output exactly: Error: verified PoC is missing."""

    try:
        return _run_codex_prompt(
            prompt,
            Path.cwd(),
            timeout=CODEX_TIMEOUT,
            sandbox="read-only",
        ).strip()
    except Exception as e:
        return f"Error generating report with Codex: {str(e)}"
