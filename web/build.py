"""Build Taxonymous for the browser into web/dist: a static site, the engine running in the
page (Pyodide + PyMuPDF + the app's own Python), the workspace UI (ui/), every file served
from the site itself.

    uv run python web/build.py      # builds the UI, fetches pinned files once, then copies
    uv run python web/serve.py      # http://127.0.0.1:8800

dist/ can be deployed as it is (Cloudflare Pages: `npx wrangler pages deploy web/dist`).
manifest.json lists every file the setup step downloads, with its size and SHA-256: the
page checks each one before keeping it, so a changed file is refused.
"""

import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
DIST = HERE / "dist"
CACHE = HERE / ".downloads"  # pinned files, fetched once (gitignored with dist)
PYODIDE = "0.28.3"
CDN = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE}/full"
PACKAGES = ["pymupdf", "numpy", "pillow", "pydantic", "anyio"]
SAMPLES = ["household.json"]  # the synthetic test household: fake people, fake numbers
PYPI = "https://files.pythonhosted.org/packages"
# the app's server (taxonymous.api), pure Python, at the versions the app pins (uv.lock); pydantic
# and anyio are Pyodide's own. Each checked against PyPI's SHA-256.
WHEELS = {
    "fastapi-0.143.3-py3-none-any.whl":
        ("2c/1a/03d246befcf307219bb2161c97d8c5d314f84125bf7dd245730a28589828",
         "75e8bd68e2f52f77d29b2879749c257d64de2d85282d25270caf2bfccce5efe5"),
    "starlette-1.8.0-py3-none-any.whl":
        ("c1/b0/5742e4ac7af5eb58ec3470a537a49d7aa507e5539413e504b3a65ef50ba8",
         "dfdd6b29c26483288088d990eee59631dedadd66ce20d203402a7ca8e3c4656f"),
    "python_multipart-0.0.32-py3-none-any.whl":
        ("e1/04/e8135ebd1ad02c56ec633277529b2602ff99ff634be76cdba5744cf554fd",
         "ff6d3f776f16878c894e52e107296ffc890e913c611b1a4ec6c44e2821fe2e23"),
    "typing_inspection-0.4.4-py3-none-any.whl":
        ("67/81/4add07e5172b7ac40d8ed5ff580409a7801a4fe26d529bdd915401dabfbe",
         "65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147"),
    "annotated_doc-0.0.5-py3-none-any.whl":
        ("3e/30/e900b21425a860e195f32e37657aa1f7c7f2b1bfb26f03ca209b90933c06",
         "117bac03a25ede5df5440e855b32d556049ca169ead221505badf432fed4b101"),
    "typing_extensions-4.16.0-py3-none-any.whl":  # newer than Pyodide's: typing-inspection needs it
        ("49/d3/b8441a820a491ddfc024b0b0cf0393375b75ea13866d9c66727e54c2fc80",
         "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8"),
    "opentelemetry_api-1.45.1-py3-none-any.whl":
        ("1e/41/f7dcf80b81ee8e71c1a2b59f14208bc723edbd89ed027a73b175abf6348e",
         "b31553efa588ae44bc306f863c785c5333a9ecc091248c6ee68b4b6c87fdedfb"),
}
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
        name = todo.pop().lower().replace("_", "-")
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
    (DIST / "engine" / "wheels").mkdir(parents=True)
    for name, (where, digest) in WHEELS.items():
        data = fetch(f"{PYPI}/{where}/{name}")
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f"{name} isn't the file PyPI published: not used")
        (DIST / "engine" / "wheels" / name).write_bytes(data)
    # the engine, as it is in the repo, and its two roles in the page (engine/: home, lane)
    (DIST / "engine" / "taxonymous").mkdir(parents=True)
    for py in sorted((ROOT / "src" / "taxonymous").glob("*.py")):
        shutil.copy(py, DIST / "engine" / "taxonymous" / py.name)
    for py in sorted((HERE / "engine").glob("*.py")):
        shutil.copy(py, DIST / "engine" / py.name)
    # synthetic samples (the generated test corpus: fake people, fake numbers)
    (DIST / "samples").mkdir()
    for name in SAMPLES:
        shutil.copy(ROOT / "tests" / "fixtures" / "generated" / name, DIST / "samples" / name)
    for f in (HERE / "static").iterdir():
        shutil.copy(f, DIST / f.name)
    # the workspace UI (ui/, built here): its files, placed in the page (boot.js adds them once
    # your file is open)
    subprocess.run(["npm", "run", "build"], cwd=ROOT / "ui", check=True, capture_output=True)
    built = (ROOT / "ui" / "dist" / "index.html").read_text()
    tags = re.findall(r"<script [^>]*></script>|<link rel=\"stylesheet\"[^>]*>", built)
    shutil.copytree(ROOT / "ui" / "dist" / "assets", DIST / "assets")
    page = (DIST / "index.html").read_text()
    page = page.replace("<!-- the workspace UI's files (build.py) -->", "".join(tags))
    # the page's own files named by their contents: a page always loads the files made with it,
    # even from a site or an offline layer that still has an older version's under the old name
    for name in ("boot.css", "bridge.js", "keyfile.js", "boot.js"):
        data = (DIST / name).read_bytes()
        named = f"{Path(name).stem}.{hashlib.sha256(data).hexdigest()[:10]}{Path(name).suffix}"
        (DIST / name).rename(DIST / named)
        assert page.count(f'"{name}"') == 1, name
        page = page.replace(f'"{name}"', f'"{named}"')
    (DIST / "index.html").write_text(page)
    # what the setup step downloads: everything, the page too (a version's page and its scripts
    # always go together), but the site's own headers
    keep = [p for p in sorted(DIST.rglob("*")) if p.is_file() and p.relative_to(DIST).as_posix() != "_headers"]
    manifest = [{"path": p.relative_to(DIST).as_posix(), "size": p.stat().st_size,
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in keep]
    (DIST / "manifest.json").write_text(json.dumps({"version": PYODIDE, "files": manifest}, indent=1))
    total = sum(f["size"] for f in manifest)
    print(f"dist: {len(manifest)} files, {total / 2**20:.1f} MB to download once")


if __name__ == "__main__":
    main()
