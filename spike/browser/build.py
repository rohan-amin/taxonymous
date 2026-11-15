"""Build the browser spike into spike/browser/dist: a static site, the engine running in the
browser (Pyodide + PyMuPDF), every file served from the site itself.

    uv run python spike/browser/build.py      # fetches pinned files once, then copies
    uv run python spike/browser/serve.py      # http://127.0.0.1:8800

dist/ can be deployed as it is (Cloudflare Pages: `npx wrangler pages deploy spike/browser/dist`).
manifest.json lists every file the setup step downloads, with its size and SHA-256: the
page checks each one before keeping it, so a changed file is refused.
"""

import hashlib
import json
import shutil
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
DIST = HERE / "dist"
CACHE = HERE / ".downloads"  # pinned files, fetched once (gitignored with dist)
PYODIDE = "0.28.3"
CDN = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE}/full"
PACKAGES = ["pymupdf", "numpy", "pillow"]
SAMPLES = ["household.json", "w2.pdf", "1099_int.pdf", "k1_statement.pdf", "brokerage_consolidated.pdf",
           "w2_scan.pdf", "1099_int_scan.pdf", "receipt_photo.jpg"]
NPM = "https://cdn.jsdelivr.net/npm"
ORT = f"{NPM}/onnxruntime-web@1.22.0/dist"
# a mirror of PaddleOCR's own models in ONNX (not pinned to a commit yet: the real build should)
HF = "https://huggingface.co/snowfluke/ppu-paddle-ocr-models/resolve/main"
# OCR (Tesseract, the same engine and English model as natively: tessdata_fast 4.1.0) and
# barcodes (ZXing, the same C++ library as natively), each pinned
EXTRA = {
    "ocr/tesseract.min.js": f"{NPM}/tesseract.js@5.1.1/dist/tesseract.min.js",
    "ocr/worker.min.js": f"{NPM}/tesseract.js@5.1.1/dist/worker.min.js",
    "ocr/tesseract-core-simd-lstm.wasm.js": f"{NPM}/tesseract.js-core@5.1.1/tesseract-core-simd-lstm.wasm.js",
    "ocr/tesseract-core-lstm.wasm.js": f"{NPM}/tesseract.js-core@5.1.1/tesseract-core-lstm.wasm.js",
    "ocr/eng.traineddata": "https://cdn.jsdelivr.net/gh/tesseract-ocr/tessdata_fast@4.1.0/eng.traineddata",
    "barcode/zxing-reader.js": f"{NPM}/zxing-wasm@2.2.4/dist/iife/reader/index.js",
    "barcode/zxing_reader.wasm": f"{NPM}/zxing-wasm@2.2.4/dist/reader/zxing_reader.wasm",
    # the second OCR engine, in place of the Mac's Vision: PaddleOCR v5 (Apache 2.0, the
    # official models in ONNX), run by onnxruntime-web
    # (the WebGPU build: the graphics card when there is one, the processor when not)
    "paddle/ort.webgpu.min.js": f"{ORT}/ort.webgpu.min.js",
    "paddle/ort-wasm-simd-threaded.jsep.wasm": f"{ORT}/ort-wasm-simd-threaded.jsep.wasm",
    "paddle/ort-wasm-simd-threaded.jsep.mjs": f"{ORT}/ort-wasm-simd-threaded.jsep.mjs",
    "paddle/det.onnx": f"{HF}/detection/PP-OCRv5_mobile_det_infer.onnx",
    "paddle/rec.onnx": f"{HF}/recognition/multi/en/v5/en_PP-OCRv5_mobile_rec_infer.onnx",
    "paddle/dict.txt": f"{HF}/recognition/multi/en/v5/ppocrv5_en_dict.txt",
}


def fetch(url: str) -> bytes:
    path = CACHE / url.split("://", 1)[1].replace("/", "_")
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print("fetching", url)
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 (a pinned https URL)
            path.write_bytes(r.read())
    return path.read_bytes()


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    (DIST / "pyodide").mkdir(parents=True)
    # the Python runtime and the packages the engine needs, from the pinned release
    lock = json.loads(fetch(f"{CDN}/pyodide-lock.json"))
    needed, todo = set(), list(PACKAGES)
    while todo:
        name = todo.pop()
        if name not in needed:
            needed.add(name)
            todo += lock["packages"][name].get("depends", [])
    files = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]
    files += [lock["packages"][n]["file_name"] for n in sorted(needed)]
    for name in files:
        (DIST / "pyodide" / name).write_bytes(fetch(f"{CDN}/{name}"))
    for path, url in EXTRA.items():
        (DIST / path).parent.mkdir(parents=True, exist_ok=True)
        (DIST / path).write_bytes(fetch(url))
    # the engine, as it is in the repo
    (DIST / "engine" / "taxonymous").mkdir(parents=True)
    for py in sorted((ROOT / "src" / "taxonymous").glob("*.py")):
        shutil.copy(py, DIST / "engine" / "taxonymous" / py.name)
    shutil.copy(HERE / "run.py", DIST / "engine" / "run.py")
    # synthetic samples (the generated test corpus: fake people, fake numbers)
    (DIST / "samples").mkdir()
    for name in SAMPLES:
        shutil.copy(ROOT / "tests" / "fixtures" / "generated" / name, DIST / "samples" / name)
    for f in (HERE / "static").iterdir():
        shutil.copy(f, DIST / f.name)
    # what the setup step downloads: everything but the page itself
    keep = [p for p in sorted(DIST.rglob("*")) if p.is_file() and p.name not in ("index.html", "_headers")]
    manifest = [{"path": p.relative_to(DIST).as_posix(), "size": p.stat().st_size,
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in keep]
    (DIST / "manifest.json").write_text(json.dumps({"version": PYODIDE, "files": manifest}, indent=1))
    total = sum(f["size"] for f in manifest)
    print(f"dist: {len(manifest)} files, {total / 2**20:.1f} MB to download once")


if __name__ == "__main__":
    main()
