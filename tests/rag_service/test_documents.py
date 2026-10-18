from rag_service.documents import (
    chunk_document,
    clean_markdown,
    load_corpus,
    pack_paragraphs,
    split_paragraphs,
    split_sections,
)


def test_clean_markdown_removes_site_syntax_and_keeps_text():
    raw = """# Title { #title }

Intro with a [link](https://x.test) and ![img](a.png) and <abbr title="x">ABBR</abbr>.

{* ../../docs_src/app.py hl[1] *}

/// tip | Pro tip

Keep this text.

///

//// tab | Python 3.10+
////
"""
    out = clean_markdown(raw)
    assert "{ #title }" not in out and "{*" not in out and "///" not in out
    assert "Intro with a link and" in out and "ABBR" in out and "a.png" not in out
    assert "Tip (Pro tip):" in out and "Keep this text." in out


def test_split_sections_tracks_heading_path_and_ignores_code_headings():
    text = "# Doc\n\nintro\n\n## A\n\nalpha\n\n### A1\n\n```python\n# not a heading\n```\n\n## B\n\nbeta"
    sections = split_sections(text)
    assert [(t, s) for t, s, _ in sections] == [("Doc", ""), ("Doc", "A"), ("Doc", "A > A1"), ("Doc", "B")]
    assert "# not a heading" in sections[2][2]


def test_code_block_stays_one_paragraph():
    paras = split_paragraphs("text\n\n```\nline1\n\nline2\n```\n\nafter")
    assert paras == ["text", "```\nline1\n\nline2\n```", "after"]


def test_pack_paragraphs_respects_size_and_overlap():
    paras = [" ".join(["w"] * 40) for _ in range(5)]  # 5 x 40 words
    chunks = pack_paragraphs(paras, size=100, overlap=40)
    assert all(len(c.split()) <= 100 for c in chunks)
    assert len(chunks) >= 3
    # overlap: the last paragraph of a chunk starts the next one
    assert chunks[0].split("\n\n")[-1] == chunks[1].split("\n\n")[0]


def test_long_paragraph_is_split():
    chunks = pack_paragraphs([" ".join(["w"] * 450)], size=200, overlap=0)
    assert [len(c.split()) for c in chunks] == [200, 200, 50]


def test_chunk_metadata(kb_dir):
    chunks = load_corpus(kb_dir)
    cors = [c for c in chunks if c.source == "docs/tutorial/cors.md"]
    assert cors and cors[0].title == "CORS (Cross-Origin Resource Sharing)"
    assert any(c.section == "Use `CORSMiddleware`" for c in cors)
    assert all(c.url.startswith("https://example.test/") and c.last_updated for c in chunks)
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert cors[1].search_text.startswith("CORS (Cross-Origin Resource Sharing) > Use `CORSMiddleware`")


def test_tiny_fragments_are_dropped():
    assert chunk_document("x.md", "# T\n\n## A\n\nok\n\n## B\n\nthis section has enough words in it", "", "") [0].section == "B"
