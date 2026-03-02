"""
ComfyDep Manager — Smart dependency resolver for ComfyUI custom nodes.
Scans all installed nodes, detects conflicts, and lets you pick exact versions
to install into ComfyUI's embedded Python environment.

Run:  streamlit run app.py
"""

import json
import re
import subprocess
import sys
import time
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import streamlit as st
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version, InvalidVersion


# ── Activity Log ──────────────────────────────────────────────────────────────
# A simple in-memory log that every function can write to.
# Rendered as a collapsible terminal at the bottom of the page.

def _get_log() -> list[str]:
    if "activity_log" not in st.session_state:
        st.session_state["activity_log"] = []
    return st.session_state["activity_log"]


def log(msg: str):
    """Append a timestamped message to the activity log."""
    ts = datetime.now().strftime("%H:%M:%S")
    _get_log().append(f"[{ts}] {msg}")


def log_clear():
    st.session_state["activity_log"] = []

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_NODES_DIR = r"D:\SwarmUI\dlbackend\comfy\ComfyUI\custom_nodes"
DEFAULT_PYTHON_EXE = r"D:\SwarmUI\dlbackend\comfy\python_embeded\python.exe"
CACHE_FILE = Path(__file__).parent / ".comfydep_cache.json"
GITHUB_RAW = "https://raw.githubusercontent.com"
GITHUB_API = "https://api.github.com/repos"

# Packages that need special handling (index URLs, build flags, etc.)
SPECIAL_PACKAGES = {
    "torch", "torchvision", "torchaudio", "xformers",
    "triton", "bitsandbytes", "flash-attn",
}

# ── Pip version check ─────────────────────────────────────────────────────────


def check_pip_version(python_exe: str) -> tuple[Optional[str], Optional[str]]:
    """
    Check the installed pip version and whether a newer one is available.
    Returns (current_version, latest_version). Either may be None on error.
    """
    current = None
    latest = None
    try:
        result = subprocess.run(
            [python_exe, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            m = re.search(r'pip\s+([\d.]+)', result.stdout)
            if m:
                current = m.group(1)
                log(f"pip current version: {current}")
    except Exception as e:
        log(f"ERROR checking pip version: {e}")

    try:
        result = subprocess.run(
            [python_exe, "-m", "pip", "index", "versions", "pip"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            # Output like: "pip (24.3.1)\nAvailable versions: 24.3.1, 24.2, ..."
            m = re.search(r'Available versions:\s*([\d.]+)', result.stdout)
            if m:
                latest = m.group(1)
                log(f"pip latest version:  {latest}")
        else:
            # Fallback: try `pip install pip==__nonexistent__` trick to get versions
            # or just query PyPI JSON API
            resp = requests.get("https://pypi.org/pypi/pip/json", timeout=10)
            if resp.status_code == 200:
                latest = resp.json()["info"]["version"]
                log(f"pip latest version (via PyPI): {latest}")
    except Exception as e:
        log(f"Could not check latest pip version: {e}")

    return current, latest


def upgrade_pip(python_exe: str) -> tuple[int, str]:
    """Upgrade pip in the embedded Python environment."""
    cmd = [python_exe, "-m", "pip", "install", "--upgrade", "pip"]
    log(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        if result.returncode == 0:
            log("pip upgraded successfully")
        else:
            log(f"pip upgrade failed (exit code {result.returncode})")
        return result.returncode, output
    except Exception as e:
        log(f"ERROR upgrading pip: {e}")
        return 1, str(e)


# ── Helpers ───────────────────────────────────────────────────────────────────


def safe_parse_requirement(line: str) -> Optional[Requirement]:
    """Parse a single requirement line, tolerating common junk."""
    line = line.strip()
    if not line or line.startswith(("#", "-", "git+", "http://", "https://")):
        return None
    # Strip inline comments
    line = line.split("#")[0].strip()
    # Strip environment markers after semicolons for cleaner display
    # but keep them in the Requirement object
    try:
        return Requirement(line)
    except InvalidRequirement:
        # Try stripping extras / markers
        clean = re.split(r"[;@]", line)[0].strip()
        try:
            return Requirement(clean)
        except InvalidRequirement:
            return None


def parse_requirements_txt(path: Path) -> list[Requirement]:
    """Parse a requirements.txt file."""
    reqs = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            r = safe_parse_requirement(line)
            if r:
                reqs.append(r)
    except OSError:
        pass
    return reqs


def parse_pyproject_toml(path: Path) -> list[Requirement]:
    """Extract dependencies from pyproject.toml (PEP 621)."""
    reqs = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Simple TOML parsing — look for dependencies = [...]
        m = re.search(r'dependencies\s*=\s*\[(.*?)\]', text, re.DOTALL)
        if m:
            for item in re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1)):
                line = item[0] or item[1]
                r = safe_parse_requirement(line)
                if r:
                    reqs.append(r)
    except OSError:
        pass
    return reqs


def parse_setup_py(path: Path) -> list[Requirement]:
    """Extract install_requires from setup.py via regex (no exec)."""
    reqs = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'install_requires\s*=\s*\[(.*?)\]', text, re.DOTALL)
        if m:
            for item in re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1)):
                line = item[0] or item[1]
                r = safe_parse_requirement(line)
                if r:
                    reqs.append(r)
    except OSError:
        pass
    return reqs


def parse_install_py(path: Path) -> list[Requirement]:
    """Extract pip install targets from install.py subprocess calls."""
    reqs = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Match pip install commands in subprocess calls
        for m in re.finditer(r'pip\s+install["\s]+([^")\]]+)', text):
            for token in m.group(1).split():
                if token.startswith("-"):
                    continue
                r = safe_parse_requirement(token)
                if r:
                    reqs.append(r)
    except OSError:
        pass
    return reqs


def detect_github_url(node_dir: Path) -> Optional[str]:
    """Try to find the GitHub repo URL from .git/config or README."""
    git_config = node_dir / ".git" / "config"
    if git_config.exists():
        try:
            cp = ConfigParser()
            cp.read(str(git_config), encoding="utf-8")
            for section in cp.sections():
                if "remote" in section:
                    url = cp.get(section, "url", fallback="")
                    if "github.com" in url:
                        # Normalize to owner/repo
                        m = re.search(r'github\.com[/:]([^/]+/[^/\s.]+)', url)
                        if m:
                            return m.group(1).removesuffix(".git")
        except Exception:
            pass
    # Fallback: scan README
    for readme in node_dir.glob("README*"):
        try:
            text = readme.read_text(encoding="utf-8", errors="ignore")[:4000]
            m = re.search(r'github\.com/([^/\s]+/[^/\s#?)]+)', text)
            if m:
                return m.group(1).removesuffix(".git")
        except OSError:
            pass
    return None


def fetch_github_requirements(owner_repo: str) -> list[Requirement]:
    """Fetch requirements.txt from GitHub raw (default branch)."""
    reqs = []
    try:
        # Try common default branches
        for branch in ("main", "master"):
            url = f"{GITHUB_RAW}/{owner_repo}/{branch}/requirements.txt"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    r = safe_parse_requirement(line)
                    if r:
                        reqs.append(r)
                break
    except requests.RequestException:
        pass
    return reqs


# ── Scanning ──────────────────────────────────────────────────────────────────


def scan_node(node_dir: Path, use_github: bool = True) -> dict:
    """Scan a single custom node and return its metadata."""
    name = node_dir.name
    reqs: list[Requirement] = []
    sources: list[str] = []
    log(f"Scanning node: {name}")

    # Parse local dependency files
    req_txt = node_dir / "requirements.txt"
    if req_txt.exists():
        reqs.extend(parse_requirements_txt(req_txt))
        sources.append("requirements.txt")
        log(f"  {name}: parsed requirements.txt ({len(reqs)} deps)")

    pyproject = node_dir / "pyproject.toml"
    if pyproject.exists():
        found = parse_pyproject_toml(pyproject)
        if found:
            reqs.extend(found)
            sources.append("pyproject.toml")
            log(f"  {name}: parsed pyproject.toml (+{len(found)} deps)")

    setup = node_dir / "setup.py"
    if setup.exists():
        found = parse_setup_py(setup)
        if found:
            reqs.extend(found)
            sources.append("setup.py")
            log(f"  {name}: parsed setup.py (+{len(found)} deps)")

    install = node_dir / "install.py"
    if install.exists():
        found = parse_install_py(install)
        if found:
            reqs.extend(found)
            sources.append("install.py")
            log(f"  {name}: parsed install.py (+{len(found)} deps)")

    # GitHub fallback
    github_url = detect_github_url(node_dir)
    if use_github and github_url and not reqs:
        log(f"  {name}: no local deps, trying GitHub ({github_url})...")
        gh_reqs = fetch_github_requirements(github_url)
        if gh_reqs:
            reqs.extend(gh_reqs)
            sources.append("GitHub")
            log(f"  {name}: fetched {len(gh_reqs)} deps from GitHub")

    # Deduplicate by package name (keep first)
    seen = set()
    unique_reqs = []
    for r in reqs:
        key = r.name.lower().replace("-", "_").replace(".", "_")
        if key not in seen:
            seen.add(key)
            unique_reqs.append(r)

    return {
        "name": name,
        "path": str(node_dir),
        "github": github_url,
        "sources": sources,
        "requirements": [str(r) for r in unique_reqs],
    }


def scan_all_nodes(nodes_dir: str, use_github: bool = True) -> list[dict]:
    """Scan all valid custom nodes in the directory."""
    nodes_path = Path(nodes_dir)
    if not nodes_path.is_dir():
        log(f"ERROR: Directory not found: {nodes_dir}")
        return []
    log(f"Starting scan of {nodes_dir}")
    results = []
    for child in sorted(nodes_path.iterdir()):
        if child.is_dir() and (child / "__init__.py").exists():
            results.append(scan_node(child, use_github=use_github))
    log(f"Scan complete: {len(results)} nodes found")
    return results


# ── Installed packages ────────────────────────────────────────────────────────


def get_installed_packages(python_exe: str) -> dict[str, str]:
    """Run pip list and return {normalized_name: version}."""
    log(f"Running: {python_exe} -m pip list --format=json")
    try:
        result = subprocess.run(
            [python_exe, "-m", "pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            pkgs = json.loads(result.stdout)
            log(f"Loaded {len(pkgs)} installed packages")
            return {
                p["name"].lower().replace("-", "_").replace(".", "_"): p["version"]
                for p in pkgs
            }
        else:
            log(f"pip list failed (exit code {result.returncode})")
    except Exception as e:
        log(f"ERROR loading installed packages: {e}")
    return {}


# ── Dependency aggregation ────────────────────────────────────────────────────


def normalize_name(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def aggregate_dependencies(nodes: list[dict]) -> dict:
    """
    Build a dict: {normalized_pkg_name: {
        "display_name": str,
        "requesters": [(node_name, specifier_str), ...],
    }}
    """
    deps: dict[str, dict] = {}
    for node in nodes:
        for req_str in node["requirements"]:
            r = safe_parse_requirement(req_str)
            if not r:
                continue
            key = normalize_name(r.name)
            if key not in deps:
                deps[key] = {"display_name": r.name, "requesters": []}
            spec_str = str(r.specifier) if r.specifier else "(any)"
            deps[key]["requesters"].append((node["name"], spec_str))
    return deps


def check_conflict(requesters: list[tuple[str, str]], installed_ver: Optional[str]) -> tuple[str, str]:
    """
    Determine conflict status.
    Returns (status, detail) where status is 'ok', 'conflict', 'missing', or 'warn'.
    """
    specs = []
    for _, spec_str in requesters:
        if spec_str != "(any)":
            try:
                specs.append(SpecifierSet(spec_str))
            except Exception:
                pass

    if not installed_ver:
        return ("missing", "Not installed")

    try:
        ver = Version(installed_ver)
    except InvalidVersion:
        return ("warn", f"Unparseable version: {installed_ver}")

    # Check if installed version satisfies all specifiers
    failures = []
    for i, ss in enumerate(specs):
        if ver not in ss:
            node_name = [r for r in requesters if r[1] != "(any)"][i][0] if i < len(requesters) else "?"
            failures.append(f"{node_name} wants {ss}")

    if failures:
        return ("conflict", "; ".join(failures))

    # Check if specifiers conflict with each other (even if current version is ok)
    if len(specs) >= 2:
        combined = SpecifierSet()
        try:
            for ss in specs:
                combined &= ss
        except Exception:
            return ("warn", "Could not combine specifiers")

    return ("ok", "All satisfied")


def find_best_version(requesters: list[tuple[str, str]], installed_ver: Optional[str]) -> Optional[str]:
    """Suggest the best version that satisfies the most specifiers."""
    specs = []
    for _, spec_str in requesters:
        if spec_str != "(any)":
            try:
                specs.append(SpecifierSet(spec_str))
            except Exception:
                pass
    if not specs:
        return installed_ver

    # Try installed version first
    if installed_ver:
        try:
            ver = Version(installed_ver)
            if all(ver in ss for ss in specs):
                return installed_ver
        except InvalidVersion:
            pass

    # Collect pinned versions as candidates
    candidates = set()
    for ss in specs:
        for spec in ss:
            if spec.operator in ("==", "~="):
                candidates.add(spec.version)

    # Check each candidate against all specs
    for c in sorted(candidates, reverse=True):
        try:
            v = Version(c)
            if all(v in ss for ss in specs):
                return c
        except InvalidVersion:
            pass

    return None


# ── Cache ─────────────────────────────────────────────────────────────────────


def save_cache(nodes: list[dict]):
    try:
        CACHE_FILE.write_text(json.dumps(nodes, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_cache() -> Optional[list[dict]]:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


# ── pip freeze backup ─────────────────────────────────────────────────────────


def backup_pip_freeze(python_exe: str) -> Optional[str]:
    """Save current pip freeze to a timestamped file. Returns path."""
    log("Creating pip freeze backup...")
    try:
        result = subprocess.run(
            [python_exe, "-m", "pip", "freeze"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            backup_dir = Path(__file__).parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = backup_dir / f"pip_freeze_{ts}.txt"
            path.write_text(result.stdout, encoding="utf-8")
            log(f"Backup saved: {path}")
            return str(path)
        else:
            log(f"pip freeze failed (exit code {result.returncode})")
    except Exception as e:
        log(f"ERROR creating backup: {e}")
    return None


# ── Installation ──────────────────────────────────────────────────────────────


def run_pip_install(python_exe: str, packages: list[str], dry_run: bool = False) -> tuple[int, str]:
    """
    Install packages via pip. Returns (return_code, output).
    If dry_run, just return the command that would be run.
    """
    cmd = [python_exe, "-m", "pip", "install", "--no-cache-dir"] + packages
    log(f"{'[DRY RUN] ' if dry_run else ''}Running: {' '.join(cmd)}")
    if dry_run:
        return 0, f"Would run:\n{' '.join(cmd)}"
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        if result.returncode == 0:
            log(f"pip install succeeded ({len(packages)} packages)")
        else:
            log(f"pip install FAILED (exit code {result.returncode})")
        # Log each line of output
        for line in output.strip().splitlines():
            log(f"  pip: {line}")
        return result.returncode, output
    except subprocess.TimeoutExpired:
        log("ERROR: pip install timed out after 10 minutes")
        return 1, "ERROR: pip install timed out after 10 minutes"
    except Exception as e:
        log(f"ERROR: {e}")
        return 1, f"ERROR: {e}"


# Known build-error patterns and the advice to show the user.
# The 4th element is an optional "fix_key" that tells the UI to show a fix button.
BUILD_ERROR_HINTS: list[tuple[str, str, str, Optional[str]]] = [
    (
        r"Microsoft Visual C\+\+ 14\.0 or greater is required",
        "Microsoft Visual C++ Build Tools Required",
        "This package contains C/C++ extensions that must be compiled.\n\n"
        "**Fix:** Install the Microsoft C++ Build Tools, then restart your machine.\n\n"
        "Alternatively, look for a pre-built wheel for this package "
        "(some packages offer `*-cp311-win_amd64.whl` downloads on PyPI or GitHub Releases).",
        "install_msvc",
    ),
    (
        r"Failed building wheel for",
        "Wheel Build Failed",
        "pip could not build a wheel for one or more packages. Common causes:\n"
        "- Missing C/C++ compiler (install MS Build Tools — see above)\n"
        "- Missing system library headers\n"
        "- Package doesn't support your Python version\n\n"
        "**Tip:** Try installing packages one at a time to isolate the failure, "
        "or search PyPI for a pre-built wheel.",
        None,
    ),
    (
        r"No matching distribution found",
        "Package Not Found",
        "pip could not find a version of this package that matches your Python version or platform.\n\n"
        "**Check:** Is the package name spelled correctly? Does it support Python 3.11 on Windows?",
        None,
    ),
    (
        r"(?i)permission(?:s)?\s+(?:denied|error)",
        "Permission Denied",
        "pip doesn't have permission to write to the target directory.\n\n"
        "**Fix:** Make sure no other process (ComfyUI, SwarmUI) is using the embedded Python, "
        "then try again.",
        None,
    ),
    (
        r"Ignoring invalid distribution",
        "Corrupted Package Detected",
        "There are broken/partial packages in your `site-packages` directory "
        "(folder names starting with `~`). These are left over from failed installs "
        "and can cause warnings or conflicts.",
        "fix_corrupted",
    ),
]


def diagnose_pip_output(output: str) -> list[tuple[str, str, Optional[str]]]:
    """Scan pip output for known error patterns. Returns [(title, advice, fix_key), ...]."""
    hints = []
    seen = set()
    for pattern, title, advice, fix_key in BUILD_ERROR_HINTS:
        if title not in seen and re.search(pattern, output):
            hints.append((title, advice, fix_key))
            seen.add(title)
    return hints


# ── Auto-fix: corrupted packages ─────────────────────────────────────────────


def find_corrupted_packages(python_exe: str) -> list[Path]:
    """Find all ~tilde folders in site-packages (broken partial installs)."""
    site_packages = Path(python_exe).parent / "Lib" / "site-packages"
    if not site_packages.is_dir():
        return []
    return sorted(p for p in site_packages.iterdir() if p.is_dir() and p.name.startswith("~"))


def remove_corrupted_packages(python_exe: str) -> tuple[list[str], list[str]]:
    """
    Delete all ~tilde folders from site-packages.
    Returns (deleted, failed) lists of folder names.
    """
    import shutil
    corrupted = find_corrupted_packages(python_exe)
    deleted = []
    failed = []
    for p in corrupted:
        try:
            shutil.rmtree(p)
            log(f"Deleted corrupted package: {p.name}")
            deleted.append(p.name)
        except Exception as e:
            log(f"ERROR deleting {p.name}: {e}")
            failed.append(f"{p.name} ({e})")
    return deleted, failed


# ── Auto-fix: MSVC Build Tools ───────────────────────────────────────────────

VSWHERE = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"


def check_msvc_installed() -> bool:
    """Check if Microsoft Visual C++ Build Tools are installed."""
    # Method 1: vswhere
    if Path(VSWHERE).exists():
        try:
            result = subprocess.run(
                [VSWHERE, "-products", "*", "-requires",
                 "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                 "-property", "installationPath"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except Exception:
            pass
    # Method 2: check common paths
    for path in [
        r"C:\Program Files\Microsoft Visual Studio\2022",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022",
        r"C:\Program Files\Microsoft Visual Studio\2019",
        r"C:\BuildTools",
    ]:
        if Path(path).is_dir():
            return True
    return False


def check_winget_available() -> bool:
    """Check if winget is available for automated installs."""
    try:
        result = subprocess.run(
            ["winget", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def install_msvc_build_tools() -> tuple[int, str]:
    """
    Install Microsoft Visual C++ Build Tools via winget.
    Returns (return_code, output).
    """
    log("Attempting to install Microsoft Visual C++ Build Tools via winget...")

    if not check_winget_available():
        msg = ("winget is not available on this system.\n\n"
               "Please install the Build Tools manually from:\n"
               "https://visualstudio.microsoft.com/visual-cpp-build-tools/")
        log("winget not available — manual install required")
        return 1, msg

    cmd = [
        "winget", "install",
        "Microsoft.VisualStudio.2022.BuildTools",
        "--override", "--quiet --wait --add "
        "Microsoft.VisualStudio.Workload.VCTools "
        "--includeRecommended",
        "--accept-source-agreements",
        "--accept-package-agreements",
    ]
    log(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        for line in output.strip().splitlines():
            log(f"  winget: {line}")
        if result.returncode == 0:
            log("MSVC Build Tools installation completed")
        else:
            log(f"winget exited with code {result.returncode}")
        return result.returncode, output
    except subprocess.TimeoutExpired:
        log("ERROR: winget timed out after 10 minutes")
        return 1, "Installation timed out after 10 minutes"
    except Exception as e:
        log(f"ERROR: {e}")
        return 1, str(e)


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="ComfyDep Manager",
        page_icon="🔧",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Custom CSS for cleaner look
    st.markdown("""
    <style>
    .conflict-red { color: #ff4b4b; font-weight: bold; }
    .ok-green { color: #21c354; font-weight: bold; }
    .warn-orange { color: #ffa534; font-weight: bold; }
    .missing-gray { color: #808495; font-style: italic; }
    div[data-testid="stMetric"] { background: #262730; padding: 12px; border-radius: 8px; }
    /* Terminal log styling */
    .terminal-log {
        background: #0e1117;
        color: #00ff41;
        font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
        font-size: 12px;
        padding: 12px;
        border-radius: 6px;
        max-height: 350px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-all;
        border: 1px solid #1e2530;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.title("ComfyDep Manager")
        st.caption("Smart dependency resolver for ComfyUI custom nodes")
        st.divider()

        nodes_dir = st.text_input(
            "Custom Nodes Folder",
            value=st.session_state.get("nodes_dir", DEFAULT_NODES_DIR),
            key="nodes_dir_input",
        )
        python_exe = st.text_input(
            "Embedded Python Executable",
            value=st.session_state.get("python_exe", DEFAULT_PYTHON_EXE),
            key="python_exe_input",
        )
        st.session_state["nodes_dir"] = nodes_dir
        st.session_state["python_exe"] = python_exe

        st.divider()
        use_github = st.checkbox("Enable GitHub fallback", value=True,
                                 help="Fetch requirements from GitHub when local files are missing")

        col1, col2 = st.columns(2)
        with col1:
            rescan = st.button("Rescan All Nodes", type="primary", use_container_width=True)
        with col2:
            refresh_pkgs = st.button("Refresh Pip List", use_container_width=True)

        if st.button("Load from Cache", use_container_width=True):
            cached = load_cache()
            if cached:
                st.session_state["nodes"] = cached
                st.toast("Loaded from cache", icon="📦")
            else:
                st.warning("No cache found")

        st.divider()
        # Quick stats
        if "nodes" in st.session_state:
            nodes = st.session_state["nodes"]
            total_nodes = len(nodes)
            nodes_with_deps = sum(1 for n in nodes if n["requirements"])
            total_reqs = sum(len(n["requirements"]) for n in nodes)
            st.metric("Scanned Nodes", total_nodes)
            st.metric("Nodes with Dependencies", nodes_with_deps)
            st.metric("Total Requirements", total_reqs)

        # ── Pip version check ─────────────────────────────────────────────────
        st.divider()
        st.caption("pip Status")
        if "pip_version" not in st.session_state:
            if Path(python_exe).exists():
                cur, latest = check_pip_version(python_exe)
                st.session_state["pip_version"] = (cur, latest)
            else:
                st.session_state["pip_version"] = (None, None)

        pip_cur, pip_latest = st.session_state["pip_version"]
        if pip_cur:
            needs_update = False
            if pip_latest:
                try:
                    needs_update = Version(pip_latest) > Version(pip_cur)
                except InvalidVersion:
                    pass

            if needs_update:
                st.warning(f"pip **{pip_cur}** installed — **{pip_latest}** available")
                if st.button("Upgrade pip", use_container_width=True):
                    with st.spinner("Upgrading pip..."):
                        rc, output = upgrade_pip(python_exe)
                    if rc == 0:
                        st.session_state.pop("pip_version", None)
                        st.toast("pip upgraded!", icon="✅")
                        st.rerun()
                    else:
                        st.error("Upgrade failed — check terminal log")
            else:
                st.success(f"pip **{pip_cur}** (up to date)")
        else:
            st.caption("Could not determine pip version")

        if st.button("Re-check pip version", use_container_width=True):
            st.session_state.pop("pip_version", None)
            st.rerun()

    # ── Scanning logic ────────────────────────────────────────────────────────
    if rescan:
        log("User triggered: Rescan All Nodes")
        with st.spinner("Scanning custom nodes..."):
            nodes_data = scan_all_nodes(nodes_dir, use_github=use_github)
            st.session_state["nodes"] = nodes_data
            save_cache(nodes_data)
            log("Cache saved to disk")
            st.session_state.pop("installed_packages", None)
            st.toast(f"Scanned {len(nodes_data)} nodes", icon="✅")

    if refresh_pkgs:
        log("User triggered: Refresh Pip List")
        st.session_state.pop("installed_packages", None)
        st.toast("Pip list will refresh", icon="🔄")

    # Lazy-load installed packages
    if "installed_packages" not in st.session_state:
        if Path(python_exe).exists():
            with st.spinner("Loading installed packages..."):
                st.session_state["installed_packages"] = get_installed_packages(python_exe)
        else:
            st.session_state["installed_packages"] = {}

    installed = st.session_state.get("installed_packages", {})

    # ── No data yet ───────────────────────────────────────────────────────────
    if "nodes" not in st.session_state:
        st.info("Click **Rescan All Nodes** in the sidebar to get started.")
        st.stop()

    nodes_data = st.session_state["nodes"]
    deps = aggregate_dependencies(nodes_data)

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_nodes, tab_deps, tab_resolver, tab_install = st.tabs([
        "📦 Nodes", "📋 Dependencies", "🔧 Resolver", "⚡ Install"
    ])

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 1: NODES
    # ══════════════════════════════════════════════════════════════════════════
    with tab_nodes:
        st.subheader("Custom Nodes Overview")

        # Filter
        filter_text = st.text_input("Filter nodes by name", "", key="node_filter")

        rows = []
        for n in nodes_data:
            if filter_text and filter_text.lower() not in n["name"].lower():
                continue
            rows.append({
                "Node": n["name"],
                "Dependencies": len(n["requirements"]),
                "Sources": ", ".join(n["sources"]) if n["sources"] else "—",
                "GitHub": n["github"] or "—",
                "Requirements": ", ".join(n["requirements"]) if n["requirements"] else "—",
            })

        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Node": st.column_config.TextColumn(width="medium"),
                    "Requirements": st.column_config.TextColumn(width="large"),
                },
            )
        else:
            st.info("No nodes found matching your filter.")

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 2: DEPENDENCIES
    # ══════════════════════════════════════════════════════════════════════════
    with tab_deps:
        st.subheader("Dependency Analysis")

        # Summary metrics
        n_ok = n_conflict = n_missing = n_warn = 0
        dep_rows = []
        for pkg_key in sorted(deps.keys()):
            info = deps[pkg_key]
            inst_ver = installed.get(pkg_key)
            status, detail = check_conflict(info["requesters"], inst_ver)
            if status == "ok":
                n_ok += 1
            elif status == "conflict":
                n_conflict += 1
            elif status == "missing":
                n_missing += 1
            else:
                n_warn += 1

            requesters_str = "; ".join(
                f"{node} ({spec})" for node, spec in info["requesters"]
            )
            dep_rows.append({
                "Package": info["display_name"],
                "Installed": inst_ver or "—",
                "Required By": requesters_str,
                "# Nodes": len(info["requesters"]),
                "Status": status.upper(),
                "Detail": detail,
                "_key": pkg_key,
                "_status": status,
            })

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("OK", n_ok)
        mc2.metric("Conflicts", n_conflict)
        mc3.metric("Missing", n_missing)
        mc4.metric("Warnings", n_warn)

        # Filter controls
        fcol1, fcol2 = st.columns([1, 3])
        with fcol1:
            status_filter = st.multiselect(
                "Filter by status",
                ["OK", "CONFLICT", "MISSING", "WARN"],
                default=["CONFLICT", "MISSING", "WARN"],
            )
        with fcol2:
            dep_search = st.text_input("Search packages", "", key="dep_search")

        filtered_rows = []
        for row in dep_rows:
            if status_filter and row["Status"] not in status_filter:
                continue
            if dep_search and dep_search.lower() not in row["Package"].lower():
                continue
            filtered_rows.append(row)

        if filtered_rows:
            display_df = pd.DataFrame(filtered_rows).drop(columns=["_key", "_status"])

            def style_status(val):
                colors = {
                    "OK": "color: #21c354",
                    "CONFLICT": "color: #ff4b4b; font-weight: bold",
                    "MISSING": "color: #808495; font-style: italic",
                    "WARN": "color: #ffa534",
                }
                return colors.get(val, "")

            styled = display_df.style.map(style_status, subset=["Status"])
            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
                height=min(len(filtered_rows) * 40 + 60, 600),
            )
        else:
            st.success("No dependency issues found!")

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 3: RESOLVER
    # ══════════════════════════════════════════════════════════════════════════
    with tab_resolver:
        st.subheader("Dependency Resolver")
        st.caption("Choose how to resolve each package. Only packages you configure here will be installed.")

        if "resolutions" not in st.session_state:
            st.session_state["resolutions"] = {}

        # Show only packages with issues by default, option to show all
        show_all = st.checkbox("Show all packages (not just conflicts/missing)", value=False)

        resolver_pkgs = []
        for pkg_key in sorted(deps.keys()):
            info = deps[pkg_key]
            inst_ver = installed.get(pkg_key)
            status, detail = check_conflict(info["requesters"], inst_ver)
            if not show_all and status == "ok":
                continue
            resolver_pkgs.append((pkg_key, info, inst_ver, status, detail))

        if not resolver_pkgs:
            st.success("All dependencies are satisfied! Nothing to resolve.")
        else:
            # Quick action buttons
            qcol1, qcol2, qcol3 = st.columns(3)
            with qcol1:
                if st.button("Auto-resolve all (best guess)"):
                    for pkg_key, info, inst_ver, status, detail in resolver_pkgs:
                        best = find_best_version(info["requesters"], inst_ver)
                        if best and best != inst_ver:
                            st.session_state["resolutions"][pkg_key] = {
                                "action": "install",
                                "version": best,
                                "display_name": info["display_name"],
                            }
                    st.rerun()
            with qcol2:
                if st.button("Skip all"):
                    for pkg_key, info, _, _, _ in resolver_pkgs:
                        st.session_state["resolutions"][pkg_key] = {
                            "action": "skip",
                            "version": "",
                            "display_name": info["display_name"],
                        }
                    st.rerun()
            with qcol3:
                if st.button("Clear all choices"):
                    st.session_state["resolutions"] = {}
                    st.rerun()

            st.divider()

            for pkg_key, info, inst_ver, status, detail in resolver_pkgs:
                with st.container(border=True):
                    hcol1, hcol2, hcol3 = st.columns([2, 1, 1])
                    with hcol1:
                        status_emoji = {"ok": "🟢", "conflict": "🔴", "missing": "⚪", "warn": "🟡"}
                        st.markdown(f"**{status_emoji.get(status, '❓')} {info['display_name']}**")
                    with hcol2:
                        st.caption(f"Installed: `{inst_ver or 'N/A'}`")
                    with hcol3:
                        is_special = normalize_name(info["display_name"]) in SPECIAL_PACKAGES
                        if is_special:
                            st.caption("⚠️ Special package")

                    # Show who needs it
                    req_parts = []
                    for node, spec in info["requesters"]:
                        req_parts.append(f"`{node}` → {spec}")
                    st.caption(" | ".join(req_parts))

                    # Resolution options
                    best = find_best_version(info["requesters"], inst_ver)
                    current = st.session_state["resolutions"].get(pkg_key, {})

                    options = ["Skip"]
                    option_labels = ["Skip this package"]

                    if best:
                        options.append(f"install:{best}")
                        option_labels.append(f"Install best compatible: {best}")

                    # Offer each pinned version from requesters
                    pinned = set()
                    for _, spec_str in info["requesters"]:
                        if spec_str.startswith("=="):
                            pinned.add(spec_str[2:])
                    for pv in sorted(pinned):
                        key = f"install:{pv}"
                        if key not in options:
                            options.append(key)
                            option_labels.append(f"Use exact version: {pv}")

                    options.append("latest")
                    option_labels.append("Install latest (pip default)")
                    options.append("manual")
                    option_labels.append("Manual version input")

                    # Determine default index
                    default_idx = 0
                    if current.get("action") == "skip":
                        default_idx = 0
                    elif current.get("action") == "install" and current.get("version"):
                        target = f"install:{current['version']}"
                        if target in options:
                            default_idx = options.index(target)

                    choice = st.radio(
                        f"Action for {info['display_name']}",
                        option_labels,
                        index=default_idx,
                        key=f"resolve_{pkg_key}",
                        horizontal=True,
                        label_visibility="collapsed",
                    )

                    chosen_idx = option_labels.index(choice)
                    chosen_option = options[chosen_idx]

                    if chosen_option == "manual":
                        manual_ver = st.text_input(
                            "Version spec",
                            value=current.get("version", ""),
                            key=f"manual_{pkg_key}",
                            placeholder="e.g. 2.1.0 or >=2.0,<3.0",
                        )
                        if manual_ver:
                            st.session_state["resolutions"][pkg_key] = {
                                "action": "install",
                                "version": manual_ver,
                                "display_name": info["display_name"],
                            }
                        else:
                            st.session_state["resolutions"].pop(pkg_key, None)
                    elif chosen_option == "Skip":
                        st.session_state["resolutions"][pkg_key] = {
                            "action": "skip",
                            "version": "",
                            "display_name": info["display_name"],
                        }
                    elif chosen_option == "latest":
                        st.session_state["resolutions"][pkg_key] = {
                            "action": "install",
                            "version": "",
                            "display_name": info["display_name"],
                        }
                    elif chosen_option.startswith("install:"):
                        ver = chosen_option.split(":", 1)[1]
                        st.session_state["resolutions"][pkg_key] = {
                            "action": "install",
                            "version": ver,
                            "display_name": info["display_name"],
                        }

    # ══════════════════════════════════════════════════════════════════════════
    # TAB 4: INSTALL
    # ══════════════════════════════════════════════════════════════════════════
    with tab_install:
        st.subheader("Installation")

        # ── Environment Health Check ──────────────────────────────────────────
        st.markdown("### Environment Health")
        health_col1, health_col2 = st.columns(2)

        # Corrupted packages check
        with health_col1:
            corrupted = find_corrupted_packages(python_exe)
            if corrupted:
                with st.container(border=True):
                    st.markdown(f"**Corrupted Packages ({len(corrupted)})**")
                    st.caption(
                        "Broken partial installs in site-packages (folders starting with `~`). "
                        "These cause pip warnings and can interfere with installs."
                    )
                    for p in corrupted:
                        st.text(f"  {p.name}/")
                    if st.button("Auto-fix: Delete All Corrupted Packages",
                                 type="primary", key="fix_corrupted_btn"):
                        with st.spinner("Removing corrupted packages..."):
                            deleted, failed = remove_corrupted_packages(python_exe)
                        if deleted:
                            st.success(f"Deleted {len(deleted)} corrupted packages: {', '.join(deleted)}")
                        if failed:
                            st.error(f"Failed to delete: {', '.join(failed)}")
                        if deleted and not failed:
                            st.rerun()
            else:
                with st.container(border=True):
                    st.markdown("**Corrupted Packages**")
                    st.success("No corrupted packages found")

        # MSVC check
        with health_col2:
            with st.container(border=True):
                st.markdown("**C/C++ Compiler (MSVC)**")
                msvc_ok = check_msvc_installed()
                if msvc_ok:
                    st.success("Visual C++ Build Tools detected")
                else:
                    st.caption(
                        "Not installed. Some packages with C extensions "
                        "(e.g., hydra, bitsandbytes) will fail to build."
                    )
                    if st.button("Install Visual C++ Build Tools",
                                 type="primary", key="install_msvc_btn"):
                        if not check_winget_available():
                            st.error(
                                "**winget** is not available on this system.\n\n"
                                "Please install the Build Tools manually from: "
                                "[visualstudio.microsoft.com]"
                                "(https://visualstudio.microsoft.com/visual-cpp-build-tools/)"
                            )
                        else:
                            st.info(
                                "This will install **Visual Studio 2022 Build Tools** "
                                "with the C++ workload via winget. "
                                "The install may take several minutes and will require "
                                "a restart before the compiler is usable."
                            )
                            if st.button("Confirm Install", key="confirm_msvc"):
                                with st.spinner("Installing MSVC Build Tools (this may take a while)..."):
                                    rc, output = install_msvc_build_tools()
                                if rc == 0:
                                    st.success(
                                        "Build Tools installed! **Restart your machine** "
                                        "before building packages that need a C compiler."
                                    )
                                else:
                                    st.error("Installation failed — check the terminal log for details")
                                st.code(output, language="text")

        st.divider()

        # ── Package Install ───────────────────────────────────────────────────
        resolutions = st.session_state.get("resolutions", {})
        to_install = {
            k: v for k, v in resolutions.items()
            if v.get("action") == "install"
        }

        if not to_install:
            st.info("No packages selected for installation. Use the **Resolver** tab to choose packages.")
            st.stop()

        # Build install list
        st.markdown("### Packages to Install")
        install_specs = []
        rows = []
        for pkg_key, res in sorted(to_install.items()):
            name = res["display_name"]
            ver = res.get("version", "")
            if ver and not any(c in ver for c in ("<", ">", "!", "~", "=")):
                spec = f"{name}=={ver}"
            elif ver:
                spec = f"{name}{ver}"
            else:
                spec = name
            install_specs.append(spec)
            inst_ver = installed.get(pkg_key, "—")
            is_special = normalize_name(name) in SPECIAL_PACKAGES
            rows.append({
                "Package": name,
                "Current": inst_ver,
                "Target": ver or "(latest)",
                "Spec": spec,
                "Special": "⚠️" if is_special else "",
            })

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Warn about special packages
        special_in_list = [r["Package"] for r in rows if r["Special"]]
        if special_in_list:
            st.warning(
                f"Special packages detected: **{', '.join(special_in_list)}**. "
                "These may require specific index URLs (e.g., PyTorch). "
                "Consider installing them manually with the correct --index-url."
            )

        # Extra pip args
        extra_args = st.text_input(
            "Extra pip arguments (optional)",
            placeholder="e.g. --index-url https://download.pytorch.org/whl/cu121",
            key="extra_pip_args",
        )

        st.divider()

        # Export / import profile
        ecol1, ecol2 = st.columns(2)
        with ecol1:
            if st.button("Export resolution profile"):
                profile = json.dumps(resolutions, indent=2)
                st.download_button(
                    "Download profile JSON",
                    data=profile,
                    file_name="comfydep_profile.json",
                    mime="application/json",
                )
        with ecol2:
            uploaded = st.file_uploader("Import resolution profile", type=["json"], key="import_profile")
            if uploaded:
                try:
                    imported = json.loads(uploaded.read())
                    st.session_state["resolutions"] = imported
                    st.toast("Profile imported!", icon="📥")
                    st.rerun()
                except Exception as e:
                    st.error(f"Invalid profile: {e}")

        st.divider()

        # Action buttons
        bcol1, bcol2, bcol3 = st.columns(3)
        with bcol1:
            do_dry_run = st.button("🔍 Dry Run", use_container_width=True)
        with bcol2:
            do_backup = st.button("💾 Backup pip freeze", use_container_width=True)
        with bcol3:
            do_install = st.button("⚡ Install Selected", type="primary", use_container_width=True)

        if do_backup:
            path = backup_pip_freeze(python_exe)
            if path:
                st.success(f"Backup saved to: `{path}`")
            else:
                st.error("Failed to create backup")

        full_specs = install_specs
        if extra_args:
            full_specs = extra_args.split() + install_specs

        if do_dry_run:
            rc, output = run_pip_install(python_exe, full_specs, dry_run=True)
            st.code(output, language="bash")

        if do_install:
            if not Path(python_exe).exists():
                st.error(f"Python executable not found: `{python_exe}`")
            else:
                # Auto-backup before install
                bpath = backup_pip_freeze(python_exe)
                if bpath:
                    st.caption(f"Auto-backup saved: {bpath}")

                with st.spinner("Installing packages..."):
                    progress = st.progress(0, text="Running pip install...")
                    rc, output = run_pip_install(python_exe, full_specs, dry_run=False)
                    progress.progress(100, text="Done!")

                if rc == 0:
                    st.success("Installation completed successfully!")
                else:
                    st.error(f"Installation failed (exit code {rc})")
                    # Diagnose the output and show actionable hints with fix buttons
                    hints = diagnose_pip_output(output)
                    for title, advice, fix_key in hints:
                        st.warning(f"**{title}**\n\n{advice}")
                        if fix_key == "fix_corrupted":
                            st.info("Use the **Environment Health** section above to auto-fix corrupted packages.")
                        elif fix_key == "install_msvc":
                            st.info("Use the **Environment Health** section above to install the C++ Build Tools.")

                st.code(output, language="text")

                # Refresh installed packages
                st.session_state.pop("installed_packages", None)
                st.toast("Refresh the page to see updated package versions", icon="🔄")

    # ══════════════════════════════════════════════════════════════════════════
    # COLLAPSIBLE TERMINAL LOG (always at the bottom)
    # ══════════════════════════════════════════════════════════════════════════
    render_terminal_log()


def render_terminal_log():
    """Render the collapsible activity log at the bottom of the page."""
    activity = _get_log()
    count = len(activity)
    label = f"Terminal Log ({count} entries)" if count else "Terminal Log"

    with st.expander(label, expanded=False):
        col1, col2 = st.columns([4, 1])
        with col2:
            if st.button("Clear Log", key="clear_log", use_container_width=True):
                log_clear()
                st.rerun()

        if activity:
            log_text = "\n".join(activity)
            # st.code renders with Streamlit's built-in copy button (top-right icon)
            st.code(log_text, language="log", wrap_lines=True)
        else:
            st.caption("No activity yet. Actions will be logged here.")


if __name__ == "__main__":
    main()
