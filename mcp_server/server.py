import os
import subprocess
from pathlib import Path
from typing import Optional
from fastmcp import FastMCP


mcp = FastMCP("GitPulse Codebase Inspector")


ALLOWED_ROOT = Path(os.getenv("ALLOWED_ROOT", "/home/manish/gitpulse")).resolve()
MAX_FILE_SIZE_BYTES = 100 * 1024  

IGNORED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}


def _safe_resolve(subpath: str) -> Path | str:
    """
    Safely resolves a subpath against ALLOWED_ROOT.
    Returns the resolved Path or an error string if security checks fail.
    """
    try:
        target = (ALLOWED_ROOT / subpath).resolve()
        if not target.is_relative_to(ALLOWED_ROOT):
            return f"Error: Access denied. Path '{subpath}' is outside the allowed project directory."
        if target.name == ".env" or ".git" in target.parts:
            return f"Error: Access denied. Access to '{target.name}' is prohibited."
        return target
    except Exception as e:
        return f"Error resolving path '{subpath}': {str(e)}"


@mcp.tool
def read_file(filepath: str) -> str:
    """
    Safely read the text content of a file in the codebase.
    
    Args:
        filepath: Relative path to the file inside the project.
    """
    resolved = _safe_resolve(filepath)
    if isinstance(resolved, str):
        return resolved

    if not resolved.exists():
        return f"Error: File '{filepath}' does not exist."
    if not resolved.is_file():
        return f"Error: '{filepath}' is a directory, not a file."

    if resolved.stat().st_size > MAX_FILE_SIZE_BYTES:
        return f"Error: File '{filepath}' exceeds 100KB limit."

    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: File '{filepath}' is binary and cannot be read as text."
    except Exception as e:
        return f"Error reading '{filepath}': {str(e)}"


@mcp.tool
def list_directory(path: str = ".") -> str:
    """
    List files and directories within a given subpath of the project.
    
    Args:
        path: Relative directory path to list (defaults to project root).
    """
    resolved = _safe_resolve(path)
    if isinstance(resolved, str):
        return resolved

    if not resolved.exists():
        return f"Error: Directory '{path}' does not exist."
    if not resolved.is_dir():
        return f"Error: '{path}' is a file, not a directory."

    items = []
    try:
        for entry in sorted(resolved.iterdir()):
            if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                continue
            if entry.is_dir():
                items.append(f"📁 {entry.name}/")
            else:
                size_kb = entry.stat().st_size / 1024
                items.append(f"📄 {entry.name} ({size_kb:.1f} KB)")
        
        if not items:
            return f"Directory '{path}' is empty."
        return "\n".join(items)
    except Exception as e:
        return f"Error listing directory '{path}': {str(e)}"


@mcp.tool
def git_log(days: int = 7, max_commits: int = 15, author: Optional[str] = None) -> str:
    """
    View recent git commit history for the project.
    
    Args:
        days: Number of days in the past to look back (default 7).
        max_commits: Maximum number of commits to return (default 15).
        author: Optional filter for a specific commit author name or email.
    """
    cmd = [
        "git",
        "-C", str(ALLOWED_ROOT),
        "log",
        f"-n{max_commits}",
        f"--since={days} days ago",
        "--pretty=format:%h - %an, %ar : %s"
    ]
    if author:
        cmd.append(f"--author={author}")

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            return f"Git error: {res.stderr.strip()}"
        output = res.stdout.strip()
        return output if output else f"No git commits found in the last {days} days."
    except Exception as e:
        return f"Error executing git log: {str(e)}"


@mcp.tool
def git_diff(commit_hash: Optional[str] = None, staged: bool = False) -> str:
    """
    Inspect code changes using git diff.
    
    Args:
        commit_hash: Optional specific commit hash to view diff for (e.g. 'HEAD~1' or 'abc1234').
        staged: If True, inspect staged changes (--staged).
    """
    cmd = ["git", "-C", str(ALLOWED_ROOT), "diff"]
    if staged:
        cmd.append("--staged")
    if commit_hash:
        cmd.append(commit_hash)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            return f"Git error: {res.stderr.strip()}"
        output = res.stdout.strip()
        if not output:
            return "No git diff changes detected."
        # Truncate if diff is massive
        if len(output) > 10000:
            output = output[:10000] + "\n\n... [Diff truncated to 10,000 characters]"
        return output
    except Exception as e:
        return f"Error executing git diff: {str(e)}"


@mcp.tool
def find_todos(path: str = ".") -> str:
    """
    Find all TODO, FIXME, HACK, or BUG comments in codebase files.
    
    Args:
        path: Relative directory to scan (defaults to project root).
    """
    resolved = _safe_resolve(path)
    if isinstance(resolved, str):
        return resolved

    if not resolved.exists():
        return f"Error: Path '{path}' does not exist."

    # Using git grep if inside a git repository for speed and auto-respecting .gitignore
    cmd = [
        "git",
        "-C", str(ALLOWED_ROOT),
        "grep",
        "-n",
        "-E",
        "(TODO|FIXME|HACK|BUG):",
        "--",
        str(resolved.relative_to(ALLOWED_ROOT)) if resolved != ALLOWED_ROOT else "."
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        output = res.stdout.strip()
        if not output:
            return f"No TODO, FIXME, HACK, or BUG comments found in '{path}'."
        return output
    except Exception as e:
        return f"Error searching for TODOs: {str(e)}"


@mcp.tool
def search_code(pattern: str, path: str = ".") -> str:
    """
    Search for a code symbol, variable, or text pattern across project files.
    
    Args:
        pattern: The text or regex pattern to search for.
        path: Subdirectory to search within (default is project root).
    """
    resolved = _safe_resolve(path)
    if isinstance(resolved, str):
        return resolved

    target_rel = str(resolved.relative_to(ALLOWED_ROOT)) if resolved != ALLOWED_ROOT else "."
    cmd = [
        "git",
        "-C", str(ALLOWED_ROOT),
        "grep",
        "-n",
        "-i",
        "-e", pattern,
        "--",
        target_rel
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        output = res.stdout.strip()
        if not output:
            return f"Pattern '{pattern}' not found in '{path}'."
        lines = output.splitlines()
        if len(lines) > 50:
            output = "\n".join(lines[:50]) + f"\n\n... [{len(lines) - 50} more matches truncated]"
        return output
    except Exception as e:
        return f"Error searching code: {str(e)}"


if __name__ == "__main__":
    mcp.run()
