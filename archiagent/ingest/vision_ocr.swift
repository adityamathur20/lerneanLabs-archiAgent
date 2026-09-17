#!/usr/bin/swift
// Offline macOS Vision OCR. Input coordinates are raw image pixels (.up).
import Foundation
import Vision
import ImageIO
import Darwin

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

guard CommandLine.arguments.count == 2 else {
    fail("usage: vision_ocr.swift IMAGE_PATH")
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fail("cannot read OCR image")
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["en-US"]
request.minimumTextHeight = 0.001
let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
do {
    try handler.perform([request])
    let observations = (request.results ?? []).sorted {
        if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.001 {
            return $0.boundingBox.midY > $1.boundingBox.midY
        }
        return $0.boundingBox.minX < $1.boundingBox.minX
    }
    let records: [[String: Any]] = observations.compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return ["text": candidate.string, "confidence": Double(candidate.confidence),
                "bbox": [Double(box.minX), Double(box.minY), Double(box.width), Double(box.height)]]
    }
    let result: [String: Any] = ["engine": "macos-vision", "pixel_width": image.width,
                               "pixel_height": image.height,
                               "coordinate_system": "normalized-bottom-left", "records": records]
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fail("macOS Vision OCR failed: \(error.localizedDescription)")
}
