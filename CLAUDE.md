# CLAUDE.md — ComfyDep Manager

## Project Overview

ComfyDep Manager is a single-file Streamlit app (`app.py`, ~1700 lines) that scans ComfyUI custom nodes for dependency conflicts and helps resolve them. It targets Windows with an embedded Python environment.

## Key Files

- `app.py` — The entire application. Single file by design.
- `requirements.txt` — Manager's own pip dependencies (streamlit, pandas, packaging, requests).
- `run.bat` — Windows launcher (installs deps + runs streamlit).
- `backups/` — Auto-created directory for pip freeze snapshots.
- `.comfydep_cache.json` — Auto-created scan result cache.

## Architecture

The app is organized into sections within `app.py`:

1. **Activity Log** — `log()`, `_get_log()`, `log_clear()` — in-memory timestamped log rendered as terminal
2. **Defaults & Constants** — paths, special packages list, GitHub URLs
3. **Pip Version Check** — `check_pip_version()`, `upgrade_pip()`
4. **Parsing** — `safe_parse_requirement()`, `parse_requirements_txt()`, `parse_pyproject_toml()`, `parse_setup_py()`, `parse_install_py()`
5. **GitHub Fallback** — `detect_github_url()`, `fetch_github_requirements()`
6. **Scanning** — `scan_node()`, `scan_all_nodes()`
7. **Installed Packages** — `get_installed_packages()` via `pip list --format=json`
8. **Dependency Aggregation** — `aggregate_dependencies()`, `check_conflict()`, `find_best_version()`
9. **Cache** — `save_cache()`, `load_cache()` — JSON on disk
10. **Backup** — `backup_pip_freeze()`
11. **Installation** — `run_pip_install()`, `diagnose_pip_output()`, `BUILD_ERROR_HINTS`
12. **Auto-fix** — `find_corrupted_packages()`, `remove_corrupted_packages()`, `check_msvc_installed()`, `install_msvc_build_tools()`
13. **Node Card Renderers** — `_render_nodes_list()`, `_render_nodes_grid()`, `_render_nodes_detail()`, `_health_bar()`, `_status_dots()`
14. **Main UI** — `main()` — Streamlit layout with sidebar + 4 tabs

## Important Patterns

### Session State Keys

All persistent UI state lives in `st.session_state`:
- `nodes` — list of scanned node dicts
- `installed_packages` — dict of {normalized_name: version}
- `resolutions` — dict of {pkg_key: {action, version, display_name}}
- `pip_version` — tuple of (current, latest)
- `activity_log` — list of log strings
- `resolve_{pkg_key}` — radio widget state per package (Resolver tab)
- `manual_{pkg_key}` — manual version input state per package

### Package Name Normalization

Always use `normalize_name()` when comparing package names. It lowercases and replaces `-` and `.` with `_`. This matches pip's behavior.

### Embedded Python

All pip commands MUST target the embedded Python executable, not the system Python. The path is configurable in the sidebar and stored in `st.session_state["python_exe"]`.

### Widget Key Cleanup

When bulk-clearing resolver choices (Skip All, Clear All), you MUST also delete the cached radio widget keys (`resolve_*`, `manual_*`) from session state before calling `st.rerun()`. Otherwise the radios re-populate resolutions from their cached values.

## Default Paths

```
Custom Nodes: D:\SwarmUI\dlbackend\comfy\ComfyUI\custom_nodes
Embedded Python: D:\SwarmUI\dlbackend\comfy\python_embeded\python.exe
```

## Common Tasks

### Adding a new error diagnostic

Add a tuple to `BUILD_ERROR_HINTS`:
```python
(r"regex pattern", "Title", "Markdown advice text", "optional_fix_key")
```
If `fix_key` is set, the UI will show a pointer to the Environment Health panel.

### Adding a new auto-fix

1. Write a detection function (e.g., `check_something()`) and a fix function (e.g., `fix_something()`)
2. Add them to the Environment Health panel in the Install tab section of `main()`
3. Optionally add a corresponding `BUILD_ERROR_HINTS` entry with a `fix_key`

### Adding a new node card view

1. Write `_render_nodes_newview(nodes: list[dict])` following the pattern of existing renderers
2. Add the view name to the radio options in the Nodes tab section of `main()`
3. Add the elif branch to call your renderer

## Testing

No test suite — this is an internal tool. Verify changes by:
1. `python -c "import app; print('OK')"` — module loads without error
2. `streamlit run app.py` — app starts and renders
3. Click "Rescan All Nodes" — verify node cards populate
4. Check Dependencies tab — verify conflict detection
5. Test Resolver bulk buttons — Skip All, Clear All, Auto-resolve

## GitHub

Repository: https://github.com/johnpeterman72/comfyUI-node-manager
