# ComfyDep Manager

A smart dependency resolver for ComfyUI custom nodes. Scans all installed nodes, detects package version conflicts, and lets you pick exact versions to install into ComfyUI's embedded Python environment.

## The Problem

Every ComfyUI user knows the pain: different custom nodes demand conflicting versions of the same packages (`torch`, `numpy`, `torchvision`, `transformers`, `diffusers`, etc.). Installing one node breaks another. This tool gives you visibility and control.

## Features

- **Smart Node Scanning** — Scans your `custom_nodes` directory, parsing `requirements.txt`, `pyproject.toml`, `setup.py`, and `install.py` from each node
- **GitHub Fallback** — When local dependency info is missing, fetches requirements from the node's GitHub repo automatically
- **Conflict Detection** — Aggregates every package across all nodes, highlights version conflicts, and shows which nodes require what
- **Visual Resolver** — Choose how to resolve each conflict: pick a compatible version, use a specific node's requirement, enter a manual version, or skip
- **Direct Installation** — Installs packages directly into ComfyUI's embedded Python environment with dry-run support and automatic pip freeze backups
- **Export/Import Profiles** — Save your resolution choices as a JSON profile and share or reuse them

## Quick Start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then in the sidebar:
1. Verify or update the paths to your `custom_nodes` folder and embedded Python executable
2. Click **Rescan All Nodes**
3. Use the **Dependencies** tab to see conflicts
4. Use the **Resolver** tab to choose versions
5. Use the **Install** tab to apply changes

## Default Paths

These are configurable in the sidebar:

| Setting | Default |
|---------|---------|
| Custom Nodes Folder | `D:\SwarmUI\dlbackend\comfy\ComfyUI\custom_nodes` |
| Embedded Python | `D:\SwarmUI\dlbackend\comfy\python_embeded\python.exe` |

## Tabs

### Nodes
Overview of every detected custom node with its raw requirements and dependency sources.

### Dependencies
The heart of the app — every package grouped by name, showing current installed version, which nodes require it and their exact version specs, and conflict status (OK / CONFLICT / MISSING / WARN).

### Resolver
Interactive resolution for each package: best compatible version, exact version from a node, latest, manual input, or skip.

### Install
Review your selections, run a dry run, and install with one click. Includes automatic pip freeze backup, extra pip arguments support, warnings for special packages, and resolution profile export/import.

## Tech Stack

- Python + Streamlit
- `packaging` for standards-compliant requirement parsing
- `pandas` for data display
- `requests` for GitHub fallback
- `subprocess` for pip integration

## License

MIT
