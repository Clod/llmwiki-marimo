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
    # 1. Clean input: return empty list immediately if there is no text
    if not content or not content.strip():
        return []

    # 2. Slice text into separate paragraphs, then break apart any single
    #    paragraph larger than chunk_size so it can't become one oversized chunk.
    paragraphs = _split_oversized(_split_paragraphs(content), chunk_size)

    # Stores tuples of (header_level, header_title) to track markdown outline depth
    header_stack: list[tuple[int, str]] = []

    # The final list of constructed Chunks we will return
    chunks: list[Chunk] = []

    # Temporary buffer of paragraphs forming the current chunk being built
    current_blocks: list[str] = []

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

            # Pop off any deeper/equal headings from the stack to maintain outline hierarchy
            header_stack = [(lv, t) for lv, t in header_stack if lv < level]
            # Push the new heading onto the stack
            header_stack.append((level, heading))

        # B. If adding this paragraph would exceed the maximum CHUNK_SIZE:
        #    we finalize, validate, and save the current chunk before continuing
        if current_tokens + para_tokens > chunk_size and current_blocks:
            # Combine current paragraphs with double-newlines
            chunk_str = "\n\n".join(current_blocks)

            # If the chunk is long enough to be useful (above minimum limits):
            if _estimate_tokens(chunk_str) >= MIN_CHUNK_TOKENS:
                # Build the hierarchy breadcrumb (e.g., "Overview > Definitions > Concept A")
                breadcrumb = " > ".join(t for _, t in header_stack)
                chunks.append(Chunk(
                    index=len(chunks),
                    content=chunk_str,
                    page=page,
                    start_char=current_start,
                    token_count=_estimate_tokens(chunk_str),
                    header_breadcrumb=breadcrumb,
                ))

            # C. Establish context overlap for the NEXT chunk.
            #    We pull trailing paragraphs from this chunk until we fill the 'overlap' budget.
            overlap_blocks, overlap_tokens = _get_overlap(current_blocks, overlap)
            current_blocks = overlap_blocks
            current_tokens = overlap_tokens

            # Recalculate where the new overlapping chunk starts in characters
            current_start = char_pos - sum(len(b) + 2 for b in overlap_blocks)

        # D. Add current paragraph block to our buffer
        current_blocks.append(para)
        current_tokens += para_tokens
        # Move our absolute character pointer (+2 accounts for the double-newlines '\n\n' separator)
        char_pos += len(para) + 2

    # 4. Finalize the remaining trailing paragraphs at the end of the file
    if current_blocks:
        chunk_str = "\n\n".join(current_blocks)
        if _estimate_tokens(chunk_str) >= MIN_CHUNK_TOKENS:
            breadcrumb = " > ".join(t for _, t in header_stack)
            chunks.append(Chunk(
                index=len(chunks),
                content=chunk_str,
                page=page,
                start_char=current_start,
                token_count=_estimate_tokens(chunk_str),
                header_breadcrumb=breadcrumb,
            ))

    return chunks


def chunk_pages(page_contents: list[tuple[int, str]]) -> list[Chunk]:
    """Chunk multiple pages, preserving page numbers.

    Args:
        page_contents: A list of (page_number, page_text) tuples.

    Returns:
        A combined list of Chunk objects indexed sequentially across pages.
    """
    all_chunks: list[Chunk] = []
    for page_num, content in page_contents:
        # Chunk the text of this specific page
        for c in chunk_text(content, page=page_num):
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
