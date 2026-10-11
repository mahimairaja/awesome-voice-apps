"""Check a demo folder against CONTRIBUTING.md before you open a pull request.

    python3 scripts/check_demo.py demos/your-app
    python3 scripts/check_demo.py demos/your-app --json   # for coding agents

Needs only Python 3.11+ and uv (for Ruff). It never calls a provider and never
reads your .env. Exit code 0 means every required check passed.
"""

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("agent.py", "pyproject.toml", ".env.example", "README.md")
RUFF = "ruff==0.15.16"
# Values that look like real credentials: long tokens, or known key prefixes.
SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|[A-Za-z0-9_-]{32,})")


def check(folder: Path) -> list[dict]:
    results = []

    def add(name: str, ok: bool, fix: str = "", required: bool = True) -> None:
        results.append({"check": name, "ok": ok, "required": required, "fix": "" if ok else fix})

    if not folder.is_dir() or folder.parent.resolve() != (ROOT / "demos").resolve():
        add(
            "folder",
            False,
            "Put your example in demos/<your-app>, copied from templates/livekit-base.",
        )
        return results
    slug = folder.name
    add(
        "slug",
        bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug)),
        "Use a lowercase, hyphenated folder name, like demos/pharmacy-refill.",
    )
    for name in REQUIRED:
        add(
            f"has {name}",
            (folder / name).is_file(),
            f"Add {name} (copy it from templates/livekit-base).",
        )

    pyproject = folder / "pyproject.toml"
    if pyproject.is_file():
        try:
            project = tomllib.loads(pyproject.read_text()).get("project", {})
            add(
                "project name",
                project.get("name") not in (None, "livekit-base"),
                f'Rename the project in pyproject.toml, e.g. name = "{slug}".',
            )
            add(
                "python 3.11+",
                ">=3.11" in str(project.get("requires-python", "")).replace(" ", ""),
                'Set requires-python = ">=3.11" in pyproject.toml.',
            )
        except tomllib.TOMLDecodeError as problem:
            add("pyproject.toml parses", False, f"Fix pyproject.toml: {problem}")

    example = folder / ".env.example"
    if example.is_file():
        # Settings may keep a default; credentials stay empty or a "your-..." placeholder.
        filled = []
        for line in example.read_text().splitlines():
            name, _, value = line.partition("=")
            value = value.strip().strip("'\"")
            credential = re.search(r"KEY|SECRET|TOKEN|PASSWORD", name, re.I)
            if not line.lstrip().startswith("#") and credential and value:
                if not value.startswith("your"):
                    filled.append(name.strip())
        add(
            ".env.example has no credentials",
            not filled,
            f"Leave these values empty: {', '.join(filled)}.",
        )
    add(
        "no .env committed",
        not (folder / ".env").exists(),
        "Delete demos/<app>/.env; never commit it.",
    )

    leaked = []
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".toml", ".md", ".json", ".txt", ".yaml"}:
            for line in path.read_text(errors="ignore").splitlines():
                if re.search(
                    r"(api[_-]?key|secret|token)\s*[=:]\s*['\"]", line, re.I
                ) and SECRET.search(line):
                    leaked.append(str(path.relative_to(ROOT)))
                    break
    add(
        "no secrets in files",
        not leaked,
        f"Remove credentials from {', '.join(leaked)}; read them from .env.",
    )

    readme = folder / "README.md"
    if readme.is_file():
        text = readme.read_text()
        add(
            "README says how to run it",
            "uv run" in text and len(text.split()) >= 30,
            "Say what the example does, which credentials it needs, and the uv commands to run it.",
        )

    models = folder / "models.toml"
    if models.is_file():
        sys.path.insert(0, str(ROOT / "playground"))
        import model_menu

        try:
            model_menu.read_priority(models)
            add("models.toml", True, required=False)
        except (ValueError, tomllib.TOMLDecodeError) as problem:
            add("models.toml", False, str(problem))

    agent = folder / "agent.py"
    if agent.is_file():
        compiled = subprocess.run(
            [sys.executable, "-m", "py_compile", str(agent)], capture_output=True, text=True
        )
        add("agent.py compiles", compiled.returncode == 0, compiled.stderr.strip()[-400:])
    for args, name in (
        (["check", str(folder)], "ruff check"),
        (["format", "--check", str(folder)], "ruff format"),
    ):
        try:
            ran = subprocess.run(["uvx", RUFF, *args], capture_output=True, text=True, cwd=ROOT)
            fix = f"Run `uvx {RUFF} {' '.join(args).replace(' --check', '')}` and fix what it reports."
            add(name, ran.returncode == 0, fix)
        except FileNotFoundError:
            add(
                name,
                False,
                "Install uv (https://docs.astral.sh/uv/) so Ruff can run.",
                required=False,
            )
    return results


def main(argv: list[str]) -> int:
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 1:
        print(__doc__)
        return 2
    results = check(
        (ROOT / paths[0]).resolve() if not Path(paths[0]).is_absolute() else Path(paths[0])
    )
    ready = all(r["ok"] for r in results if r["required"])
    if "--json" in argv:
        print(json.dumps({"ready": ready, "results": results}, indent=2))
    else:
        for r in results:
            mark = "ok  " if r["ok"] else ("FAIL" if r["required"] else "warn")
            print(f"{mark}  {r['check']}" + (f"\n      {r['fix']}" if r["fix"] else ""))
        print(
            "\nReady for a pull request." if ready else "\nFix the FAIL lines, then run this again."
        )
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
