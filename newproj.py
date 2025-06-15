#!/usr/bin/env python3
"""
newproj – standard project-folder generator and template manager
Author: Zein Alamah • MIT License
"""
from __future__ import annotations

import argparse, json, os, subprocess, sys, textwrap
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# ----------------------------------------------------------------------
TEMPLATE_FILE = Path(__file__).with_name("project_templates.json")


def load_templates() -> Dict[str, List[str]]:
    if not TEMPLATE_FILE.exists():
        sys.exit(f"Template file not found: {TEMPLATE_FILE}")
    try:
        return json.loads(TEMPLATE_FILE.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Template file is not valid JSON:\n  {e}")


def save_templates(templates: Dict[str, List[str]]):
    TEMPLATE_FILE.write_text(json.dumps(templates, indent=2))
    print(f"✔ Templates saved to {TEMPLATE_FILE}")


# ----------------------------------------------------------------------
def render_tree(items: List[str]) -> str:
    out: List[str] = []
    for p in sorted(items):
        depth = p.count(os.sep)
        out.append(f"{'  ' * depth}└─ {Path(p).name if depth else p}")
    return "\n".join(out)


def build_structure(root: Path, items: List[str], dry=False):
    for item in items:
        tgt = root / Path(item)
        if dry:
            print(f"[dry-run] {'file' if tgt.suffix else 'dir '} → {tgt}")
            continue
        if tgt.suffix in {".md", ".txt"}:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.touch(exist_ok=True)
        else:
            tgt.mkdir(parents=True, exist_ok=True)


def write_readme(root: Path, name: str, template: str):
    (root / "README.md").write_text(
        f"""# {name}  ·  {template} template\nCreated: {datetime.now():%Y-%m-%d}\n
        

                This project was generated with **newproj**.\n 
                MIT License - Copyright (c) 2025 Zein Alamah \n
                Edit this README to document the project-specific workflow.
      
      """
      
    )


def write_gitignore(root: Path):
    (root / ".gitignore").write_text(
        textwrap.dedent(
            """
            # Python cache
            __pycache__/
            *.py[cod]

            # Virtual environments
            venv/
            .env/

            # OS metadata
            .DS_Store
            Thumbs.db
            """
        ).lstrip()
    )


def init_git(root: Path):
    try:
        subprocess.run(
            ["git", "init"],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("⚠ Git executable not found; skipping git init.")


def open_folder(root: Path):
    if sys.platform == "win32":
        os.startfile(str(root))
    elif sys.platform == "darwin":
        subprocess.run(["open", root])
    else:
        subprocess.run(["xdg-open", root])


# ======================================================================
def main():
    # ---------- top-level parser ----------
    parser = argparse.ArgumentParser(
        prog="newproj",
        description="Create organised project folders from named templates.",
        epilog=textwrap.dedent(
            """
            common examples
              newproj list
              newproj show Academic
              newproj add -t Workshop --items "data,code,docs/readme.md"
              newproj rename Workshop Training
              newproj create -n "StudyA" -t Academic --git --datestamp
            """
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,  # we'll add -h/-help manually
    )

    # global help flags
    parser.add_argument("-h", "-help", action="help", help="show this help message")

    sub = parser.add_subparsers(dest="command", required=True)

    # ---------- list ----------
    sub.add_parser("list", help="list all template names")

    # ---------- show ----------
    show = sub.add_parser(
        "show",
        help="display the folder / file layout for a template",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    show.add_argument("template", help="template name to inspect")

    # ---------- add ----------
    add = sub.add_parser(
        "add",
        help="create a new template",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="example:\n  newproj add -t Workshop --items \"data,code,docs/readme.md\"",
    )
    add.add_argument("-t", "--template", required=True, help="new template name")
    add.add_argument(
        "--items",
        required=True,
        help="comma-separated list of folders / files (use / for subfolders)",
    )

    # ---------- rename ----------
    ren = sub.add_parser(
        "rename",
        help="rename an existing template",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="example:\n  newproj rename Workshop Training",
    )
    ren.add_argument("old", help="current template name")
    ren.add_argument("new", help="new template name")

    # ---------- create ----------
    create = sub.add_parser(
        "create",
        help="generate a project folder from a template",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=textwrap.dedent(
            """
            examples
              newproj create -n "MyProj" -t Academic
              newproj create -n DataRun -t Personal --git --datestamp
              newproj create -n Draft -t Consulting --dry-run
            """
        ),
    )
    create.add_argument("-n", "--name", required=True, help="project folder name")
    create.add_argument("-t", "--template", required=True, help="template to use")
    create.add_argument(
        "-p",
        "--path",
        default=".",
        help="root directory (default: current location)",
    )
    create.add_argument(
        "--datestamp", action="store_true", help="append _YYYY-MM-DD to folder name"
    )
    create.add_argument(
        "--git", action="store_true", help="initialise a git repository in the folder"
    )
    create.add_argument(
        "--no-open", action="store_true", help="skip opening the folder in Explorer"
    )
    create.add_argument(
        "--dry-run", action="store_true", help="show what would be created"
    )

    # ------------------------------------------------------------------
    args = parser.parse_args()
    templates = load_templates()

    # ---------- list ----------
    if args.command == "list":
        print("available templates:")
        for t in templates:
            print(" •", t)
        return

    # ---------- show ----------
    if args.command == "show":
        if args.template not in templates:
            sys.exit(f"template '{args.template}' not found.")
        print(render_tree(templates[args.template]))
        return

    # ---------- add ----------
    if args.command == "add":
        if args.template in templates:
            sys.exit("template already exists.")
        items = [
            p.strip().replace("\\", "/") for p in args.items.split(",") if p.strip()
        ]
        templates[args.template] = items
        save_templates(templates)
        return

    # ---------- rename ----------
    if args.command == "rename":
        if args.old not in templates:
            sys.exit(f"template '{args.old}' not found.")
        if args.new in templates:
            sys.exit(f"template '{args.new}' already exists.")
        templates[args.new] = templates.pop(args.old)
        save_templates(templates)
        return

    # ---------- create ----------
    if args.command == "create":
        if args.template not in templates:
            sys.exit(f"unknown template: {args.template}")
        final_name = args.name + (
            "_" + datetime.now().strftime("%Y-%m-%d") if args.datestamp else ""
        )
        root = Path(args.path).expanduser().resolve() / final_name
        if root.exists():
            sys.exit(f"directory '{root}' already exists.")

        build_structure(root, templates[args.template], dry=args.dry_run)
        if args.dry_run:
            return
        write_readme(root, final_name, args.template)
        write_gitignore(root)
        if args.git:
            init_git(root)
        if not args.no_open:
            open_folder(root)
        print(f"✔ project created at {root}")
        return


# ----------------------------------------------------------------------
if __name__ == "__main__":
    main()
