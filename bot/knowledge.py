from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".txt"}


@dataclass(frozen=True)
class KnowledgeBase:
    text: str
    files: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.text)


def load_knowledge(directory: str) -> KnowledgeBase:
    """Склеивает все .md/.txt из папки в один текст. Порядок файлов фиксированный, чтобы не ломать кэш промпта."""
    root = Path(directory)
    if not root.is_dir():
        raise RuntimeError(f"Папка базы знаний не найдена: {root.resolve()}")

    paths = sorted(p for p in root.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)
    if not paths:
        raise RuntimeError(f"В папке {root.resolve()} нет файлов .md или .txt")

    parts = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        content = path.read_text(encoding="utf-8").strip()
        parts.append(f'<document name="{name}">\n{content}\n</document>')
    return KnowledgeBase(text="\n\n".join(parts), files=tuple(p.relative_to(root).as_posix() for p in paths))
