import AppKit
import SwiftUI

// ⌘+ / ⌘- / ⌘0: the conversation's text size.
//
// SwiftUI's own answer, `dynamicTypeSize`, does nothing on macOS — a `.body` is
// 13 pt at every size, measured — and `scaleEffect` scales a bitmap of the
// answer's text view and leaves the layout at the old size. So the transcript
// asks for its fonts through `zoomFont`, which reads one factor from the
// environment and multiplies the text style's point size by it. The names match
// `Font`'s (`.caption`, `.system(size:weight:)`, `.weight(_:)`), so a call site
// changes by its modifier's name only.
//
// Only the conversation zooms: the sidebar, the header and the composer's
// controls are chrome, and a browser's zoom leaves its own chrome alone too.

public enum Zoom {
    /// The `UserDefaults` key the factor is kept under, so it survives a relaunch
    /// and the menu commands and the views share it without plumbing.
    public static let key = "transcriptZoom"
    public static let steps: [Double] = [0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]

    public static func larger(_ zoom: Double) -> Double {
        steps.first { $0 > zoom + 0.001 } ?? steps.last!
    }

    public static func smaller(_ zoom: Double) -> Double {
        steps.last { $0 < zoom - 0.001 } ?? steps.first!
    }

    /// The factor as stored, read at the moment of the keystroke. A command's
    /// `@AppStorage` copy can be a step behind when the other shortcut changed
    /// it, and a step computed from a stale copy repeats or skips one.
    static var current: Double {
        let stored = UserDefaults.standard.double(forKey: key)
        return stored > 0 ? stored : 1
    }

    static func zoomIn() { UserDefaults.standard.set(larger(current), forKey: key) }
    static func zoomOut() { UserDefaults.standard.set(smaller(current), forKey: key) }
    static func reset() { UserDefaults.standard.set(1.0, forKey: key) }
}

private struct ZoomKey: EnvironmentKey {
    static let defaultValue: CGFloat = 1
}

extension EnvironmentValues {
    var zoom: CGFloat {
        get { self[ZoomKey.self] }
        set { self[ZoomKey.self] = newValue }
    }
}

/// A font named the way `Font` names one, resolved at the zoom in effect.
struct ZoomFont {
    var size: CGFloat
    var weight: Font.Weight = .regular
    var design: Font.Design = .default
    var monospacedDigits = false

    private init(size: CGFloat, weight: Font.Weight = .regular) {
        self.size = size
        self.weight = weight
    }

    /// The text style's point size on this Mac: what `Font.body` and the rest
    /// draw at, so a zoom of 1 is the window as it was.
    private init(_ style: NSFont.TextStyle, weight: Font.Weight = .regular) {
        self.init(size: NSFont.preferredFont(forTextStyle: style).pointSize, weight: weight)
    }

    static var body: ZoomFont { ZoomFont(.body) }
    static var callout: ZoomFont { ZoomFont(.callout) }
    static var caption: ZoomFont { ZoomFont(.caption1) }
    static var caption2: ZoomFont { ZoomFont(.caption2) }
    static var headline: ZoomFont { ZoomFont(.headline, weight: .bold) }
    static var subheadline: ZoomFont { ZoomFont(.subheadline) }
    static var title2: ZoomFont { ZoomFont(.title2) }
    static var title3: ZoomFont { ZoomFont(.title3) }

    static func system(size: CGFloat, weight: Font.Weight = .regular) -> ZoomFont {
        ZoomFont(size: size, weight: weight)
    }

    static func system(_ style: ZoomFont, design: Font.Design) -> ZoomFont {
        var font = style
        font.design = design
        return font
    }

    func weight(_ weight: Font.Weight) -> ZoomFont {
        var font = self
        font.weight = weight
        return font
    }

    func monospacedDigit() -> ZoomFont {
        var font = self
        font.monospacedDigits = true
        return font
    }

    func font(at zoom: CGFloat) -> Font {
        let font = Font.system(size: size * zoom, weight: weight, design: design)
        return monospacedDigits ? font.monospacedDigit() : font
    }
}

private struct ZoomedFont: ViewModifier {
    let font: ZoomFont
    @Environment(\.zoom) private var zoom

    func body(content: Content) -> some View {
        content.font(font.font(at: zoom))
    }
}

extension View {
    /// `.font(_:)`, at the conversation's zoom.
    func zoomFont(_ font: ZoomFont) -> some View {
        modifier(ZoomedFont(font: font))
    }
}

extension NSAttributedString {
    /// Every font in the string, `zoom` times its size: the answer's text view
    /// is built at the system sizes and scaled once, rather than every builder
    /// taking the factor.
    func zoomed(_ zoom: CGFloat) -> NSAttributedString {
        guard zoom != 1 else { return self }
        let out = NSMutableAttributedString(attributedString: self)
        let all = NSRange(location: 0, length: out.length)
        out.enumerateAttribute(.font, in: all) { value, range, _ in
            guard let font = value as? NSFont else { return }
            out.addAttribute(.font, value: font.withSize(font.pointSize * zoom), range: range)
        }
        // A list's hanging indent is measured in its marker's font.
        out.enumerateAttribute(.paragraphStyle, in: all) { value, range, _ in
            guard let style = value as? NSParagraphStyle, style.headIndent > 0 else { return }
            let scaled = style.mutableCopy() as! NSMutableParagraphStyle
            scaled.headIndent *= zoom
            scaled.firstLineHeadIndent *= zoom
            scaled.tabStops = style.tabStops.map { NSTextTab(textAlignment: $0.alignment, location: $0.location * zoom) }
            out.addAttribute(.paragraphStyle, value: scaled, range: range)
        }
        return out
    }
}

/// View ▸ Actual Size / Zoom In / Zoom Out.
public struct ZoomCommands: Commands {
    @AppStorage(Zoom.key) private var zoom = 1.0

    public init() {}

    public var body: some Commands {
        CommandGroup(before: .toolbar) {
            Button("Actual Size") { Zoom.reset() }
                .keyboardShortcut("0", modifiers: .command)
                .disabled(zoom == 1)
            Button("Zoom In") { Zoom.zoomIn() }
                .keyboardShortcut("+", modifiers: .command)
                .disabled(zoom >= Zoom.steps.last!)
            Button("Zoom Out") { Zoom.zoomOut() }
                .keyboardShortcut("-", modifiers: .command)
                .disabled(zoom <= Zoom.steps.first!)
            Divider()
        }
    }
}

/// ⌘= as Zoom In: "+" is shift-= on most layouts, and the browsers and Xcode
/// take the unshifted key too. A menu item has one shortcut, so this one is a
/// button with no size in the window.
struct ZoomInAlias: View {
    var body: some View {
        Button("Zoom In") { Zoom.zoomIn() }
            .keyboardShortcut("=", modifiers: .command)
            .frame(width: 0, height: 0)
            .opacity(0)
            .accessibilityHidden(true)
    }
}
