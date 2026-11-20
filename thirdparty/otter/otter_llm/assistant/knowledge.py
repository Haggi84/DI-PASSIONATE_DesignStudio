"""Search over OTTER's own documentation, for the assistant (retrieval part of RAG).

The documents are OTTER's debugging findings, the solvers' step-by-step change logs and the
user guides: tens of files, a few hundred sections. A BM25 keyword index finds the right
sections reliably at that size and needs no embedding model, so it works the same with the
server's local model and with Claude.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SOURCES = ("docs/findings/*.md", "docs/user-guide/*.md", "docs/reference/*.md", "docs/benchmark/*.md",
           "examples/*/*/CHANGES.md", "examples/*/*/README.md")
MAX_CHARS = 1800
WORD = re.compile(r"[a-z0-9]+(?:[._][a-z0-9]+)*")
STOP = set("the a an and or of to in on for is are be it this that with as by at from not no can if then "
           "than so its into was were has have had which when what how".split())


@dataclass
class Section:
    source: str  # path inside the OTTER repo
    title: str  # heading path, e.g. "openEMS findings > Step 5"
    text: str


def tokens(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def split_sections(source: str, markdown: str) -> list[Section]:
    """Sections by heading; long ones in paragraphs of at most MAX_CHARS."""
    sections, heading, lines = [], [], []

    def flush():
        body = "\n".join(lines).strip()
        if not body:
            return
        title = " > ".join(heading) or source
        chunk = ""
        for paragraph in re.split(r"\n\s*\n", body):
            if chunk and len(chunk) + len(paragraph) > MAX_CHARS:
                sections.append(Section(source, title, chunk.strip()))
                chunk = ""
            chunk += paragraph + "\n\n"
        if chunk.strip():
            sections.append(Section(source, title, chunk.strip()))

    for line in markdown.splitlines():
        match = re.match(r"^(#{1,4})\s+(.*)", line)
        if match:
            flush()
            lines = []
            level = len(match.group(1))
            heading = heading[:level - 1] + [match.group(2).strip()]
        elif not line.startswith("--8<--"):  # mkdocs include lines, the included files are indexed themselves
            lines.append(line)
    flush()
    return sections


class Knowledge:
    def __init__(self, sections: list[Section]):
        self.sections = sections
        self.docs = [Counter(tokens(s.title + " " + s.text)) for s in sections]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lengths) / max(len(self.lengths), 1)
        frequency = Counter(word for d in self.docs for word in d)
        n = len(self.docs)
        self.idf = {word: math.log(1 + (n - f + 0.5) / (f + 0.5)) for word, f in frequency.items()}

    @classmethod
    def from_repo(cls, otter_dir: Path) -> "Knowledge":
        sections = []
        for pattern in SOURCES:
            for path in sorted(otter_dir.glob(pattern)):
                sections += split_sections(str(path.relative_to(otter_dir)), path.read_text(errors="replace"))
        return cls(sections)

    def search(self, query: str, k: int = 3) -> list[Section]:
        """BM25 (k1 = 1.5, b = 0.75), best first; nothing when no word matches."""
        words = [w for w in tokens(query) if w in self.idf]
        scores = []
        for i, doc in enumerate(self.docs):
            score = 0.0
            for w in words:
                f = doc.get(w, 0)
                if f:
                    score += self.idf[w] * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * self.lengths[i] / self.avg))
            if score > 0:
                scores.append((score, i))
        return [self.sections[i] for _, i in sorted(scores, reverse=True)[:k]]


@lru_cache(maxsize=4)
def knowledge_for(otter_dir: str) -> Knowledge:
    return Knowledge.from_repo(Path(otter_dir))


def format_sections(sections: list[Section]) -> str:
    return "\n\n".join(f"[{s.source} — {s.title}]\n{s.text}" for s in sections)
