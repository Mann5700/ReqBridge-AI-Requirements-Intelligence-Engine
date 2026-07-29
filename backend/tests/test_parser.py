"""Tests for the document parser module."""

import tempfile
from pathlib import Path

from backend.app.ingest.parser import DocumentParser


def test_parse_text_file():
    """Test plain text parsing with paragraph-based chunking."""
    parser = DocumentParser()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("This is the first paragraph with enough content to be a chunk.\n\n")
        f.write("This is the second paragraph with different content for testing.\n\n")
        f.write("Short.\n\n")  # Too short, should be filtered
        f.write("This is the third paragraph that should appear as a separate chunk.\n")
        f.flush()
        path = f.name

    chunks = parser.parse(path, "text")

    # Should have at least 2 chunks (short paragraph filtered out)
    assert len(chunks) >= 2
    assert all("content" in c for c in chunks)
    assert all("id" in c for c in chunks)
    assert all(c["chunk_type"] == "paragraph" for c in chunks)

    # Cleanup
    Path(path).unlink()


def test_parse_nonexistent_file():
    """Test error handling for missing files."""
    parser = DocumentParser()
    try:
        parser.parse("/nonexistent/file.txt", "text")
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass


def test_unsupported_file_type():
    """Test error handling for unsupported file types."""
    parser = DocumentParser()
    try:
        parser.parse("somefile.xyz", "xyz")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
