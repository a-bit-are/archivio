// ==============================================================================
// Archivio — Native Apple Vision & AVFoundation Camera Helper
// Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3
// ==============================================================================
import Foundation
import Vision
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

struct VisionResult: Codable {
    let barcodes: [String]
    let Symbologies: [String]
    let lines: [OCRLine]
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
    let message: String?
}

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

func loadPreprocessedCGImages(from path: String) -> [CGImage] {
    let url = URL(fileURLWithPath: path)
    guard let baseCI = CIImage(contentsOf: url, options: [.applyOrientationProperty: true]) else {
        return []
    }
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
    // Pin a Revision 3 per evitare la ricompilazione AOT Neural Engine (~90s su macOS recenti) mantenendo ~0.2s e massima precisione
    if #available(macOS 13.0, *) {
        request.revision = VNRecognizeTextRequestRevision3
    }
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.minimumTextHeight = 0.012
    request.recognitionLanguages = ["it-IT", "en-US"]
    try? handler.perform([request])
    return lines
}

func recognizeText(in cgImage: CGImage) -> [OCRLine] {
    return performTextRecognition(with: VNImageRequestHandler(cgImage: cgImage, options: [:]))
}

func recognizeTextInPixelBuffer(_ pixelBuffer: CVPixelBuffer) -> [OCRLine] {
    return performTextRecognition(with: VNImageRequestHandler(cvPixelBuffer: pixelBuffer, options: [:]))
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

func scanImageFile(at imgPath: String, cmd: String) -> VisionResult {
    let images = loadPreprocessedCGImages(from: imgPath)
    if images.isEmpty {
        return VisionResult(barcodes: [], Symbologies: [], lines: [], error: "Cannot load image")
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
    if cmd == "scan" || cmd == "ocr" || allCodes.isEmpty {
        ocrLines = recognizeText(in: images[0])
        if ocrLines.count < 2 && images.count > 1 {
            let extra = recognizeText(in: images[1])
            var seenTxt = Set(ocrLines.map { $0.text.lowercased() })
            for ln in extra {
                if !seenTxt.contains(ln.text.lowercased()) {
                    seenTxt.insert(ln.text.lowercased())
                    ocrLines.append(ln)
                }
            }
        }
    }

    return VisionResult(barcodes: allCodes, Symbologies: allSyms, lines: ocrLines, error: nil)
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

    func requestScanFile(_ path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        ocrQueue.async {
            let res = scanImageFile(at: trimmed, cmd: "ocr")
            emitJSONLine(LiveFrameMsg(
                type: "ocr_result",
                jpeg_b64: nil,
                jpeg: nil,
                barcode: res.barcodes.first,
                barcodes: res.barcodes,
                lines: res.lines,
                message: nil
            ))
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
            ocrQueue.async { [weak self] in
                defer {
                    if let self = self {
                        self.lock.lock()
                        self.ocrInFlight = false
                        self.lock.unlock()
                    }
                }
                let codes = detectBarcodesInPixelBuffer(pixelBuffer)
                let lines = recognizeTextInPixelBuffer(pixelBuffer)
                emitJSONLine(LiveFrameMsg(
                    type: "ocr_result",
                    jpeg_b64: nil,
                    jpeg: nil,
                    barcode: codes.first,
                    barcodes: codes,
                    lines: lines,
                    message: nil
                ))
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
    let err = VisionResult(barcodes: [], Symbologies: [], lines: [], error: "Usage: vision_scanner <scan|qr|cameras|livecam> ...")
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

if args.count < 3 {
    let err = VisionResult(barcodes: [], Symbologies: [], lines: [], error: "Missing arguments")
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
