"""Pure text chunking — no I/O, no database dependency.

Keeps chunk_text / chunk_pages / Chunk which are all we need for parsing.

PURPOSE FOR BEGINNERS:
In AI searching and Retrieval-Augmented Generation (RAG), you cannot easily feed
an entire 100-page book to a search index or to an LLM context window. It's too slow,
expensive, and inaccurate.

Instead, we "chunk" the text—meaning we split the document into small, readable,
overlapping paragraphs (chunks) of roughly 512 words/tokens. The small "overlap"
(e.g., 128 tokens) ensures that if a vital sentence is sliced in half by a boundary,
the complete meaning is preserved on both sides of the split.
"""

# Import regular expressions to find punctuation and markdown headings
import re
# Import standard logging library
import logging
# Import dataclass to create clean structured chunk data containers
from dataclasses import dataclass

# Set up logging for this module
logger = logging.getLogger(__name__)

# CONFIGURATION TUNING CONSTANTS:
# Target size of each text segment (in estimated tokens/words)
CHUNK_SIZE = 512
# Amount of text to repeat between adjacent chunks to preserve transition context
CHUNK_OVERLAP = 128
# Minimum size required to save a chunk (throws away tiny fragments like isolated numbers or footers)
MIN_CHUNK_TOKENS = 32

# Matches punctuation boundaries followed by whitespace to locate sentence transitions
SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')
# Matches markdown headings (e.g. "## Introduction" -> Level 2, Title "Introduction")
HEADER_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a given text string.

    A standard rule of thumb for English/Spanish text is that 1 token is roughly
    equivalent to 4 characters. We divide length by 4 and ensure at least 1 token is counted.
    """
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    """Data container representing a single semantic slice of text."""
    # Incremental position number of the chunk (0, 1, 2, ...)
    index: int
    # The actual plain text inside this chunk
    content: str
    # The source page number where this chunk is located (optional)
    page: int | None
    # Character position in the original text where this chunk starts
    start_char: int
    # Estimated token length of the text inside the chunk
    token_count: int
    # Path of markdown headings leading to this section (e.g., "Intro > Definitions")
    header_breadcrumb: str = ""


@dataclass(frozen=True)
class Outline:
    """Where in a document's heading structure chunking has got to.

    A page break is not a section break: a PDF section opened on page 3 runs on
    into page 4, and chunking a page at a time would forget that. This is the
    state handed from one page to the next so it doesn't.
    """
    # The heading path in force, as (level, title) from outermost to innermost
    headers: tuple[tuple[int, str], ...] = ()
    # The last breadcrumb written out, used as the fallback in _settled_or
    last_breadcrumb: str = ""

    @property
    def breadcrumb(self) -> str:
        """The heading path rendered the way it is stored on a chunk."""
        return " > ".join(t for _, t in self.headers)

    def with_heading(self, level: int, title: str) -> "Outline":
        """The outline after a heading of the given level opens a section.

        Headings at the same level or deeper are closed by it, which is what
        keeps the path an outline rather than an ever-growing list.
        """
        kept = tuple((lv, t) for lv, t in self.headers if lv < level)
        return Outline(kept + ((level, title),), self.last_breadcrumb)

    def with_last_breadcrumb(self, breadcrumb: str) -> "Outline":
        """The outline after a chunk has been emitted under `breadcrumb`."""
        return Outline(self.headers, breadcrumb)


def _settled_or(breadcrumb: str, settled: bool, fallback: str) -> str:
    """The breadcrumb to write out for a chunk about to be emitted.

    An unsettled breadcrumb means no body text ever arrived under the heading it
    names — the chunk merely ends on that heading, with the section's content
    falling beyond it. Naming the chunk after it would point a citation at a
    passage the chunk does not contain, so the previous chunk's heading, which
    the text actually sits under, is used instead.
    """
    return breadcrumb if settled else fallback


def chunk_text(
    content: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    page: int | None = None,
    start_char_offset: int = 0,
) -> list[Chunk]:
    """Chunk a text string into overlapping segments with header tracking.

    Args:
        content: The raw text string to chunk.
        chunk_size: Target token capacity of each chunk.
        overlap: Target token count to overlap between chunks.
        page: Optional page identifier.
        start_char_offset: The starting character position offset.

    Returns:
        A list of constructed Chunk objects.
    """
    chunks, _ = chunk_text_continuing(
        content, Outline(), chunk_size=chunk_size, overlap=overlap,
        page=page, start_char_offset=start_char_offset,
    )
    return chunks


def chunk_text_continuing(
    content: str,
    outline: Outline,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    page: int | None = None,
    start_char_offset: int = 0,
) -> tuple[list[Chunk], Outline]:
    """Chunk one more stretch of a document, resuming from `outline`.

    Same as chunk_text, except that it starts inside whatever section the
    previous stretch ended in and reports where this one ended, so a document
    split into pages can be chunked page by page without losing its outline.

    Args:
        content: The raw text string to chunk.
        outline: Heading state left by the preceding stretch of the document.
        chunk_size: Target token capacity of each chunk.
        overlap: Target token count to overlap between chunks.
        page: Optional page identifier.
        start_char_offset: The starting character position offset.

    Returns:
        The chunks of this stretch, and the outline the next one resumes from.
    """
    # 1. Clean input: return immediately if there is no text, leaving the
    #    incoming outline untouched for whatever follows.
    if not content or not content.strip():
        return [], outline

    # 2. Slice text into separate paragraphs, then break apart any single
    #    paragraph larger than chunk_size so it can't become one oversized chunk.
    paragraphs = _split_oversized(_split_paragraphs(content), chunk_size)

    # The final list of constructed Chunks we will return
    chunks: list[Chunk] = []

    # Temporary buffer of paragraphs forming the current chunk being built
    current_blocks: list[str] = []

    # The heading path this chunk belongs to. Captured while the chunk is still
    # collecting its opening headings and then frozen, so the breadcrumb names
    # the section the chunk holds text FROM — not a later heading it happens to
    # end on, and not the section that starts right after it. Text resuming
    # mid-section starts out under the section it is resuming inside.
    current_breadcrumb = outline.breadcrumb

    # Whether the breadcrumb above has settled. It settles once the chunk holds
    # body text that sits under some heading. Text arriving while no heading has
    # been seen at all is preamble, not body — a wiki page opens with a YAML
    # frontmatter block before its title — and must not settle anything, or the
    # title that follows it would never be recorded.
    current_settled = False

    # Cumulative token counter for the current chunk
    current_tokens = 0

    # The character start index of the current chunk
    current_start = start_char_offset

    # Absolute character position pointer in the original file
    char_pos = start_char_offset

    # 3. Process each paragraph one by one
    for para in paragraphs:
        para_tokens = _estimate_tokens(para)

        # A. Check if this paragraph is a Markdown heading (e.g., "### Sub-section")
        header_match = HEADER_RE.match(para)
        if header_match:
            level = len(header_match.group(1)) # Number of hashes determines level (e.g., '###' is level 3)
            heading = header_match.group(2).strip()

            # Open the new section, closing any at the same level or deeper
            outline = outline.with_heading(level, heading)

            # A chunk that has not settled is still being titled, so this
            # heading belongs to it. Once settled, a later heading must not
            # overwrite it: that heading introduces the NEXT chunk, not this one.
            if not current_settled:
                current_breadcrumb = outline.breadcrumb

            # A heading is only a heading up to its own end of line. Generated
            # wiki pages put the section text on the very next line with no
            # blank line between, so this block usually carries body text too.
            block_has_body = bool(para[header_match.end():].strip())
        else:
            block_has_body = True

        # B. If adding this paragraph would exceed the maximum CHUNK_SIZE:
        #    we finalize, validate, and save the current chunk before continuing
        if current_tokens + para_tokens > chunk_size and current_blocks:
            # Combine current paragraphs with double-newlines
            chunk_str = "\n\n".join(current_blocks)

            # If the chunk is long enough to be useful (above minimum limits):
            emitted_breadcrumb = _settled_or(current_breadcrumb, current_settled,
                                             outline.last_breadcrumb)
            if _estimate_tokens(chunk_str) >= MIN_CHUNK_TOKENS:
                chunks.append(Chunk(
                    index=len(chunks),
                    content=chunk_str,
                    page=page,
                    start_char=current_start,
                    token_count=_estimate_tokens(chunk_str),
                    header_breadcrumb=emitted_breadcrumb,
                ))
            outline = outline.with_last_breadcrumb(emitted_breadcrumb)

            # C. Establish context overlap for the NEXT chunk.
            #    We pull trailing paragraphs from this chunk until we fill the 'overlap' budget.
            overlap_blocks, overlap_tokens = _get_overlap(current_blocks, overlap)
            current_blocks = overlap_blocks
            current_tokens = overlap_tokens

            # The chunk starting here sits under whatever heading is in force
            # now — including the one that just triggered this flush — and is
            # open to being titled further until its own body text begins.
            current_breadcrumb = outline.breadcrumb
            current_settled = False

            # Recalculate where the new overlapping chunk starts in characters
            current_start = char_pos - sum(len(b) + 2 for b in overlap_blocks)

        # D. Add current paragraph block to our buffer
        current_blocks.append(para)
        current_tokens += para_tokens
        # Body text sitting under a heading closes the chunk's title
        if block_has_body and outline.headers:
            current_settled = True
        # Move our absolute character pointer (+2 accounts for the double-newlines '\n\n' separator)
        char_pos += len(para) + 2

    # 4. Finalize the remaining trailing paragraphs at the end of the file
    if current_blocks:
        chunk_str = "\n\n".join(current_blocks)
        emitted_breadcrumb = _settled_or(current_breadcrumb, current_settled,
                                         outline.last_breadcrumb)
        if _estimate_tokens(chunk_str) >= MIN_CHUNK_TOKENS:
            chunks.append(Chunk(
                index=len(chunks),
                content=chunk_str,
                page=page,
                start_char=current_start,
                token_count=_estimate_tokens(chunk_str),
                header_breadcrumb=emitted_breadcrumb,
            ))
        outline = outline.with_last_breadcrumb(emitted_breadcrumb)

    return chunks, outline


def chunk_pages(page_contents: list[tuple[int, str]]) -> list[Chunk]:
    """Chunk multiple pages, preserving page numbers.

    Args:
        page_contents: A list of (page_number, page_text) tuples.

    Returns:
        A combined list of Chunk objects indexed sequentially across pages.
    """
    all_chunks: list[Chunk] = []
    # The heading structure runs through the document, not through each page, so
    # it is handed on from one page to the next
    outline = Outline()
    for page_num, content in page_contents:
        # Chunk the text of this specific page, resuming where the last left off
        page_chunks, outline = chunk_text_continuing(content, outline, page=page_num)
        for c in page_chunks:
            # Recalculate its global index across all pages combined
            c.index = len(all_chunks)
            all_chunks.append(c)
    return all_chunks


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs by looking for double-newlines (blank lines)."""
    parts = re.split(r'\n\s*\n', text)
    return [p.strip() for p in parts if p.strip()]


def _split_oversized(paragraphs: list[str], chunk_size: int) -> list[str]:
    """Break any paragraph larger than chunk_size into sentence-packed pieces.

    A paragraph with no blank lines can exceed chunk_size on its own; without this
    it would land in a single oversized chunk. Each returned piece is <= chunk_size
    tokens where the sentence structure allows; a lone sentence longer than
    chunk_size is hard-split on a character budget as a last resort.
    """
    out: list[str] = []
    for para in paragraphs:
        if _estimate_tokens(para) <= chunk_size:
            out.append(para)
            continue
        out.extend(_pack_sentences(para, chunk_size))
    return out


def _pack_sentences(text: str, chunk_size: int) -> list[str]:
    """Greedily pack sentences into pieces no larger than chunk_size tokens."""
    char_budget = chunk_size * 4  # _estimate_tokens uses ~4 chars/token
    pieces: list[str] = []
    buf: list[str] = []
    buf_tokens = 0

    def _flush() -> None:
        nonlocal buf, buf_tokens
        if buf:
            pieces.append(" ".join(buf))
            buf, buf_tokens = [], 0

    for sentence in SENTENCE_RE.split(text):
        if not sentence:
            continue
        sent_tokens = _estimate_tokens(sentence)
        # A single sentence over budget: flush, then hard-split it by characters.
        if sent_tokens > chunk_size:
            _flush()
            for i in range(0, len(sentence), char_budget):
                pieces.append(sentence[i:i + char_budget])
            continue
        if buf_tokens + sent_tokens > chunk_size and buf:
            _flush()
        buf.append(sentence)
        buf_tokens += sent_tokens

    _flush()
    return pieces


def _get_overlap(blocks: list[str], target_tokens: int) -> tuple[list[str], int]:
    """Iterates backward through paragraphs to fill the overlap token budget.

    This ensures we pull complete, clean paragraphs for the overlap rather
    than slicing a paragraph directly in half.
    """
    result: list[str] = []
    tokens = 0
    for block in reversed(blocks):
        block_tokens = _estimate_tokens(block)
        # Stop once we have reached the overlap target
        if tokens + block_tokens > target_tokens:
            break
        result.insert(0, block)
        tokens += block_tokens
    return result, tokens
