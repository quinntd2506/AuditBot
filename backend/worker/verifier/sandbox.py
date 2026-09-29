import os
import shutil
import tempfile
import requests
from pathlib import Path
from typing import Dict, Any

SANDBOX_URL = os.environ.get("SANDBOX_URL", "http://sandbox:8080/run")
SANDBOX_VERIFY_URL = os.environ.get("SANDBOX_VERIFY_URL", SANDBOX_URL.rsplit("/", 1)[0] + "/verify-agentic")
SANDBOX_CLIENT_TIMEOUT = int(os.environ.get("SANDBOX_CLIENT_TIMEOUT", "5400"))

def _zip_workspace(workspace_dir: Path) -> Path:
    tmp_zip = Path(tempfile.mktemp(suffix=".zip"))
    shutil.make_archive(str(tmp_zip.with_suffix("")), "zip", workspace_dir)
    return tmp_zip

def execute_poc_test(workspace_dir: Path, framework: str, test_file_path: str) -> Dict[str, Any]:
    """
    Zips the workspace and sends it to the isolated Sandbox service to run an
    approved test command for the detected framework.
    """
    tmp_zip = _zip_workspace(workspace_dir)
    
    try:
        print(f"[Sandbox Client] Sending workspace ({tmp_zip.stat().st_size // 1024} KB) to sandbox for testing...")
        
        with open(tmp_zip, "rb") as f:
            response = requests.post(
                SANDBOX_URL,
                files={"file": ("workspace.zip", f, "application/zip")},
                data={"framework": framework, "test_file_path": test_file_path},
                timeout=SANDBOX_CLIENT_TIMEOUT,
            )
            
        response.raise_for_status()
        return response.json()
        
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Error communicating with Sandbox API: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Internal Sandbox Client Error: {str(e)}"
        }
    finally:
        if tmp_zip.exists():
            tmp_zip.unlink()

def execute_agentic_poc_verification(
    workspace_dir: Path,
    framework: str,
    test_file_path: str,
    language: str,
    command_hint: str,
    instructions: str,
    issue: dict,
    contest_description: str = "",
    skill_context: str = "",
) -> Dict[str, Any]:
    """
    Sends the workspace once to the sandbox, where Codex performs generation,
    repair, execution, and final realism review inside the extracted repo.
    """
    tmp_zip = _zip_workspace(workspace_dir)

    try:
        print(
            "[Sandbox Client] Sending workspace "
            f"({tmp_zip.stat().st_size // 1024} KB) to sandbox for agentic verification..."
        )
        with open(tmp_zip, "rb") as f:
            response = requests.post(
                SANDBOX_VERIFY_URL,
                files={"file": ("workspace.zip", f, "application/zip")},
                data={
                    "framework": framework,
                    "test_file_path": test_file_path,
                    "language": language,
                    "command_hint": command_hint,
                    "instructions": instructions,
                    "issue_title": issue.get("title", ""),
                    "issue_description": issue.get("description", ""),
                    "contest_description": contest_description or "",
                    "skill_context": skill_context or "",
                },
                timeout=SANDBOX_CLIENT_TIMEOUT,
            )

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "status": "failed",
            "stdout": "",
            "stderr": f"Error communicating with Sandbox agentic API: {str(e)}",
            "reason": f"Error communicating with Sandbox agentic API: {str(e)}",
        }
    except Exception as e:
        return {
            "success": False,
            "status": "failed",
            "stdout": "",
            "stderr": f"Internal Sandbox Client Error: {str(e)}",
            "reason": f"Internal Sandbox Client Error: {str(e)}",
        }
    finally:
        if tmp_zip.exists():
            tmp_zip.unlink()

def execute_foundry_test(workspace_dir: Path, test_file_path: str) -> Dict[str, Any]:
    """Backward-compatible wrapper for older callers."""
    return execute_poc_test(workspace_dir, "foundry", test_file_path)
