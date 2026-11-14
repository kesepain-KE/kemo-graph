"""Check the module boundaries established by the facade migration."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYERS = {"common": 0, "settings": 1, "observability": 1, "storage": 2,
          "documents": 3, "graph": 3, "rag": 3, "ingest": 4,
          "retrieval": 4, "maintenance": 5, "service": 6}
EXPORT_BUDGETS = {"core": 24, "core.common": 24, "core.settings": 20,
                  "core.storage": 16, "core.graph": 24, "core.rag": 16,
                  "core.ingest": 16, "core.documents": 20,
                  "core.retrieval": 12, "core.observability": 12,
                  "core.maintenance": 8, "core.service": 20}
STAGE = 3


def import_edges(root: Path = ROOT):
    for directory in ("core", "api", "provider"):
        for path in sorted((root / directory).rglob("*.py")):
            if "sqlite_simple" in path.parts:
                continue
            relative = path.relative_to(root).with_suffix("")
            parts = relative.parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            package = module if parts[-1] == "__init__" else module.rpartition(".")[0]
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        yield module, alias.name, path, node.lineno
                elif isinstance(node, ast.ImportFrom):
                    target = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module or ""
                    if node.module is None:
                        for alias in node.names:
                            yield module, target + "." + alias.name, path, node.lineno
                    else:
                        yield module, target, path, node.lineno


def check_architecture(root: Path = ROOT, stage: int = STAGE) -> list[str]:
    problems = []
    for source, target, path, line in import_edges(root):
        label = f"{path.relative_to(root)}:{line}: {source} -> {target}"
        source_parts, target_parts = source.split("."), target.split(".")
        if source_parts[0] == "core" and target_parts[0] == "api":
            problems.append(label + " (core must not depend on API)")
        if source.startswith("core.common") and target_parts[0] in {"core", "api", "provider"} and not target.startswith("core.common"):
            problems.append(label + " (common must be independent)")
        if stage >= 3 and source_parts[0] == "core" and target.startswith("provider.tools"):
            problems.append(label + " (reverse tool dependency)")
        if len(source_parts) > 1 and len(target_parts) > 1 and source_parts[0] == target_parts[0] == "core":
            left, right = source_parts[1], target_parts[1]
            if left in LAYERS and right in LAYERS and LAYERS[left] < LAYERS[right]:
                problems.append(label + " (upward dependency)")
    for module, budget in EXPORT_BUDGETS.items():
        path = root.joinpath(*module.split("."), "__init__.py")
        if not path.exists():
            if stage >= 10:
                problems.append(f"Missing facade: {module}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                if len(ast.literal_eval(node.value)) > budget:
                    problems.append(f"{module}: export budget exceeded ({budget})")
    facade = root / "core/service/facade.py"
    if stage >= 4 and facade.exists():
        for node in ast.walk(ast.parse(facade.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                body = node.body[1:] if ast.get_docstring(node) else node.body
                if len(body) > 3:
                    problems.append(f"{facade.name}:{node.lineno}: {node.name} exceeds three statements")
    return problems


def main() -> int:
    problems = check_architecture()
    for problem in problems:
        print(problem)
    print(f"Architecture: {len(problems)} violation(s), stage {STAGE}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
