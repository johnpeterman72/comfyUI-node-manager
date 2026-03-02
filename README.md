# ComfyDep Manager

A smart dependency resolver for ComfyUI custom nodes. Scans all installed nodes, detects package version conflicts, and provides an interactive UI to resolve and install the right versions into ComfyUI's embedded Python environment.

## The Problem

Every ComfyUI user knows the pain: different custom nodes demand conflicting versions of the same packages (`torch`, `numpy`, `torchvision`, `transformers`, `diffusers`, etc.). Installing one node breaks another. This tool gives you full visibility and control over every dependency across all your nodes.

## Quick Start

```bash
# Install manager dependencies
pip install -r requirements.txt

# Launch the app
streamlit run app.py
```

Or on Windows, just double-click **`run.bat`**.

The app opens at [http://localhost:8501](http://localhost:8501). Click **Rescan All Nodes** in the sidebar to begin.

## Default Paths

Configurable in the sidebar:

| Setting | Default |
|---------|---------|
| Custom Nodes Folder | `D:\SwarmUI\dlbackend\comfy\ComfyUI\custom_nodes` |
| Embedded Python | `D:\SwarmUI\dlbackend\comfy\python_embeded\python.exe` |

## Features

### Node Score Cards (Nodes Tab)

Each custom node gets a score card showing dependency count, conflict count, missing packages, and a health percentage. Three view modes:

- **List** — compact expandable rows with colored status dots
- **Grid** — 3-column card layout with colored borders (red = conflicts, orange = missing, green = healthy)
- **Detail** — full info view with metrics, health bars, and all requirements

Sort by name, dependency count, conflicts, missing packages, or health score.

### Dependency Analysis (Dependencies Tab)

Every package aggregated across all nodes:

- Current installed version
- Which nodes require it and their exact version specs
- Conflict status: OK / CONFLICT / MISSING / WARN
- Filter by status or search by package name

### Interactive Resolver (Resolver Tab)

Choose how to resolve each package:

- **Best compatible** — automatically computed version satisfying the most specifiers
- **Exact version from a node** — use a specific node's pinned version
- **Latest** — let pip pick the newest
- **Manual** — enter any version or range
- **Skip** — leave this package alone

Bulk actions: Auto-resolve all, Skip all, Clear all choices.

### Installation (Install Tab)

#### Environment Health Panel

Two automated checks run before every install:

- **Corrupted Packages** — scans `site-packages` for broken `~tilde` folders left from failed installs. One-click button to delete them all.
- **C/C++ Compiler (MSVC)** — detects if Visual C++ Build Tools are installed. If missing, offers one-click install via `winget`.

#### Package Installation

- Review all selected packages in a summary table
- Dry run mode to preview the pip command
- Automatic `pip freeze` backup before every install
- Extra pip arguments support (e.g., `--index-url` for PyTorch wheels)
- Warnings for special packages (torch, xformers, etc.)
- Smart error diagnostics: detects MSVC errors, wheel build failures, permission issues, corrupted packages, and shows actionable fix instructions
- Export/import resolution profiles as JSON

### pip Update Checker (Sidebar)

Checks the embedded Python's pip version against PyPI on startup. Shows a warning with one-click upgrade button when an update is available.

### Terminal Log

Collapsible terminal at the bottom of every page showing timestamped activity: scanning, pip operations, installs, errors. Includes a copy button for easy troubleshooting.

### GitHub Fallback

When a node has no local dependency files, the app detects its GitHub repo (from `.git/config` or README) and fetches `requirements.txt` from the remote.

### Other Features

- Scan result caching (JSON on disk)
- Floating scroll-to-top button
- Configurable paths in the sidebar

## File Structure

```
comfydep/
  app.py              # Main application (single file)
  requirements.txt    # Manager's own dependencies
  run.bat             # Windows launcher
  backups/            # Auto-created pip freeze backups
  .comfydep_cache.json  # Auto-created scan cache
```

## Tech Stack

- **Streamlit** — UI framework
- **packaging** — PEP 440 requirement parsing and version comparison
- **pandas** — data display
- **requests** — GitHub fallback
- **subprocess** — pip integration and system tool execution

## Requirements

- Python 3.10+
- Streamlit >= 1.30.0
- pandas >= 2.0.0
- packaging >= 23.0
- requests >= 2.28.0

## License

MIT
