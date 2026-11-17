// ==============================================================================
// Archivio — Native Apple Vision & AVFoundation Camera Helper
// Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3
// ==============================================================================
import Foundation
import Vision
import CoreML
import CoreImage
import AppKit
import AVFoundation
import CoreMedia
import CoreVideo

struct OCRLine: Codable {
    let text: String
    let height: Double
    let y: Double
    let confidence: Float
}

struct VisualMatch: Codable {
    let title: String
    let artist: String
    let year: String
    let mbid: String
    let distance: Double
}

struct VisionResult: Codable {
    let barcodes: [String]
    let Symbologies: [String]
    let lines: [OCRLine]
    let visual_matches: [VisualMatch]
    let error: String?
}

struct CameraInfo: Codable {
    let id: String
    let name: String
}

struct LiveFrameMsg: Codable {
    let type: String
    let jpeg_b64: String?
    let jpeg: String?
    let barcode: String?
    let barcodes: [String]?
    let lines: [OCRLine]?
    let visual_matches: [VisualMatch]?
    let message: String?

    init(
        type: String,
        jpeg_b64: String? = nil,
        jpeg: String? = nil,
        barcode: String? = nil,
        barcodes: [String]? = nil,
        lines: [OCRLine]? = nil,
        visual_matches: [VisualMatch]? = nil,
        message: String? = nil
    ) {
        self.type = type
        self.jpeg_b64 = jpeg_b64
        self.jpeg = jpeg
        self.barcode = barcode
        self.barcodes = barcodes
        self.lines = lines
        self.visual_matches = visual_matches
        self.message = message
    }
}

private struct SeedCoverEntry {
    let title: String
    let artist: String
    let year: String
    let mbid: String
    let vec: [Float]
}

private let SEED_COVER_DIM = 768

private func resolveSeedCoversURL() -> URL? {
    let fm = FileManager.default
    var candidates: [String] = []
    if let envPath = ProcessInfo.processInfo.environment["ARCHIVIO_SEED_COVERS_PATH"], !envPath.isEmpty {
        candidates.append(envPath)
    }
    let arg0 = CommandLine.arguments.first ?? ""
    if !arg0.isEmpty {
        let rawURL = URL(fileURLWithPath: arg0)
        let resolvedURL = rawURL.resolvingSymlinksInPath()
        for exeURL in [rawURL, resolvedURL] {
            let dir = exeURL.deletingLastPathComponent()
            candidates.append(dir.appendingPathComponent("seed_covers.json").path)
            candidates.append(
                dir.appendingPathComponent("../../../Resources/archivio/data/seed_covers.json")
                    .standardizedFileURL.path
            )
            candidates.append(
                dir.appendingPathComponent("../Resources/archivio/data/seed_covers.json")
                    .standardizedFileURL.path
            )
            candidates.append(
                dir.appendingPathComponent("app/archivio/data/seed_covers.json")
                    .standardizedFileURL.path
            )
        }
    }
    let cwd = fm.currentDirectoryPath
    candidates.append(URL(fileURLWithPath: cwd).appendingPathComponent("app/archivio/data/seed_covers.json").path)
    for p in candidates {
        if fm.fileExists(atPath: p) {
            return URL(fileURLWithPath: p)
        }
    }
    return nil
}

private let SEED_COVERS: [SeedCoverEntry] = {
    guard let url = resolveSeedCoversURL(),
          let data = try? Data(contentsOf: url),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let rows = json["items"] as? [[Any]] else {
        return []
    }
    let dim = (json["dim"] as? Int) ?? SEED_COVER_DIM
    let scale = Float((json["scale"] as? Double) ?? 32767.0)
    guard dim == SEED_COVER_DIM, scale > 0 else { return [] }
    var out: [SeedCoverEntry] = []
    out.reserveCapacity(rows.count)
    for row in rows {
        guard row.count >= 5,
              let title = row[0] as? String,
              let artist = row[1] as? String,
              let mbid = row[3] as? String,
              let fpB64 = row[4] as? String,
              let raw = Data(base64Encoded: fpB64),
              raw.count == dim * 2 else {
            continue
        }
        let year = (row[2] as? String) ?? "\(row[2])"
        var vec = [Float](repeating: 0, count: dim)
        raw.withUnsafeBytes { buf in
            let ptr = buf.bindMemory(to: Int16.self)
            for i in 0..<dim {
                vec[i] = Float(Int16(littleEndian: ptr[i])) / scale
            }
        }
        out.append(SeedCoverEntry(title: title, artist: artist, year: year, mbid: mbid, vec: vec))
    }
    return out
}()

private let emitLock = NSLock()

func emitJSONLine<T: Encodable>(_ obj: T) {
    if let data = try? JSONEncoder().encode(obj),
       let str = String(data: data, encoding: .utf8),
       let lineData = (str + "\n").data(using: .utf8) {
        emitLock.lock()
        FileHandle.standardOutput.write(lineData)
        emitLock.unlock()
    }
}

func discoverCameras() -> [AVCaptureDevice] {
    var types: [AVCaptureDevice.DeviceType] = [.builtInWideAngleCamera, .external]
    if #available(macOS 14.0, *) {
        types.append(.continuityCamera)
    }
    let session = AVCaptureDevice.DiscoverySession(
        deviceTypes: types,
        mediaType: .video,
        position: .unspecified
    )
    return session.devices
}

private let SHARED_CI_CONTEXT = CIContext(options: nil)

func preprocessCGImages(from baseCI: CIImage) -> [CGImage] {
    var out: [CGImage] = []
    if let cg = SHARED_CI_CONTEXT.createCGImage(baseCI, from: baseCI.extent) {
        out.append(cg)
    }
    // Pass 2: Anti-riflesso cellophane / plastica CD e Vinili
    if let colorFilter = CIFilter(name: "CIColorControls") {
        colorFilter.setValue(baseCI, forKey: kCIInputImageKey)
        colorFilter.setValue(1.55, forKey: kCIInputContrastKey)
        colorFilter.setValue(-0.08, forKey: kCIInputBrightnessKey)
        colorFilter.setValue(0.0, forKey: kCIInputSaturationKey)
        if let ciOut = colorFilter.outputImage,
           let cg2 = SHARED_CI_CONTEXT.createCGImage(ciOut, from: ciOut.extent) {
            out.append(cg2)
        }
    }
    return out
}

func detectBarcodes(in cgImage: CGImage) -> [(String, String)] {
    var found: [(String, String)] = []
    let request = VNDetectBarcodesRequest { req, _ in
        guard let results = req.results as? [VNBarcodeObservation] else { return }
        for obs in results {
            if let payload = obs.payloadStringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
               !payload.isEmpty {
                found.append((payload, obs.symbology.rawValue))
            }
        }
    }
    request.symbologies = [.ean13, .ean8, .upce, .code128, .code39, .itf14]
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try? handler.perform([request])
    return found
}

func detectBarcodesInPixelBuffer(_ pixelBuffer: CVPixelBuffer) -> [String] {
    var found: [String] = []
    let request = VNDetectBarcodesRequest { req, _ in
        guard let results = req.results as? [VNBarcodeObservation] else { return }
        for obs in results {
            if let payload = obs.payloadStringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
               !payload.isEmpty {
                found.append(payload)
            }
        }
    }
    request.symbologies = [.ean13, .ean8, .upce, .code128, .code39, .itf14]
    let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, options: [:])
    try? handler.perform([request])
    return found
}

private func forceCPUOnly(_ request: VNRequest) {
    if #available(macOS 14.0, *) {
        if let stages = try? request.supportedComputeStageDevices {
            for (stage, devs) in stages {
                for dev in devs {
                    if case .cpu = dev {
                        request.setComputeDevice(dev, for: stage)
                        break
                    }
                }
            }
        }
    } else {
        request.usesCPUOnly = true
    }
}

private func performTextRecognition(with handler: VNImageRequestHandler) -> [OCRLine] {
    var lines: [OCRLine] = []
    let request = VNRecognizeTextRequest { req, _ in
        guard let results = req.results as? [VNRecognizedTextObservation] else { return }
        for obs in results {
            guard let top = obs.topCandidates(1).first else { continue }
            let txt = top.string.trimmingCharacters(in: .whitespacesAndNewlines)
            if !txt.isEmpty {
                let bbox = obs.boundingBox
                lines.append(OCRLine(
                    text: txt,
                    height: Double(bbox.height),
                    y: Double(bbox.origin.y),
                    confidence: top.confidence
                ))
            }
        }
    }
    // Modalità .accurate su CPU: ~0.07-0.14s senza stalli ANE (MILCompilerForANE), legge font stilizzati
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.minimumTextHeight = 0.010
    request.recognitionLanguages = ["it-IT", "en-US", "fr-FR", "es-ES", "de-DE"]
    forceCPUOnly(request)
    try? handler.perform([request])
    return lines
}

func recognizeText(in cgImage: CGImage) -> [OCRLine] {
    return performTextRecognition(with: VNImageRequestHandler(cgImage: cgImage, options: [:]))
}

private func renderCGImage(_ ci: CIImage, maxDim: CGFloat = 0) -> CGImage? {
    let ext = ci.extent
    guard ext.width > 1, ext.height > 1 else { return nil }
    var working = ci
    if ext.origin.x != 0 || ext.origin.y != 0 {
        working = working.transformed(by: CGAffineTransform(translationX: -ext.origin.x, y: -ext.origin.y))
    }
    if maxDim > 0 {
        let curMax = max(ext.width, ext.height)
        if curMax > maxDim {
            let s = maxDim / curMax
            working = working.transformed(by: CGAffineTransform(scaleX: s, y: s))
        }
    }
    return SHARED_CI_CONTEXT.createCGImage(working, from: working.extent)
}

private func hasVisualContrast(_ cgImage: CGImage) -> Bool {
    let w = 16
    let h = 16
    var pixels = [UInt8](repeating: 0, count: w * h * 4)
    guard let ctx = CGContext(
        data: &pixels,
        width: w,
        height: h,
        bitsPerComponent: 8,
        bytesPerRow: w * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        return true
    }
    ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: w, height: h))
    var minLuma = 255
    var maxLuma = 0
    var coloredPixels = 0
    var nonWhitePixels = 0
    for i in stride(from: 0, to: pixels.count, by: 4) {
        let r = Int(pixels[i])
        let g = Int(pixels[i + 1])
        let b = Int(pixels[i + 2])
        let luma = (r + g + b) / 3
        let sat = max(r, max(g, b)) - min(r, min(g, b))
        if luma < minLuma { minLuma = luma }
        if luma > maxLuma { maxLuma = luma }
        if sat >= 12 {
            coloredPixels += 1
        }
        if luma <= 215 {
            nonWhitePixels += 1
        }
    }
    return (maxLuma - minLuma) >= 16 && (coloredPixels >= 16 || nonWhitePixels >= 40)
}

private func extractFeaturePrint(from cgImage: CGImage, orientation: CGImagePropertyOrientation = .up) -> [Float]? {
    let req = VNGenerateImageFeaturePrintRequest()
    forceCPUOnly(req)
    let handler = VNImageRequestHandler(cgImage: cgImage, orientation: orientation, options: [:])
    do {
        try handler.perform([req])
    } catch {
        return nil
    }
    guard let obs = req.results?.first as? VNFeaturePrintObservation,
          obs.elementCount == SEED_COVER_DIM,
          obs.elementType == .float else {
        return nil
    }
    let floats: [Float] = obs.data.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
    return floats.count == SEED_COVER_DIM ? floats : nil
}

private func l2Distance(_ a: [Float], _ b: [Float]) -> Double {
    var sumSq: Float = 0.0
    for i in 0..<SEED_COVER_DIM {
        let d = a[i] - b[i]
        sumSq += d * d
    }
    return Double(sqrtf(sumSq))
}

private func detectAlbumRectCrops(in normCI: CIImage, fullCG: CGImage) -> [CIImage] {
    var rects: [VNRectangleObservation] = []
    let req = VNDetectRectanglesRequest { r, _ in
        if let res = r.results as? [VNRectangleObservation] {
            rects = res
        }
    }
    req.maximumObservations = 3
    req.minimumAspectRatio = 0.65
    req.maximumAspectRatio = 1.45
    req.minimumSize = 0.22
    req.minimumConfidence = 0.45
    forceCPUOnly(req)
    let handler = VNImageRequestHandler(cgImage: fullCG, options: [:])
    try? handler.perform([req])

    let ext = normCI.extent
    var out: [CIImage] = []
    for obs in rects {
        let bbox = obs.boundingBox
        if bbox.width * bbox.height < 0.10 { continue }
        guard let filter = CIFilter(name: "CIPerspectiveCorrection") else { continue }
        filter.setValue(normCI, forKey: kCIInputImageKey)
        filter.setValue(CIVector(x: obs.topLeft.x * ext.width, y: obs.topLeft.y * ext.height), forKey: "inputTopLeft")
        filter.setValue(CIVector(x: obs.topRight.x * ext.width, y: obs.topRight.y * ext.height), forKey: "inputTopRight")
        filter.setValue(CIVector(x: obs.bottomLeft.x * ext.width, y: obs.bottomLeft.y * ext.height), forKey: "inputBottomLeft")
        filter.setValue(CIVector(x: obs.bottomRight.x * ext.width, y: obs.bottomRight.y * ext.height), forKey: "inputBottomRight")
        if let corrected = filter.outputImage, corrected.extent.width >= 32, corrected.extent.height >= 32 {
            out.append(corrected)
        }
    }
    return out
}

func matchCoverArtwork(in baseCI: CIImage) -> [VisualMatch] {
    guard !SEED_COVERS.isEmpty else { return [] }
    let origExt = baseCI.extent
    guard origExt.width >= 32, origExt.height >= 32 else { return [] }

    let normCI: CIImage
    if origExt.origin.x != 0 || origExt.origin.y != 0 {
        normCI = baseCI.transformed(by: CGAffineTransform(translationX: -origExt.origin.x, y: -origExt.origin.y))
    } else {
        normCI = baseCI
    }
    guard let fullCG = renderCGImage(normCI, maxDim: 640), hasVisualContrast(fullCG) else {
        return []
    }

    var cropCGs: [CGImage] = [fullCG]
    for rectCI in detectAlbumRectCrops(in: normCI, fullCG: fullCG) {
        if let cg = renderCGImage(rectCI, maxDim: 640) {
            cropCGs.append(cg)
        }
    }
    let ext = normCI.extent
    let minSide = min(ext.width, ext.height)
    let cropConfigs: [(CGFloat, [CGFloat], [CGFloat])] = [
        (1.00, [-0.13, 0.0, 0.13], [0.0]),
        (0.85, [-0.12, 0.0, 0.12], [0.0]),
        (0.74, [-0.11, 0.0, 0.11], [-0.04, 0.0, 0.04]),
        (0.58, [0.0], [0.0])
    ]
    let isNearSquare = (ext.width / ext.height) >= 0.85 && (ext.width / ext.height) <= 1.18
    for (scale, oxList, oyList) in cropConfigs {
        let side = minSide * scale
        for ox in oxList {
            for oy in oyList {
                if isNearSquare && scale >= 0.98 && (ox != 0.0 || oy != 0.0) {
                    continue
                }
                let cx = ext.midX + ox * ext.width
                let cy = ext.midY + oy * ext.height
                let rect = CGRect(
                    x: cx - side / 2.0,
                    y: cy - side / 2.0,
                    width: side,
                    height: side
                ).intersection(ext)
                if rect.width >= 32 && rect.height >= 32,
                   let cg = renderCGImage(normCI.cropped(to: rect), maxDim: 640) {
                    cropCGs.append(cg)
                }
            }
        }
    }

    var acceptedByIdx: [Int: (score: Double, rawDist: Double)] = [:]
    var bestAcceptedDist = 999.0
    let orientations: [CGImagePropertyOrientation] = [.up, .upMirrored]
    let contrastedCrops = cropCGs.filter { hasVisualContrast($0) }
    for orient in orientations {
        var foundStrongLock = false
        for cg in contrastedCrops {
            guard let vec = extractFeaturePrint(from: cg, orientation: orient) else { continue }
            var best1Idx = -1
            var best1Dist = 999.0
            var best2Dist = 999.0
            for idx in 0..<SEED_COVERS.count {
                let dist = l2Distance(vec, SEED_COVERS[idx].vec)
                if dist < best1Dist {
                    best2Dist = best1Dist
                    best1Dist = dist
                    best1Idx = idx
                } else if dist < best2Dist {
                    best2Dist = dist
                }
            }
            let margin = best2Dist - best1Dist
            // Accetta solo lock diretti (<=0.44) oppure candidati con margine netto sul 2° classificato
            let accepted = best1Idx >= 0 && (
                best1Dist <= 0.44
                || (best1Dist <= 0.58 && margin >= 0.045)
                || (best1Dist <= 0.66 && margin >= 0.085)
            )
            if accepted {
                let effDist = best1Dist
                let rankScore = best1Dist - min(0.08, margin * 0.40)
                if effDist < bestAcceptedDist {
                    bestAcceptedDist = effDist
                }
                if let prev = acceptedByIdx[best1Idx] {
                    if rankScore < prev.score || (rankScore == prev.score && effDist < prev.rawDist) {
                        acceptedByIdx[best1Idx] = (rankScore, effDist)
                    }
                } else {
                    acceptedByIdx[best1Idx] = (rankScore, effDist)
                }
                if best1Dist <= 0.25 && margin >= 0.12 {
                    foundStrongLock = true
                    break
                }
            }
        }
        if foundStrongLock || bestAcceptedDist <= 0.48 {
            break
        }
    }

    let sorted = acceptedByIdx.sorted { $0.value.score < $1.value.score }.prefix(5)
    return sorted.map { idx, entry in
        let item = SEED_COVERS[idx]
        return VisualMatch(
            title: item.title,
            artist: item.artist,
            year: item.year,
            mbid: item.mbid,
            distance: (entry.rawDist * 10000.0).rounded() / 10000.0
        )
    }
}

func generateQR(text: String, outPath: String) -> Bool {
    guard let data = text.data(using: .utf8),
          let qrFilter = CIFilter(name: "CIQRCodeGenerator") else {
        return false
    }
    qrFilter.setValue(data, forKey: "inputMessage")
    qrFilter.setValue("M", forKey: "inputCorrectionLevel")
    guard let ciImage = qrFilter.outputImage else { return false }
    let scaleX = 520.0 / ciImage.extent.size.width
    let scaleY = 520.0 / ciImage.extent.size.height
    let transformed = ciImage.transformed(by: CGAffineTransform(scaleX: scaleX, y: scaleY))
    let rep = NSCIImageRep(ciImage: transformed)
    let nsImage = NSImage(size: rep.size)
    nsImage.addRepresentation(rep)
    guard let tiffData = nsImage.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiffData),
          let pngData = bitmap.representation(using: .png, properties: [:]) else {
        return false
    }
    do {
        try pngData.write(to: URL(fileURLWithPath: outPath))
        return true
    } catch {
        return false
    }
}

private func appendUniqueOCRLines(_ extra: [OCRLine], to ocrLines: inout [OCRLine], seenTxt: inout Set<String>) {
    for ln in extra {
        let key = ln.text.lowercased()
        if !seenTxt.contains(key) {
            seenTxt.insert(key)
            ocrLines.append(ln)
        }
    }
}

func scanCIImage(_ baseCI: CIImage, cmd: String) -> VisionResult {
    let images = preprocessCGImages(from: baseCI)
    if images.isEmpty {
        return VisionResult(barcodes: [], Symbologies: [], lines: [], visual_matches: [], error: "Cannot render image")
    }

    var allCodes: [String] = []
    var allSyms: [String] = []
    var seen = Set<String>()

    for cg in images {
        let found = detectBarcodes(in: cg)
        for (c, sym) in found {
            if !seen.contains(c) {
                seen.insert(c)
                allCodes.append(c)
                allSyms.append(sym)
            }
        }
        if !allCodes.isEmpty && cmd == "barcode" {
            break
        }
    }

    var ocrLines: [OCRLine] = []
    var visualMatches: [VisualMatch] = []
    if cmd == "scan" || cmd == "ocr" || allCodes.isEmpty {
        ocrLines = recognizeText(in: images[0])
        var seenTxt = Set(ocrLines.map { $0.text.lowercased() })
        let multiCharCount = {
            ocrLines.filter { $0.text.trimmingCharacters(in: .whitespacesAndNewlines).count >= 2 }.count
        }
        if multiCharCount() < 3 && images.count > 1 {
            appendUniqueOCRLines(recognizeText(in: images[1]), to: &ocrLines, seenTxt: &seenTxt)
        }
        if multiCharCount() < 3 {
            let ext = baseCI.extent
            let minSide = min(ext.width, ext.height)
            if minSide >= 64 {
                let side = minSide * 0.74
                let cropRect = CGRect(
                    x: ext.midX - side / 2.0,
                    y: ext.midY - side / 2.0,
                    width: side,
                    height: side
                ).intersection(ext)
                if let centerCG = renderCGImage(baseCI.cropped(to: cropRect), maxDim: 960) {
                    appendUniqueOCRLines(recognizeText(in: centerCG), to: &ocrLines, seenTxt: &seenTxt)
                }
            }
            if multiCharCount() < 3 {
                let normCI: CIImage
                if ext.origin.x != 0 || ext.origin.y != 0 {
                    normCI = baseCI.transformed(by: CGAffineTransform(translationX: -ext.origin.x, y: -ext.origin.y))
                } else {
                    normCI = baseCI
                }
                if let rectCI = detectAlbumRectCrops(in: normCI, fullCG: images[0]).first,
                   let rectCG = renderCGImage(rectCI, maxDim: 960) {
                    appendUniqueOCRLines(recognizeText(in: rectCG), to: &ocrLines, seenTxt: &seenTxt)
                }
            }
        }
        if allCodes.isEmpty {
            visualMatches = matchCoverArtwork(in: baseCI)
        }
    }

    return VisionResult(barcodes: allCodes, Symbologies: allSyms, lines: ocrLines, visual_matches: visualMatches, error: nil)
}

func scanImageFile(at imgPath: String, cmd: String) -> VisionResult {
    let url = URL(fileURLWithPath: imgPath)
    guard let baseCI = CIImage(contentsOf: url, options: [.applyOrientationProperty: true]) else {
        return VisionResult(barcodes: [], Symbologies: [], lines: [], visual_matches: [], error: "Cannot load image")
    }
    return scanCIImage(baseCI, cmd: cmd)
}

func makeWarmupTextImage() -> CGImage? {
    let w = 256
    let h = 64
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: w,
        pixelsHigh: h,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    )
    guard let rep = rep, let nsCtx = NSGraphicsContext(bitmapImageRep: rep) else {
        return nil
    }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = nsCtx
    NSColor.white.setFill()
    NSRect(x: 0, y: 0, width: w, height: h).fill()
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 20, weight: .bold),
        .foregroundColor: NSColor.black
    ]
    NSAttributedString(string: "8059973191590 ABC", attributes: attrs).draw(at: NSPoint(x: 12, y: 18))
    NSGraphicsContext.restoreGraphicsState()
    return rep.cgImage
}

final class MacLiveCameraStreamer: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let session = AVCaptureSession()
    private let ciContext = CIContext(options: [.useSoftwareRenderer: false])
    private let ocrQueue = DispatchQueue(label: "com.antoniocafaro.archivio.liveocr", qos: .userInitiated)
    private let barcodeQueue = DispatchQueue(label: "com.antoniocafaro.archivio.livebarcode", qos: .userInitiated)
    private let lock = NSLock()
    private var lastFrameTime: TimeInterval = 0
    private var wantOCRSnapshot: Bool = false
    private var ocrInFlight: Bool = false
    private var barcodeInFlight: Bool = false
    private var latestBarcodes: [String] = []
    private var latestBarcodeTime: TimeInterval = 0
    private var emittedFrames: Int = 0
    private var warmupScheduled: Bool = false

    func requestOCR() {
        lock.lock()
        wantOCRSnapshot = true
        lock.unlock()
    }

    private func emitOCRResult(_ res: VisionResult) {
        emitJSONLine(LiveFrameMsg(
            type: "ocr_result",
            barcode: res.barcodes.first,
            barcodes: res.barcodes,
            lines: res.lines,
            visual_matches: res.visual_matches
        ))
    }

    func requestScanFile(_ path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        ocrQueue.async { [weak self] in
            let res = scanImageFile(at: trimmed, cmd: "ocr")
            self?.emitOCRResult(res)
        }
    }

    private func scheduleOCRWarmupIfNeeded() {
        lock.lock()
        let shouldSchedule = !warmupScheduled
        if shouldSchedule { warmupScheduled = true }
        lock.unlock()
        guard shouldSchedule else { return }
        DispatchQueue.global(qos: .background).asyncAfter(deadline: .now() + 2.5) { [weak self] in
            self?.ocrQueue.async {
                if let cg = makeWarmupTextImage() {
                    _ = recognizeText(in: cg)
                }
            }
        }
    }

    func start(preferredId: String?) -> Bool {
        let status = AVCaptureDevice.authorizationStatus(for: .video)
        if status == .notDetermined {
            let sem = DispatchSemaphore(value: 0)
            var granted = false
            AVCaptureDevice.requestAccess(for: .video) { ok in
                granted = ok
                sem.signal()
            }
            sem.wait()
            if !granted {
                emitJSONLine(LiveFrameMsg(
                    type: "error", jpeg_b64: nil, jpeg: nil, barcode: nil, barcodes: nil, lines: nil,
                    message: "Permesso fotocamera negato. Abilita Archivio in Impostazioni di Sistema → Privacy e sicurezza → Fotocamera."
                ))
                return false
            }
        } else if status == .denied || status == .restricted {
            emitJSONLine(LiveFrameMsg(
                type: "error", jpeg_b64: nil, jpeg: nil, barcode: nil, barcodes: nil, lines: nil,
                message: "Accesso alla fotocamera disabilitato. Abilita Archivio in Impostazioni di Sistema → Privacy e sicurezza → Fotocamera."
            ))
            return false
        }

        let cams = discoverCameras()
        guard !cams.isEmpty else {
            emitJSONLine(LiveFrameMsg(
                type: "error", jpeg_b64: nil, jpeg: nil, barcode: nil, barcodes: nil, lines: nil,
                message: "Nessuna fotocamera rilevata su questo Mac."
            ))
            return false
        }

        let chosen = cams.first(where: { $0.uniqueID == preferredId }) ?? cams[0]

        session.beginConfiguration()
        if session.canSetSessionPreset(.hd1280x720) {
            session.sessionPreset = .hd1280x720
        } else if session.canSetSessionPreset(.vga640x480) {
            session.sessionPreset = .vga640x480
        }

        guard let input = try? AVCaptureDeviceInput(device: chosen),
              session.canAddInput(input) else {
            session.commitConfiguration()
            emitJSONLine(LiveFrameMsg(
                type: "error", jpeg_b64: nil, jpeg: nil, barcode: nil, barcodes: nil, lines: nil,
                message: "Impossibile aprire la fotocamera selezionata (\(chosen.localizedName))."
            ))
            return false
        }
        session.addInput(input)

        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)
        ]
        let queue = DispatchQueue(label: "com.antoniocafaro.archivio.livecam", qos: .userInteractive)
        output.setSampleBufferDelegate(self, queue: queue)
        if session.canAddOutput(output) {
            session.addOutput(output)
        }
        if let conn = output.connection(with: .video), conn.isVideoMirroringSupported {
            conn.automaticallyAdjustsVideoMirroring = false
            conn.isVideoMirrored = false
        }
        session.commitConfiguration()
        session.startRunning()

        emitJSONLine(LiveFrameMsg(
            type: "ready",
            jpeg_b64: nil,
            jpeg: nil,
            barcode: nil,
            barcodes: nil,
            lines: nil,
            message: chosen.localizedName
        ))
        return true
    }

    func stop() {
        if session.isRunning {
            session.stopRunning()
        }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        lock.lock()
        let doOCR = wantOCRSnapshot && !ocrInFlight
        if doOCR {
            wantOCRSnapshot = false
            ocrInFlight = true
        }
        lock.unlock()

        if doOCR {
            let rawCI = CIImage(cvPixelBuffer: pixelBuffer)
            let snapCG = ciContext.createCGImage(rawCI, from: rawCI.extent)
            ocrQueue.async { [weak self] in
                defer {
                    if let self = self {
                        self.lock.lock()
                        self.ocrInFlight = false
                        self.lock.unlock()
                    }
                }
                guard let self = self else { return }
                if let cg = snapCG {
                    let res = scanCIImage(CIImage(cgImage: cg), cmd: "ocr")
                    self.emitOCRResult(res)
                } else {
                    self.emitOCRResult(VisionResult(barcodes: [], Symbologies: [], lines: [], visual_matches: [], error: nil))
                }
            }
        }

        let now = Date().timeIntervalSince1970
        if now - lastFrameTime < 0.085 {
            return
        }
        lastFrameTime = now

        lock.lock()
        let frameCount = emittedFrames
        emittedFrames = frameCount + 1
        let startBarcode = frameCount >= 2 && !barcodeInFlight
        if startBarcode {
            barcodeInFlight = true
        }
        let codes: [String] = (now - latestBarcodeTime <= 0.7) ? latestBarcodes : []
        lock.unlock()

        if startBarcode {
            barcodeQueue.async { [weak self] in
                let detected = detectBarcodesInPixelBuffer(pixelBuffer)
                let detectedAt = Date().timeIntervalSince1970
                guard let self = self else { return }
                self.lock.lock()
                if !detected.isEmpty {
                    self.latestBarcodes = detected
                    self.latestBarcodeTime = detectedAt
                } else if detectedAt - self.latestBarcodeTime > 0.7 {
                    self.latestBarcodes = []
                }
                self.barcodeInFlight = false
                self.lock.unlock()
            }
        }

        if frameCount == 6 {
            scheduleOCRWarmupIfNeeded()
        }

        let bestCode = codes.first

        var ciImg = CIImage(cvPixelBuffer: pixelBuffer)
        let w = ciImg.extent.width
        let h = ciImg.extent.height
        if w > 0 && h > 0 {
            // Anteprima fluida a 960px senza mai bloccare il thread video su Vision/ANE
            let targetW: CGFloat = 960.0
            let scale = min(1.0, targetW / w)
            if scale < 1.0 {
                ciImg = ciImg.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            }
            if let cg = ciContext.createCGImage(ciImg, from: ciImg.extent) {
                let rep = NSBitmapImageRep(cgImage: cg)
                if let jpeg = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.78]) {
                    let b64 = jpeg.base64EncodedString()
                    emitJSONLine(LiveFrameMsg(
                        type: "frame",
                        jpeg_b64: b64,
                        jpeg: nil,
                        barcode: bestCode,
                        barcodes: codes,
                        lines: nil,
                        message: nil
                    ))
                }
            }
        }
    }
}

let args = CommandLine.arguments
if args.count < 2 {
    let err = VisionResult(barcodes: [], Symbologies: [], lines: [], visual_matches: [], error: "Usage: vision_scanner <scan|qr|cameras|livecam> ...")
    emitJSONLine(err)
    exit(1)
}

let cmd = args[1]

if cmd == "cameras" {
    let list = discoverCameras().map { CameraInfo(id: $0.uniqueID, name: $0.localizedName) }
    emitJSONLine(list)
    exit(0)
}

if cmd == "livecam" {
    let prefId: String? = args.count >= 3 && !args[2].isEmpty ? args[2] : nil
    let streamer = MacLiveCameraStreamer()
    if !streamer.start(preferredId: prefId) {
        exit(1)
    }
    DispatchQueue.global(qos: .utility).async {
        while let line = readLine() {
            let raw = line.trimmingCharacters(in: .whitespacesAndNewlines)
            let lowered = raw.lowercased()
            if lowered == "quit" || lowered == "stop" {
                streamer.stop()
                exit(0)
            } else if lowered == "ocr" {
                streamer.requestOCR()
            } else if lowered.hasPrefix("scanfile ") {
                let idx = raw.index(raw.startIndex, offsetBy: 9)
                streamer.requestScanFile(String(raw[idx...]))
            }
        }
        streamer.stop()
        exit(0)
    }
    RunLoop.main.run()
    exit(0)
}

if cmd == "batch_scan" {
    while let line = readLine() {
        let path = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if path.isEmpty { continue }
        if path == "__QUIT__" { break }
        autoreleasepool {
            let res = scanImageFile(at: path, cmd: "scan")
            emitJSONLine(res)
        }
    }
    exit(0)
}

if args.count < 3 {
    let err = VisionResult(barcodes: [], Symbologies: [], lines: [], visual_matches: [], error: "Missing arguments")
    emitJSONLine(err)
    exit(1)
}

if cmd == "qr" && args.count >= 4 {
    let ok = generateQR(text: args[2], outPath: args[3])
    exit(ok ? 0 : 1)
}

let res = scanImageFile(at: args[2], cmd: cmd)
emitJSONLine(res)
exit(0)
