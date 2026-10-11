"""Read verified facts out of production code.

"Production" means committed code at a git ref (default HEAD), not the working tree, so
an engineer's unmerged work never becomes ground truth. `working_tree=True` is for repos
without git (and for demos). Each fact keeps a verbatim line of code as evidence.
"""
import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

from . import Fact, code_fact_id

MAX_FILES = 5000
MAX_BYTES = 200_000
EXCLUDE = re.compile(
    r"(^|/)(\.git|node_modules|vendor|dist|build|__pycache__|\.venv|venv|tests?|__tests__|"
    r"fixtures|examples?|migrations)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|\.(spec|test)\.[jt]sx?$"
)
CODE_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".tf", ".yml", ".yaml", ".json", ".txt", ".toml")


class Source:
    """Files of a repo at a git ref, or of its working tree."""

    def __init__(self, repo: Path, ref: str | None, working_tree: bool):
        self.repo = repo.resolve()
        self.working_tree = working_tree
        self.ref, self.commit, self.prefix, self.top = ref or "HEAD", "", "", self.repo
        if not working_tree:
            self.top = Path(self._git("rev-parse", "--show-toplevel", cwd=self.repo).strip())
            self.prefix = self.repo.relative_to(self.top).as_posix() if self.repo != self.top else ""
            self.commit = self._git("rev-parse", self.ref, cwd=self.top).strip()

    @staticmethod
    def _git(*args, cwd) -> str:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if p.returncode != 0:
            raise ValueError(f"git {' '.join(args)} failed: {p.stderr.strip()[:200]}")
        return p.stdout

    def paths(self) -> list[str]:
        if self.working_tree:
            found = [str(p.relative_to(self.repo)) for p in self.repo.rglob("*") if p.is_file()]
        else:
            listing = self._git("ls-tree", "-r", "--name-only", self.commit, "--", self.prefix or ".", cwd=self.top)
            found = [str(PurePosixPath(p).relative_to(self.prefix)) if self.prefix else p
                     for p in listing.splitlines()]
        return sorted(p for p in found if p.endswith(CODE_SUFFIXES) and not EXCLUDE.search(p))[:MAX_FILES]

    def read(self, path: str) -> str | None:
        if self.working_tree:
            data = (self.repo / path).read_bytes()
        else:
            full = f"{self.prefix}/{path}" if self.prefix else path
            p = subprocess.run(["git", "show", f"{self.commit}:{full}"], cwd=self.top, capture_output=True)
            data = p.stdout if p.returncode == 0 else b""
        if len(data) > MAX_BYTES:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None


def _line(text: str, lineno: int) -> str:
    return text.splitlines()[lineno - 1].strip()


def python_facts(path: str, text: str) -> list[dict]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    module_doc = (ast.get_docstring(tree) or "").split("\n")[0]
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            what = "class" if isinstance(node, ast.ClassDef) else "function"
            doc = (ast.get_docstring(node) or module_doc).split("\n")[0]
            out.append({
                "kind": "component", "subject": node.name, "line": node.lineno,
                "statement": f"{path} defines {what} {node.name}" + (f": {doc}" if doc else "."),
                "evidence": _line(text, node.lineno),
            })
    return out


JS_EXPORT = re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?(class|function|const|interface|type)\s+(\w+)", re.M)


def js_facts(path: str, text: str) -> list[dict]:
    out = []
    for m in JS_EXPORT.finditer(text):
        lineno = text.count("\n", 0, m.start()) + 1
        out.append({"kind": "component", "subject": m.group(2), "line": lineno,
                    "statement": f"{path} exports {m.group(1)} {m.group(2)}.",
                    "evidence": _line(text, lineno)})
    return out


TF_RESOURCE = re.compile(r'^\s*resource\s+"([\w-]+)"\s+"([\w-]+)"', re.M)
CFN_TYPE = re.compile(r"^\s*Type:\s*['\"]?(AWS::[\w:]+)", re.M)


def infra_facts(path: str, text: str) -> list[dict]:
    out = []
    for m in TF_RESOURCE.finditer(text):
        lineno = text.count("\n", 0, m.start()) + 1
        out.append({"kind": "infra", "subject": f"{m.group(1)} {m.group(2)}", "line": lineno,
                    "statement": f"{path} provisions {m.group(1)} \"{m.group(2)}\".",
                    "evidence": _line(text, lineno)})
    if path.endswith((".yml", ".yaml")):
        for m in CFN_TYPE.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            out.append({"kind": "infra", "subject": m.group(1).replace("::", " "), "line": lineno,
                        "statement": f"{path} provisions {m.group(1)}.", "evidence": _line(text, lineno)})
    return out


def dependency_facts(path: str, text: str) -> list[dict]:
    name = PurePosixPath(path).name
    deps: list[tuple[str, str]] = []  # (package, verbatim evidence)
    if re.fullmatch(r"requirements[\w.-]*\.txt", name):
        for line in text.splitlines():
            line = line.split("#")[0].strip()
            if m := re.match(r"([A-Za-z0-9_.-]+)", line):
                deps.append((m.group(1), line))
    elif name == "package.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        for section in ("dependencies", "peerDependencies"):
            for pkg, ver in (data.get(section) or {}).items():
                deps.append((pkg, f'"{pkg}": "{ver}"'))
    elif name == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []
        for spec in data.get("project", {}).get("dependencies", []):
            if m := re.match(r"([A-Za-z0-9_.-]+)", spec):
                deps.append((m.group(1), spec))
    return [{"kind": "dependency", "subject": pkg, "line": 0,
             "statement": f"Production code depends on {pkg} (declared in {path}).", "evidence": ev}
            for pkg, ev in deps]


def extract(path: str, text: str) -> list[dict]:
    if path.endswith(".py"):
        return python_facts(path, text)
    if path.endswith((".js", ".jsx", ".ts", ".tsx")):
        return js_facts(path, text)
    if path.endswith((".tf", ".yml", ".yaml")):
        return infra_facts(path, text)
    return dependency_facts(path, text)


def scan(repo: str | Path, ref: str | None = None, working_tree: bool = False, name: str | None = None) -> tuple[str, list[Fact]]:
    """Return (repo name, facts) for the production code of `repo`."""
    src = Source(Path(repo), ref, working_tree)
    repo_name = name or src.repo.name
    verified_by = (f"production-code:{repo_name}@working-tree" if working_tree
                   else f"production-code:{repo_name}@{src.ref}:{src.commit[:10]}")
    facts: list[Fact] = []
    for path in src.paths():
        text = src.read(path)
        if text is None:
            continue
        for item in extract(path, text):
            source = f"{path}:{item['line']}" if item["line"] else path
            facts.append(Fact(
                id=code_fact_id(repo_name, path, item["subject"]), kind=item["kind"],
                subject=item["subject"], statement=item["statement"], evidence=item["evidence"],
                source=source, source_type="code", repo=repo_name,
                ref="working-tree" if working_tree else src.ref, commit=src.commit,
                verified_by=verified_by,
            ))
    return repo_name, list({f.id: f for f in facts}.values())
