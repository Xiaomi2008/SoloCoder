// Draws SoloCoder's app icon and writes AppIcon.icns.
//
//   swift macapp/icon/make_icon.swift [output-dir]
//
// Drawn in code rather than kept as a PNG, so every size in the .icns is
// rendered at that size and the design is a diff, not a binary blob. The shape
// follows Apple's macOS grid: an 824-point rounded square on a 1024 canvas,
// leaving room for the shadow the system expects icons to carry.

import AppKit
import Foundation

let output = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? "macapp/icon")
let iconset = output.appendingPathComponent("AppIcon.iconset")

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(
        srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
        green: CGFloat((hex >> 8) & 0xFF) / 255,
        blue: CGFloat(hex & 0xFF) / 255,
        alpha: alpha
    )
}

func gradient(_ colors: [CGColor], _ locations: [CGFloat]) -> CGGradient {
    CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: colors as CFArray, locations: locations)!
}

/// A four-pointed sparkle: the agent at work, and the one in "solo".
func sparkle(center: CGPoint, radius: CGFloat) -> CGPath {
    // One curve from each tip to the next, pulled toward the middle: a smooth
    // concave side with no corner where two curves would meet.
    let path = CGMutablePath()
    let pull = radius * 0.16
    func point(_ angle: CGFloat, _ distance: CGFloat) -> CGPoint {
        CGPoint(x: center.x + cos(angle) * distance, y: center.y + sin(angle) * distance)
    }
    path.move(to: point(0, radius))
    for i in 1...4 {
        let angle = CGFloat(i) * .pi / 2
        path.addQuadCurve(to: point(angle, radius), control: point(angle - .pi / 4, pull))
    }
    path.closeSubpath()
    return path
}

func draw(_ ctx: CGContext) {
    let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
    let body = CGPath(roundedRect: tile, cornerWidth: 185, cornerHeight: 185, transform: nil)

    // The system's drop shadow, as Apple's own icons carry it.
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: color(0x000000, 0.35))
    ctx.addPath(body)
    ctx.setFillColor(color(0x161A33))
    ctx.fillPath()
    ctx.restoreGState()

    // Night-sky background: indigo at the top to near-black navy at the bottom.
    ctx.saveGState()
    ctx.addPath(body)
    ctx.clip()
    ctx.drawLinearGradient(
        gradient([color(0x3B3A8C), color(0x1D2150), color(0x0E1128)], [0, 0.55, 1]),
        start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: []
    )
    // A soft glow behind the prompt, so the glyph sits in light.
    ctx.drawRadialGradient(
        gradient([color(0x4FE3C1, 0.28), color(0x4FE3C1, 0)], [0, 1]),
        startCenter: CGPoint(x: 440, y: 500), startRadius: 0,
        endCenter: CGPoint(x: 440, y: 500), endRadius: 420, options: []
    )
    // The glass edge: a faint highlight along the top.
    ctx.drawLinearGradient(
        gradient([color(0xFFFFFF, 0.16), color(0xFFFFFF, 0)], [0, 1]),
        start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 700), options: []
    )
    ctx.restoreGState()

    // The prompt chevron, stroked thick with round ends, filled with a gradient.
    let chevron = CGMutablePath()
    chevron.move(to: CGPoint(x: 300, y: 690))
    chevron.addLine(to: CGPoint(x: 500, y: 512))
    chevron.addLine(to: CGPoint(x: 300, y: 334))
    let stroke = chevron.copy(strokingWithWidth: 92, lineCap: .round, lineJoin: .round, miterLimit: 10)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -6), blur: 18, color: color(0x000000, 0.45))
    ctx.addPath(stroke)
    ctx.setFillColor(color(0x4FE3C1))
    ctx.fillPath()
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addPath(stroke)
    ctx.clip()
    ctx.drawLinearGradient(
        gradient([color(0x7CF5D6), color(0x4FD1E3), color(0x5A9BFF)], [0, 0.5, 1]),
        start: CGPoint(x: 300, y: 736), end: CGPoint(x: 500, y: 288),
        options: [.drawsBeforeStartLocation, .drawsAfterEndLocation]
    )
    ctx.restoreGState()

    // The cursor, warm against the cool prompt.
    let cursor = CGPath(
        roundedRect: CGRect(x: 548, y: 300, width: 196, height: 74),
        cornerWidth: 37, cornerHeight: 37, transform: nil
    )
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -6), blur: 18, color: color(0x000000, 0.45))
    ctx.addPath(cursor)
    ctx.setFillColor(color(0xFFB547))
    ctx.fillPath()
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addPath(cursor)
    ctx.clip()
    ctx.drawLinearGradient(
        gradient([color(0xFFD27A), color(0xFF9F43)], [0, 1]),
        start: CGPoint(x: 548, y: 374), end: CGPoint(x: 548, y: 300),
        options: [.drawsBeforeStartLocation, .drawsAfterEndLocation]
    )
    ctx.restoreGState()

    // The sparkle, top right, with a small companion.
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 30, color: color(0xFFE9A8, 0.8))
    ctx.addPath(sparkle(center: CGPoint(x: 708, y: 700), radius: 100))
    ctx.setFillColor(color(0xFFF4D6))
    ctx.fillPath()
    ctx.addPath(sparkle(center: CGPoint(x: 800, y: 590), radius: 42))
    ctx.setFillColor(color(0xFFF4D6, 0.85))
    ctx.fillPath()
    ctx.restoreGState()
}

func render(pixels: Int) -> Data {
    let ctx = CGContext(
        data: nil, width: pixels, height: pixels, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpace(name: CGColorSpace.sRGB)!,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    )!
    ctx.interpolationQuality = .high
    ctx.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
    draw(ctx)
    let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
    return rep.representation(using: .png, properties: [:])!
}

try? FileManager.default.removeItem(at: iconset)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
for points in [16, 32, 128, 256, 512] {
    try render(pixels: points).write(to: iconset.appendingPathComponent("icon_\(points)x\(points).png"))
    try render(pixels: points * 2).write(to: iconset.appendingPathComponent("icon_\(points)x\(points)@2x.png"))
}
try render(pixels: 1024).write(to: output.appendingPathComponent("AppIcon-1024.png"))

let iconutil = Process()
iconutil.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
iconutil.arguments = ["-c", "icns", iconset.path, "-o", output.appendingPathComponent("AppIcon.icns").path]
try iconutil.run()
iconutil.waitUntilExit()
try FileManager.default.removeItem(at: iconset)
guard iconutil.terminationStatus == 0 else { fatalError("iconutil failed") }
print("==> \(output.appendingPathComponent("AppIcon.icns").path)")
