"""OCR with macOS's Vision framework, a second engine beside Tesseract (see ocr.vision_on).

A page costs a fraction of the CPU Tesseract needs. Its output is turned into the same
table Tesseract's TSV gives (one row per word: block, paragraph, line, box in pixels,
confidence, text), so the rest of the engine reads it unchanged. Each Vision observation
is one line. Its word boxes take in the line spacing; readers shrink them to the ink.

Vision handles one request at a time in a process, so reads run in helper processes
(`python -m taxonymous.vision --serve`, one per core at most, started as needed), each
given one image at a time: a line with the image's path and "tsv" or "text", answered
with the output's length in bytes on a line, then the output.
"""

import io
import os
import queue
import subprocess
import sys
import threading
import time

from PIL import Image

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


_available: bool | None = None


def available() -> bool:
    global _available
    if _available is None:
        try:
            import Vision  # noqa: F401
            _available = True
        except ImportError:
            _available = False
    return _available


class _Helper:
    def __init__(self) -> None:
        self.proc = subprocess.Popen([sys.executable, "-m", "taxonymous.vision", "--serve"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, path: str, tsv: bool) -> str:
        self.proc.stdin.write(f"{path}\t{'tsv' if tsv else 'text'}\n".encode())
        self.proc.stdin.flush()
        size = self.proc.stdout.readline()
        if not size.strip().lstrip(b"-").isdigit():
            raise RuntimeError("Vision helper stopped")
        n = int(size)
        if n < 0:
            raise RuntimeError(self.proc.stdout.read(-n).decode(errors="replace"))
        return self.proc.stdout.read(n).decode()


_idle: queue.SimpleQueue[_Helper] = queue.SimpleQueue()
_started = 0
_start_lock = threading.Lock()


def read_file(path: str, tsv: bool, most: int) -> str:
    """Read an image file in a helper process; at most `most` helpers are started."""
    global _started
    try:
        helper = _idle.get_nowait()
    except queue.Empty:
        with _start_lock:
            start = _started < most
            if start:
                _started += 1
        helper = _Helper() if start else _idle.get()
    try:
        out = helper.read(path, tsv)
    except Exception:
        helper.proc.kill()
        with _start_lock:
            _started -= 1
        raise
    _idle.put(helper)
    return out


def shutdown() -> None:
    """Stop the idle helpers (they also stop on their own when this process exits)."""
    global _started
    while True:
        try:
            helper = _idle.get_nowait()
        except queue.Empty:
            return
        helper.proc.stdin.close()
        helper.proc.wait()
        with _start_lock:
            _started -= 1


def exit_with_parent() -> None:
    """For a helper process: stop at once when the app that started it is gone, even in
    the middle of a job (closing its input only stops it between jobs)."""
    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(1)
        os._exit(0)
    threading.Thread(target=watch, daemon=True).start()


def serve() -> None:
    """The helper's loop: read each image asked for and answer, until stdin closes."""
    exit_with_parent()
    out = sys.stdout.buffer
    for line in sys.stdin.buffer:
        path, mode = line.decode().rstrip("\n").rsplit("\t", 1)
        try:
            with open(path, "rb") as f:
                data = read(f.read(), mode == "tsv").encode()
            out.write(f"{len(data)}\n".encode() + data)
        except Exception as e:  # reported to the caller, which fails the read
            err = f"Vision OCR failed: {e}".encode()
            out.write(f"-{len(err)}\n".encode() + err)
        out.flush()


def _observations(png: bytes):
    import Vision
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(png, len(png))
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)  # it "corrects" account numbers and names into words
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {err}")
    return req.results() or []


def read(png: bytes, tsv: bool = False) -> str:
    """Tesseract-shaped output for one image: TSV rows, or plain text by line."""
    width, height = Image.open(io.BytesIO(png)).size
    rows, lines = [HEADER], []
    for block, obs in enumerate(_observations(png), 1):
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        cand = cand[0]
        text = str(cand.string())
        lines.append(text)
        if not tsv:
            continue
        conf = round(float(cand.confidence()) * 100, 2)
        pos, word = 0, 0
        for token in text.split(" "):
            start, pos = pos, pos + len(token) + 1
            if not token:
                continue
            box, _ = cand.boundingBoxForRange_error_((start, len(token)), None)
            if box is None:
                continue
            r = box.boundingBox()  # normalized, origin bottom left
            x0, x1 = r.origin.x * width, (r.origin.x + r.size.width) * width
            y0 = (1 - r.origin.y - r.size.height) * height
            y1 = (1 - r.origin.y) * height
            word += 1
            rows.append(f"5\t1\t{block}\t1\t1\t{word}\t{round(x0)}\t{round(y0)}\t"
                        f"{max(1, round(x1 - x0))}\t{max(1, round(y1 - y0))}\t{conf}\t{token}")
    return "\n".join(rows) + "\n" if tsv else "\n".join(lines) + "\n"


if __name__ == "__main__" and sys.argv[1:] == ["--serve"]:
    serve()
