# ComfyDep Manager — Project Scope

## Purpose

ComfyDep Manager is an internal tool for managing Python package dependencies across ComfyUI custom nodes. It targets users running ComfyUI via SwarmUI (or standalone) on Windows with an embedded Python environment.

## Problem Statement

ComfyUI custom nodes each declare their own Python dependencies, often pinning conflicting versions of the same package. Users face a constant cycle of broken nodes after installing or updating others. There is no built-in way to see the full dependency picture or resolve conflicts intelligently.

## Goals

1. **Visibility** — Show every dependency from every installed node in one place, grouped by package, with conflict detection.
2. **Control** — Let the user choose exact versions for each package, informed by what each node needs and what's currently installed.
3. **Safety** — Backup the current environment before changes, support dry runs, and provide clear error diagnostics.
4. **Automation** — Auto-detect and fix common environment issues (corrupted packages, missing build tools) without requiring manual intervention.
5. **Simplicity** — Single-file Streamlit app, no external database, no server infrastructure. Run it, use it, close it.

## Target Environment

- **OS:** Windows 10/11
- **Python:** Embedded CPython 3.11 (shipped with ComfyUI/SwarmUI)
- **Package manager:** pip (bundled with embedded Python)
- **Node location:** `custom_nodes/` directory inside ComfyUI
- **Launcher:** `run.bat` or `streamlit run app.py`

## Scope Boundaries

### In Scope

- Scanning `custom_nodes/` for dependency declarations (`requirements.txt`, `pyproject.toml`, `setup.py`, `install.py`)
- GitHub fallback for nodes missing local dependency files
- Aggregating all requirements by package name
- Detecting version conflicts against the installed environment
- Interactive resolution: user picks version per package
- Installing via `pip install` into the embedded Python
- Pre-install backup (`pip freeze`)
- Detecting and auto-fixing corrupted `~tilde` packages in `site-packages`
- Detecting missing MSVC Build Tools and offering automated install via `winget`
- Smart error diagnostics with actionable fix suggestions
- Scan caching, resolution profile export/import
- pip version checking and one-click upgrade
- Activity logging with copy-to-clipboard

### Out of Scope

- Managing ComfyUI itself (updates, config, model downloads)
- Managing nodes (install/uninstall/update nodes) — only their *dependencies*
- Linux/macOS support (Windows-only paths and tools like winget/MSVC)
- Multi-environment support (only targets one embedded Python at a time)
- Automatic conflict resolution without user input (always user-confirmed)
- PyPI version listing / fetching all available versions for a package
- Dependency tree resolution (pip handles transitive deps)
- Running as a persistent service or daemon

## Architecture

### Single File Design

The entire application is a single `app.py` file (~1700 lines). This is intentional:
- Easy to distribute and update
- No import chain to debug
- Grep-friendly for the entire codebase
- Fits the "internal tool" use case — not a library, not a framework

### Data Flow

```
custom_nodes/ ──scan──> node metadata (JSON)
                            │
                            ├── requirements parsed via `packaging`
                            ├── GitHub fallback if local files missing
                            └── cached to .comfydep_cache.json
                            │
pip list ──────────────> installed packages dict
                            │
                            ▼
                    aggregate + conflict detection
                            │
                            ▼
                    Streamlit UI (4 tabs)
                            │
                            ▼
                    pip install (subprocess)
```

### Key Design Decisions

- **Streamlit over Flask/React**: This is an internal power-user tool, not a public web app. Streamlit gives us a rich UI with zero frontend code.
- **`packaging` library**: PEP 440 compliant parsing — handles version specifiers correctly instead of string matching.
- **Subprocess for pip**: We run pip as a subprocess against the *embedded* Python, not the system Python. This is critical.
- **No database**: JSON cache + session state. The data is small and ephemeral.
- **User-confirmed installs only**: The resolver suggests but never auto-installs. Safety first.

## Future Considerations

These are not planned but could be valuable:

- **PyPI version fetching**: Show all available versions for a package so the user can pick from a dropdown instead of typing manually.
- **Dependency graph visualization**: Show which nodes share packages as a network graph.
- **Batch node update check**: Compare installed node versions against their GitHub repos.
- **Linux/macOS paths**: Make path detection OS-aware.
- **Pre-built wheel search**: When a package fails to build, automatically search for compatible wheels on PyPI.
