// taxonymous-vision: read the text in one image with macOS's Vision framework.
//
//     taxonymous-vision IMAGE        the text, one line per line read
//     taxonymous-vision IMAGE tsv    a table like `tesseract IMAGE stdout tsv` gives: a row per word
//                                    (block = the line Vision read), box in pixels, confidence 0-100
//
// Taxonymous runs it like Tesseract: one process per image, several at once.
// Build: swiftc -O native/macos/vision_ocr.swift -o build/taxonymous-vision

import Foundation
import ImageIO
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: taxonymous-vision IMAGE [tsv]") }
let tsv = args.count > 2 && args[2] == "tsv"
let url = URL(fileURLWithPath: args[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
else { fail("cannot read image \(args[1])") }
let width = Double(image.width), height = Double(image.height)

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false  // it "corrects" account numbers and names into words
do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
} catch {
    fail("Vision failed: \(error)")
}

var out = tsv ? "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" : ""
for (block, observation) in (request.results ?? []).enumerated() {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let text = candidate.string
    if !tsv {
        out += text + "\n"
        continue
    }
    let conf = String(format: "%.2f", Double(candidate.confidence) * 100)
    var word = 0
    var i = text.startIndex
    while i < text.endIndex {
        if text[i].isWhitespace {
            i = text.index(after: i)
            continue
        }
        var end = i
        while end < text.endIndex && !text[end].isWhitespace { end = text.index(after: end) }
        if let box = try? candidate.boundingBox(for: i..<end)?.boundingBox {
            // normalized, origin bottom left -> pixels, origin top left
            let x0 = box.minX * width, x1 = box.maxX * width
            let y0 = (1 - box.maxY) * height, y1 = (1 - box.minY) * height
            word += 1
            let w = max(1, Int((x1 - x0).rounded())), h = max(1, Int((y1 - y0).rounded()))
            out += "5\t1\t\(block + 1)\t1\t1\t\(word)\t\(Int(x0.rounded()))\t\(Int(y0.rounded()))\t\(w)\t\(h)\t\(conf)\t\(text[i..<end])\n"
        }
        i = end
    }
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
