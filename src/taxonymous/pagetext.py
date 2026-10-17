"""Character-level view of a PDF page.

`PageText.text` is the page's text with lines joined by "\\n"; `boxes[i]` is
the bounding box of `text[i]` (None for separators we inserted). Detectors
run regexes over `text` and map character ranges back to rectangles, so a
redaction covers exactly the matched characters and nothing next to them.
"""

from dataclasses import dataclass, field

import pymupdf

FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_MEDIABOX_CLIP


@dataclass
class Line:
    start: int  # offset of first char in PageText.text
    end: int  # offset one past the last char
    rect: pymupdf.Rect


@dataclass
class PageText:
    number: int
    text: str = ""
    boxes: list[pymupdf.Rect | None] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)

    def rects(self, start: int, end: int) -> list[pymupdf.Rect]:
        """One rectangle per text line covered by text[start:end]."""
        out: list[pymupdf.Rect] = []
        current: pymupdf.Rect | None = None
        for i in range(start, end):
            if self.text[i] == "\n":
                if current is not None:
                    out.append(current)
                    current = None
                continue
            box = self.boxes[i]
            if box is None or box.is_empty:
                continue
            current = pymupdf.Rect(box) if current is None else current | box
        if current is not None:
            out.append(current)
        return out

    def line_at(self, offset: int) -> Line | None:
        for line in self.lines:
            if line.start <= offset < line.end:
                return line
        return None


def extract(page: pymupdf.Page) -> PageText:
    pt = PageText(number=page.number)
    chunks: list[str] = []
    boxes: list[pymupdf.Rect | None] = []
    pos = 0
    raw = page.get_text("rawdict", flags=FLAGS)
    for block in raw["blocks"]:
        for line in block.get("lines", []):
            start = pos
            prev: dict | None = None
            size = 0.0
            for span in line["spans"]:
                size = span["size"]
                for ch in span["chars"]:
                    c = ch["c"]
                    if prev is not None and c != " " and prev["c"] != " ":
                        # separate words that were placed apart without a space character
                        if ch["bbox"][0] - prev["bbox"][2] > 0.25 * size:
                            chunks.append(" ")
                            boxes.append(None)
                            pos += 1
                    chunks.append(c)
                    boxes.append(pymupdf.Rect(ch["bbox"]) if c.strip() else None)
                    pos += 1
                    prev = ch
            if pos > start:
                pt.lines.append(Line(start, pos, pymupdf.Rect(line["bbox"])))
                chunks.append("\n")
                boxes.append(None)
                pos += 1
    pt.text = "".join(chunks)
    pt.boxes = boxes
    return pt
