import importlib
from pathlib import Path
from typing import Optional

EXTENSION_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".swift": "swift",
    ".cs": "csharp",
}

LANGUAGE_MODULES = {
    "python":     "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "tsx":        "tree_sitter_typescript",
    "go":         "tree_sitter_go",
    "rust":       "tree_sitter_rust",
    "java":       "tree_sitter_java",
    "kotlin":     "tree_sitter_kotlin",
    "c":          "tree_sitter_c",
    "cpp":        "tree_sitter_cpp",
    "ruby":       "tree_sitter_ruby",
    "swift":      "tree_sitter_swift",
    "csharp":     "tree_sitter_c_sharp",
}

# Modules that don't expose a generic language() - map to their actual function name.
_LANGUAGE_FN = {
    "typescript": "language_typescript",
    "tsx":        "language_tsx",
}


def detect_language(filepath: str) -> Optional[str]:
    ext = Path(filepath).suffix.lower()
    return EXTENSION_MAP.get(ext)


def get_ts_language(lang_name: str):
    module_name = LANGUAGE_MODULES.get(lang_name)
    if not module_name:
        return None
    try:
        mod = importlib.import_module(module_name)
        from tree_sitter import Language
        fn_name = _LANGUAGE_FN.get(lang_name, "language")
        return Language(getattr(mod, fn_name)())
    except (ImportError, AttributeError, Exception):
        return None
