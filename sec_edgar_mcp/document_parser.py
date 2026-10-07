"""
SEC EDGAR document parser for handling large filing content with chunking strategies.
"""

import re
from typing import List, Dict, Optional, Any, Union
from bs4 import BeautifulSoup

from .utils.sec_http import sec_get_text


class FilingSection:
    """Represents a section of a SEC filing document."""

    def __init__(self, name: str, content: str, section_type: str = "unknown"):
        self.name = name
        self.content = content
        self.section_type = section_type
        self.word_count = len(content.split())
        self.char_count = len(content)


class DocumentChunk:
    """Represents a chunk of document content."""

    def __init__(self, content: str, section_name: str, chunk_index: int, metadata: Optional[Dict[str, Any]] = None):
        self.content = content
        self.section_name = section_name
        self.chunk_index = chunk_index
        self.metadata = metadata or {}
        self.word_count = len(content.split())
        self.char_count = len(content)


class SECDocumentParser:
    """Parser for SEC EDGAR filing documents with chunking capabilities."""

    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self.base_url = "https://www.sec.gov/Archives/edgar/data"

        # Common 10-K section patterns
        self.section_patterns = {
            "item_1": r"(?i)item\s+1[^\w].*?business",
            "item_1a": r"(?i)item\s+1a[^\w].*?risk\s+factors",
            "item_2": r"(?i)item\s+2[^\w].*?properties",
            "item_3": r"(?i)item\s+3[^\w].*?legal\s+proceedings",
            "item_4": r"(?i)item\s+4[^\w].*?mine\s+safety",
            "item_5": r"(?i)item\s+5[^\w].*?market\s+for",
            "item_6": r"(?i)item\s+6[^\w].*?selected\s+financial",
            "item_7": r"(?i)item\s+7[^\w].*?management.s\s+discussion",
            "item_7a": r"(?i)item\s+7a[^\w].*?quantitative\s+and\s+qualitative",
            "item_8": r"(?i)item\s+8[^\w].*?financial\s+statements",
            "item_9": r"(?i)item\s+9[^\w].*?controls\s+and\s+procedures",
            "item_9a": r"(?i)item\s+9a[^\w].*?controls\s+and\s+procedures",
            "item_9b": r"(?i)item\s+9b[^\w].*?other\s+information",
            "item_10": r"(?i)item\s+10[^\w].*?directors",
            "item_11": r"(?i)item\s+11[^\w].*?executive\s+compensation",
            "item_12": r"(?i)item\s+12[^\w].*?security\s+ownership",
            "item_13": r"(?i)item\s+13[^\w].*?certain\s+relationships",
            "item_14": r"(?i)item\s+14[^\w].*?principal\s+accountant",
            "item_15": r"(?i)item\s+15[^\w].*?exhibits",
        }

    def fetch_document(self, cik: str, accession_number: str, document_name: Optional[str] = None) -> str:
        """Fetch a SEC filing document from EDGAR."""
        # Clean accession number (remove hyphens)
        clean_accession = accession_number.replace("-", "")

        # If no document name provided, use the reliable .txt format
        if document_name is None:
            document_name = f"{accession_number}.txt"

        # Build URL
        url = f"{self.base_url}/{cik}/{clean_accession}/{document_name}"

        try:
            return sec_get_text(url)
        except Exception as e:
            raise Exception(f"Failed to fetch document: {str(e)}")

    def fetch_filing_txt(self, cik: str, accession_number: str) -> str:
        """Fetch the complete SEC filing in .txt format (most reliable)."""
        return self.fetch_document(cik, accession_number, f"{accession_number}.txt")

    def clean_html_content(self, html_content: str) -> str:
        """Clean HTML content and extract readable text."""
        # Parse HTML
        soup = BeautifulSoup(html_content, "html.parser")

        # Remove script and style elements
        for script in soup(["script", "style", "meta", "link"]):
            script.decompose()

        # Remove XBRL tags (common in modern filings)
        for xbrl_tag in soup.find_all(re.compile(r"^(ix:|xbrli:|dei:|us-gaap:)")):
            xbrl_tag.decompose()

        # Get text content
        text = soup.get_text()

        # Clean up whitespace
        text = re.sub(r"\n\s*\n", "\n\n", text)  # Multiple newlines to double
        text = re.sub(r" +", " ", text)  # Multiple spaces to single
        text = text.strip()

        return text

    def clean_txt_content(self, txt_content: str) -> str:
        """Clean .txt SEC filing content and extract readable text."""
        # The .txt format is already plain text but contains multiple documents
        # and lots of metadata. We need to extract the main filing content.

        lines = txt_content.split("\n")
        cleaned_lines = []
        in_document = False
        document_count = 0

        for line in lines:
            line = line.strip()

            # Skip empty lines at the start
            if not line and not in_document:
                continue

            # Look for document boundaries
            if line.startswith("<DOCUMENT>"):
                in_document = True
                document_count += 1
                continue
            elif line.startswith("</DOCUMENT>"):
                in_document = False
                # Add separator between documents
                if document_count > 1:
                    cleaned_lines.append("\n" + "=" * 80 + "\n")
                continue

            # Skip metadata lines
            if line.startswith("<") and line.endswith(">"):
                continue

            # Only include content when we're inside a document
            if in_document:
                cleaned_lines.append(line)

        # Join and clean up
        text = "\n".join(cleaned_lines)

        # Clean up excessive whitespace
        text = re.sub(r"\n\s*\n\s*\n", "\n\n", text)  # Multiple newlines to double
        text = re.sub(r" +", " ", text)  # Multiple spaces to single
        text = text.strip()

        return text

    def extract_main_document_from_txt(self, txt_content: str) -> str:
        """Extract the main document (usually the first one) from .txt filing."""
        lines = txt_content.split("\n")
        documents: List[Dict[str, Union[str, int]]] = []
        current_doc_lines: List[str] = []
        current_doc_type: Optional[str] = None
        current_doc_filename: Optional[str] = None
        in_document = False

        for line in lines:
            line_stripped = line.strip()

            # Look for document start
            if line_stripped.startswith("<DOCUMENT>"):
                in_document = True
                current_doc_lines = []
                current_doc_type = None
                current_doc_filename = None
                continue
            elif line_stripped.startswith("</DOCUMENT>"):
                # End of document - save it
                if current_doc_lines and current_doc_type:
                    documents.append(
                        {
                            "type": current_doc_type or "UNKNOWN",
                            "filename": current_doc_filename or "unknown",
                            "content": "\n".join(current_doc_lines),
                            "line_count": len([line for line in current_doc_lines if line.strip()]),
                        }
                    )
                in_document = False
                current_doc_lines = []
                current_doc_type = None
                current_doc_filename = None
                continue

            # Skip non-document content
            if not in_document:
                continue

            # Look for document metadata
            if line_stripped.startswith("<TYPE>"):
                current_doc_type = line_stripped.replace("<TYPE>", "").strip()
                continue
            elif line_stripped.startswith("<FILENAME>"):
                current_doc_filename = line_stripped.replace("<FILENAME>", "").strip()
                continue

            # Skip other metadata lines
            if line_stripped.startswith("<") and line_stripped.endswith(">"):
                continue

            # Collect document content
            current_doc_lines.append(line)

        # Find the main document (10-Q, 10-K, 8-K, etc.)
        main_document = None

        # Priority order for main documents
        main_doc_types = ["10-Q", "10-K", "10-K/A", "10-Q/A", "8-K", "8-K/A"]

        # First try to find by document type
        for doc_type in main_doc_types:
            for doc in documents:
                if doc["type"] == doc_type:
                    main_document = str(doc.get("content", ""))
                    break
            if main_document:
                break

        # If the primary document is too small (like Apple's case), look for alternatives
        if main_document and len(main_document.strip()) < 500:
            # Look for documents with substantial content
            content_docs = [
                doc
                for doc in documents
                if isinstance(doc.get("line_count"), int) and int(doc.get("line_count", 0)) > 100
            ]

            if content_docs:
                # Prefer .htm files with substantial content
                htm_docs = [
                    doc
                    for doc in content_docs
                    if isinstance(doc.get("filename"), str) and doc.get("filename", "").endswith(".htm")
                ]
                if htm_docs:
                    largest_htm = max(htm_docs, key=lambda x: int(x.get("line_count", 0)))
                    main_document = str(largest_htm.get("content", ""))
                else:
                    # Take the document with most content
                    largest_doc = max(content_docs, key=lambda x: int(x.get("line_count", 0)))
                    main_document = str(largest_doc.get("content", ""))

        # If still no good document found, take the largest by content
        if not main_document or len(main_document.strip()) < 100:
            if documents:
                largest_doc = max(documents, key=lambda x: len(str(x.get("content", ""))))
                main_document = str(largest_doc.get("content", ""))

        # If still no document, return empty
        if not main_document:
            return ""

        # Clean up whitespace
        if main_document:
            text = re.sub(r"\n\s*\n\s*\n", "\n\n", str(main_document))
            text = re.sub(r" +", " ", text)
            text = text.strip()
        else:
            text = ""

        return text

    def extract_best_content_from_txt(self, txt_content: str) -> str:
        """Extract the best available content, prioritizing substantial documents."""
        lines = txt_content.split("\n")
        documents: List[Dict[str, Any]] = []
        current_doc_lines: List[str] = []
        current_doc_type: Optional[str] = None
        current_doc_filename: Optional[str] = None
        in_document = False

        for line in lines:
            line_stripped = line.strip()

            # Look for document start
            if line_stripped.startswith("<DOCUMENT>"):
                in_document = True
                current_doc_lines = []
                current_doc_type = None
                current_doc_filename = None
                continue
            elif line_stripped.startswith("</DOCUMENT>"):
                # End of document - save it
                if current_doc_lines:
                    content = "\n".join(current_doc_lines)
                    # Only save documents with meaningful content
                    meaningful_lines = [
                        line for line in current_doc_lines if line.strip() and not line.strip().startswith("<")
                    ]
                    if meaningful_lines:
                        documents.append(
                            {
                                "type": current_doc_type or "UNKNOWN",
                                "filename": current_doc_filename or "unknown",
                                "content": content,
                                "meaningful_lines": len(meaningful_lines),
                                "total_chars": len(content),
                            }
                        )
                in_document = False
                current_doc_lines = []
                current_doc_type = None
                current_doc_filename = None
                continue

            # Skip non-document content
            if not in_document:
                continue

            # Look for document metadata
            if line_stripped.startswith("<TYPE>"):
                current_doc_type = line_stripped.replace("<TYPE>", "").strip()
                continue
            elif line_stripped.startswith("<FILENAME>"):
                current_doc_filename = line_stripped.replace("<FILENAME>", "").strip()
                continue

            # Skip most metadata lines but keep some content
            if line_stripped.startswith("<") and line_stripped.endswith(">"):
                # Skip pure metadata
                if not any(keyword in line_stripped.lower() for keyword in ["html", "body", "table", "form"]):
                    continue

            # Collect document content
            current_doc_lines.append(line)

        if not documents:
            return ""

        # Score documents based on content quality
        for doc in documents:
            score = 0

            # Prefer main filing types
            if doc["type"] in ["10-Q", "10-K", "8-K", "10-K/A", "10-Q/A", "8-K/A"]:
                score += 1000

            # Prefer .htm files
            filename = doc.get("filename", "")
            if isinstance(filename, str) and filename.endswith(".htm"):
                score += 500

            # Prefer substantial content
            meaningful_lines = doc.get("meaningful_lines", 0)
            if isinstance(meaningful_lines, int):
                if meaningful_lines > 1000:
                    score += 300
                elif meaningful_lines > 100:
                    score += 100

            # Prefer larger documents
            total_chars = doc.get("total_chars", 0)
            if isinstance(total_chars, int):
                score += min(total_chars // 1000, 200)

            doc["score"] = score

        # Get the best document
        best_doc = max(documents, key=lambda x: x.get("score", 0))

        # Clean up the content
        content = str(best_doc.get("content", ""))
        content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)
        content = re.sub(r" +", " ", content)
        content = content.strip()

        return content

    def get_document_info_from_txt(self, txt_content: str) -> List[Dict[str, Any]]:
        """Get information about all documents in the .txt filing."""
        lines = txt_content.split("\n")
        documents: List[Dict[str, Any]] = []
        current_doc_info: Dict[str, Any] = {}
        in_document = False

        for line in lines:
            line_stripped = line.strip()

            # Look for document start
            if line_stripped.startswith("<DOCUMENT>"):
                in_document = True
                current_doc_info = {}
                continue
            elif line_stripped.startswith("</DOCUMENT>"):
                # End of document - save info
                if current_doc_info:
                    documents.append(current_doc_info)
                in_document = False
                current_doc_info = {}
                continue

            # Skip non-document content
            if not in_document:
                continue

            # Extract document metadata
            if line_stripped.startswith("<TYPE>"):
                current_doc_info["type"] = line_stripped.replace("<TYPE>", "").strip()
            elif line_stripped.startswith("<SEQUENCE>"):
                current_doc_info["sequence"] = line_stripped.replace("<SEQUENCE>", "").strip()
            elif line_stripped.startswith("<FILENAME>"):
                current_doc_info["filename"] = line_stripped.replace("<FILENAME>", "").strip()
            elif line_stripped.startswith("<DESCRIPTION>"):
                current_doc_info["description"] = line_stripped.replace("<DESCRIPTION>", "").strip()
            elif not line_stripped.startswith("<"):
                # Count content lines
                if "content_lines" not in current_doc_info:
                    current_doc_info["content_lines"] = 0
                current_lines = current_doc_info.get("content_lines", 0)
                if isinstance(current_lines, int):
                    current_doc_info["content_lines"] = current_lines + 1

        return documents

    def extract_sections(self, content: str) -> List[FilingSection]:
        """Extract sections from a filing document."""
        sections = []

        # Find section boundaries
        section_matches = []
        for section_id, pattern in self.section_patterns.items():
            matches = list(re.finditer(pattern, content, re.IGNORECASE | re.MULTILINE))
            for match in matches:
                section_matches.append((match.start(), section_id, match.group()))

        # Sort by position
        section_matches.sort(key=lambda x: x[0])

        # Extract section content
        for i, (start_pos, section_id, section_title) in enumerate(section_matches):
            # Determine end position
            if i + 1 < len(section_matches):
                end_pos = section_matches[i + 1][0]
            else:
                end_pos = len(content)

            section_content = content[start_pos:end_pos].strip()

            # Clean section title
            clean_title = re.sub(r"\s+", " ", section_title).strip()

            sections.append(FilingSection(name=clean_title, content=section_content, section_type=section_id))

        return sections

    def chunk_content(
        self, content: str, chunk_size: int = 8000, overlap_size: int = 200, section_name: str = "unknown"
    ) -> List[DocumentChunk]:
        """Chunk content into smaller pieces with overlap."""
        chunks = []
        start = 0
        chunk_index = 0

        while start < len(content):
            # Calculate end position
            end = min(start + chunk_size, len(content))

            # Try to break at a natural boundary (sentence, paragraph)
            if end < len(content):
                # Look for paragraph break
                para_break = content.rfind("\n\n", start, end)
                if para_break > start + chunk_size // 2:
                    end = para_break + 2
                else:
                    # Look for sentence break
                    sent_break = content.rfind(". ", start, end)
                    if sent_break > start + chunk_size // 2:
                        end = sent_break + 2

            chunk_content = content[start:end].strip()

            if chunk_content:
                chunks.append(
                    DocumentChunk(
                        content=chunk_content,
                        section_name=section_name,
                        chunk_index=chunk_index,
                        metadata={"start_pos": start, "end_pos": end, "total_length": len(content)},
                    )
                )
                chunk_index += 1

            # Move start position with overlap
            start = max(end - overlap_size, start + 1)
            if start >= len(content):
                break

        return chunks

    def chunk_by_sections(
        self, sections: List[FilingSection], chunk_size: int = 8000, overlap_size: int = 200
    ) -> List[DocumentChunk]:
        """Chunk content by sections with configurable chunk size."""
        all_chunks = []

        for section in sections:
            if len(section.content) <= chunk_size:
                # Section fits in one chunk
                all_chunks.append(
                    DocumentChunk(
                        content=section.content,
                        section_name=section.name,
                        chunk_index=0,
                        metadata={
                            "section_type": section.section_type,
                            "is_complete_section": True,
                            "word_count": section.word_count,
                            "char_count": section.char_count,
                        },
                    )
                )
            else:
                # Section needs to be chunked
                section_chunks = self.chunk_content(section.content, chunk_size, overlap_size, section.name)

                # Add section metadata
                for chunk in section_chunks:
                    chunk.metadata.update(
                        {
                            "section_type": section.section_type,
                            "is_complete_section": False,
                            "total_section_chunks": len(section_chunks),
                            "section_word_count": section.word_count,
                            "section_char_count": section.char_count,
                        }
                    )

                all_chunks.extend(section_chunks)

        return all_chunks

    def get_filing_summary(self, sections: List[FilingSection]) -> Dict[str, Any]:
        """Generate a summary of the filing structure."""
        total_words = sum(section.word_count for section in sections)
        total_chars = sum(section.char_count for section in sections)

        section_summary = []
        for section in sections:
            section_summary.append(
                {
                    "name": section.name,
                    "type": section.section_type,
                    "word_count": section.word_count,
                    "char_count": section.char_count,
                    "percentage": round((section.char_count / total_chars) * 100, 1) if total_chars > 0 else 0,
                }
            )

        return {
            "total_sections": len(sections),
            "total_words": total_words,
            "total_chars": total_chars,
            "sections": section_summary,
        }
