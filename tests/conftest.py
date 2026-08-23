import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SOURCE_ROOTS = (
    ROOT / "packages/core/src",
    ROOT / "packages/github/src",
    ROOT / "apps/qg/src",
)

for source_root in reversed(SOURCE_ROOTS):
    sys.path.insert(0, str(source_root))

if ROOT.name == "mutants":
    sys.path.insert(0, str(ROOT))
    for module_name in tuple(sys.modules):
        if module_name == "packages" or module_name.startswith("packages."):
            del sys.modules[module_name]
    importlib.invalidate_caches()
    MUTATED_MODULES = (
        ("quality_graph_core.result", "packages.core.src.quality_graph_core.result"),
        ("quality_graph_core.graph", "packages.core.src.quality_graph_core.graph"),
        ("quality_graph_core.policy", "packages.core.src.quality_graph_core.policy"),
        ("qg_github.compiler", "packages.github.src.qg_github.compiler"),
        ("qg_github.commands", "packages.github.src.qg_github.commands"),
    )
    for public_name, mutated_name in MUTATED_MODULES:
        sys.modules[public_name] = importlib.import_module(mutated_name)
