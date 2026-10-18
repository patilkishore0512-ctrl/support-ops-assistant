"""
Knowledge-base parsing and chunking.

Parsing
  The FastAPI docs are MkDocs Markdown with some site-specific syntax. Before chunking we
  remove what carries no meaning for retrieval: heading anchors `{ #id }`, code-include
  directives `{* file.py *}`, tab markers, images and HTML tags. Admonitions (`/// tip`)
  keep their text with a short label ("Tip:").

Chunking (heading-aware)
  1. Split each document into sections at H1-H3 headings, so a chunk never mixes topics.
  2. Inside a section, pack whole paragraphs (code blocks are kept intact) up to
     `chunk_size` words, carrying the last `overlap` words of paragraphs into the next chunk.
  3. Each chunk stores metadata: source path, URL, title, section path, last-updated date.
  4. The text used for embedding/BM25 is prefixed with "title > section" so short chunks
     still carry their topic (contextual chunk headers).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,3})\s+(.*?)\s*$")
ANCHOR_RE = re.compile(r"\s*\{\s*#[^}]*\}\s*$")
INCLUDE_RE = re.compile(r"^\s*\{\*.*\*\}\s*$")
ADMONITION_OPEN_RE = re.compile(r"^/{3,4}\s*(\w+)(?:\s*\|\s*(.*))?\s*$")
ADMONITION_CLOSE_RE = re.compile(r"^/{3,4}\s*$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


@dataclass
class Chunk:
    chunk_id: str
    source: str          # e.g. fastapi/tutorial/cors.md
    url: str
    title: str           # H1 of the document
    section: str         # "H2 > H3" path, or "" for the intro
    text: str            # cleaned passage returned as a citation
    last_updated: str    # YYYY-MM-DD
    position: int        # order of the chunk in its document

    @property
    def search_text(self) -> str:
        header = self.title if not self.section else f"{self.title} > {self.section}"
        return f"{header}\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self)


def clean_markdown(raw: str) -> str:
    """Remove MkDocs-specific syntax while keeping all meaningful text."""
    out: list[str] = []
    in_code = False
    for line in raw.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        if INCLUDE_RE.match(line) or ADMONITION_CLOSE_RE.match(line.strip()):
            continue
        m = ADMONITION_OPEN_RE.match(line.strip())
        if m:
            kind, label = m.group(1), m.group(2)
            if kind == "tab":
                continue
            out.append(f"{kind.capitalize()}{f' ({label.strip()})' if label else ''}:")
            continue
        line = ANCHOR_RE.sub("", line)
        line = IMAGE_RE.sub("", line)
        line = LINK_RE.sub(r"\1", line)
        line = HTML_TAG_RE.sub("", line)
        out.append(line.rstrip())
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_sections(text: str) -> list[tuple[str, str, str]]:
    """Return (title, section_path, body) per section. Headings inside code blocks are ignored."""
    title = ""
    h2 = h3 = ""
    sections: list[tuple[str, str, str]] = []
    buf: list[str] = []
    in_code = False

    def flush():
        body = "\n".join(buf).strip()
        if body:
            path = " > ".join(p for p in (h2, h3) if p)
            sections.append((title, path, body))
        buf.clear()

    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING_RE.match(line)
        if m:
            flush()
            level, heading = len(m.group(1)), m.group(2).strip()
            if level == 1:
                title, h2, h3 = heading, "", ""
            elif level == 2:
                h2, h3 = heading, ""
            else:
                h3 = heading
            continue
        buf.append(line)
    flush()
    return sections


def split_paragraphs(body: str) -> list[str]:
    """Blank-line separated paragraphs; a fenced code block is always one paragraph."""
    paras, buf, in_code = [], [], False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if not line.strip() and not in_code:
            if buf:
                paras.append("\n".join(buf).strip())
                buf = []
            continue
        buf.append(line)
    if buf:
        paras.append("\n".join(buf).strip())
    return [p for p in paras if p]


def _words(text: str) -> int:
    return len(text.split())


def _split_long(paragraph: str, size: int) -> list[str]:
    """Split a paragraph longer than `size` words into word windows."""
    words = paragraph.split()
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)]


def pack_paragraphs(paragraphs: list[str], size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for para in paragraphs:
        pieces = _split_long(para, size) if _words(para) > size else [para]
        for piece in pieces:
            n = _words(piece)
            if current and count + n > size:
                chunks.append("\n\n".join(current))
                # carry trailing paragraphs up to `overlap` words into the next chunk
                carry, carried = [], 0
                for prev in reversed(current):
                    if carried + _words(prev) > overlap:
                        break
                    carry.insert(0, prev)
                    carried += _words(prev)
                current, count = carry, carried
            current.append(piece)
            count += n
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_document(source: str, raw: str, url: str, last_updated: str,
                   size: int = 180, overlap: int = 30) -> list[Chunk]:
    text = clean_markdown(raw)
    chunks: list[Chunk] = []
    position = 0
    for title, section, body in split_sections(text):
        for piece in pack_paragraphs(split_paragraphs(body), size, overlap):
            if _words(piece) < 5:
                continue
            cid = hashlib.sha1(f"{source}|{position}|{piece[:50]}".encode()).hexdigest()[:12]
            chunks.append(Chunk(cid, source, url, title, section, piece, last_updated, position))
            position += 1
    return chunks


def load_manifest(kb_dir: Path) -> dict[str, dict]:
    path = kb_dir / "manifest.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {d["path"]: d for d in data.get("documents", [])}


def load_corpus(kb_dir: Path, size: int = 180, overlap: int = 30) -> list[Chunk]:
    """Parse and chunk every Markdown file under kb_dir."""
    manifest = load_manifest(kb_dir)
    chunks: list[Chunk] = []
    for path in sorted(kb_dir.rglob("*.md")):
        source = path.relative_to(kb_dir).as_posix()
        meta = manifest.get(source, {})
        chunks.extend(chunk_document(
            source=source,
            raw=path.read_text(encoding="utf-8"),
            url=meta.get("url", ""),
            last_updated=meta.get("last_updated", ""),
            size=size,
            overlap=overlap,
        ))
    return chunks
