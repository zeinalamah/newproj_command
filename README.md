# newproj – Standard Project‑Folder CLI

> **Author:** Zein Alamah | **License:** MIT

## 1  Why newproj?

Creating the same web of folders every time you start a research, consulting, or side project is tedious and error‑prone. **newproj** removes that friction: one command spins up a clean, predictable directory tree and a starter README/.gitignore, so you jump straight into coding or writing.

Having a *standard* structure also makes life easier for:

- **Collaborators & reviewers** – they instantly know where to look.
- **Automation scripts** – paths are consistent across projects.
- **Reproducibility** – data, code, and results live in well‑defined places.

---

## 2  Features

| Capability                   | What it does                                    |
| ---------------------------- | ----------------------------------------------- |
| `create`                     | Generate a project folder from a named template |
| `list`                       | Show all available template names               |
| `show`                       | Print the folder/file tree for a given template |
| `add`                        | Define a new template on the fly                |
| `rename`                     | Rename an existing template key                 |
| Auto‑README                  | Seeds a README stub with date & template info   |
| Auto‑`.gitignore`            | Drops a sensible starter ignore file            |
| Optional Git init            | `--git` flag runs `git init` in the new project |
| **No external dependencies** | 100 % Python ≥3.7 std‑lib                       |

---

## 3  Installation

### 3.1  Editable mode (recommended for tweaking code/templates)

```bash
# Clone or download this repo
cd newproj

# Install locally – Windows / macOS / Linux
pip install --user -e .
# or keep it isolated with pipx
# pipx install --editable .
```

> The `-e .` flag creates a lightweight link, so **any edits you make to **``** or **``** take effect immediately**.

Make sure your Python `Scripts` directory is on **PATH** (Windows example):

```
%USERPROFILE%\AppData\Roaming\Python\Python310\Scripts
```

### 3.2  Fixed (non‑editable) install

Prefer a copy that *doesn’t* change unless you reinstall?

```bash
pip install --user .   # no -e flag
```

Re‑run the same command whenever you pull updates.

---

## 4  Command Overview

| Global help     | `newproj -h` or `newproj -help`                              |
| --------------- | ------------------------------------------------------------ |
| List templates  | `newproj list`                                               |
| Show structure  | `newproj show Academic`                                      |
| Add template    | `newproj add -t Workshop --items "data,code,docs/readme.md"` |
| Rename template | `newproj rename Workshop Training`                           |
| Create project  | `newproj create -n "MyProj" -t Academic [options]`           |

### `create` options

| Flag          | Purpose                                  |
| ------------- | ---------------------------------------- |
| `--git`       | Initialise a Git repo in the new folder  |
| `--datestamp` | Append `_YYYY‑MM‑DD` to the project name |
| `--dry-run`   | Preview the tree without touching disk   |
| `--path DIR`  | Root directory (default: current dir)    |
| `--no-open`   | Skip opening the folder after creation   |

Run `newproj create -h` for a sub‑command help screen with examples.

---

## 5  Working Example

```bash
# Academic template, today’s date appended, Git initialised
newproj create -n "ClimateImpact" -t Academic --datestamp --git
```

Creates (abridged):

```
ClimateImpact_2025-06-12/
├── README.md
├── .gitignore
├── data/ raw | processed
├── code/ scripts | notebooks
├── results/ figures | tables
├── writing/ manuscript | refs | notes.md
└── slides/ assets
```

…then opens the folder in your file explorer.

---

## 6  Customising Templates

Templates live in `` at the repo root. Each key maps to an array of folder/file paths.  Example:

```json
{
  "Academic": [
    "data/raw",
    "data/processed",
    "code/scripts",
    "writing/notes.md"
  ]
}
```

### 6.1  Via CLI (preferred)

```bash
# Add
newproj add -t Workshop --items "datasets,code,docs/readme.md,notes.txt"
# Rename
newproj rename Workshop Training
```

### 6.2  Manual edit

Open `project_templates.json` in your editor, change the arrays, save.  In editable mode, changes are instant; in fixed mode, reinstall (`pip install --user .`).

---

## 7  Files to Commit

Commit **all** of these to your Git repository:

```
LICENSE
README.md
setup.py
requirements.txt
MANIFEST.in
newproj.py
project_templates.json
```

`setup.py` + `MANIFEST.in` ensure `project_templates.json` ships with the package.

---

## 8  License

```
MIT License – see LICENSE for full text.
```

You are free to use, copy, modify, merge, publish, or distribute this software.

---

## 9  Disclaimer

This tool is provided **“as is.”**  Review the code before running it.  By using **newproj** you accept full responsibility for any outcome; the author cannot be held liable for damages or data loss.

---


I hope **newproj** saves you time and brings order to your projects.

