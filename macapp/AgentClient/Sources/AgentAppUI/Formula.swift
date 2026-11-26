import AppKit
import SwiftMath

// LaTeX formulas, typeset by SwiftMath.
//
// A formula is an image placed in the text: an attachment in the finished
// answer's text view, an `Image` inside a `Text` while the answer streams. An
// image does not grow with its fonts, so the attachment keeps its LaTeX and size,
// and zoom typesets it again; copying puts the LaTeX back, not U+FFFC.
//
// The image draws the label itself, from a drawing handler: vector at any backing
// scale, and laid out again on every draw because SwiftMath fixes its colour at
// layout, which would leave a formula drawn in light mode black in dark mode.

enum Formula {
    /// Latin Modern sets smaller than the system font at one point size; this
    /// makes a formula's letters the height of the text around it.
    static let scale: CGFloat = 1.15

    struct Typeset {
        let image: NSImage
        /// The same drawing as a template, for SwiftUI: it tints a template with
        /// the text's foreground, as it does the text, where the image's own
        /// colour follows AppKit's appearance and SwiftUI's colour scheme can
        /// differ from it (a light-scheme `ImageRenderer` drew white formulas on
        /// white under a dark system appearance).
        let template: NSImage
        /// How far the formula reaches below its baseline, which sits this far
        /// above the image's bottom edge.
        let descent: CGFloat
    }

    /// The formula at `size` points of text, or nil when SwiftMath cannot parse
    /// it, and the caller shows the LaTeX as written.
    @MainActor
    static func typeset(_ latex: String, size: CGFloat, display: Bool) -> Typeset? {
        let key = "\(display ? "d" : "i")\(size)|\(latex)" as NSString
        if let hit = cache.object(forKey: key) { return hit.value }

        let label = MTMathUILabel()
        label.font = fonts.latinModernFont(withSize: size * scale)
        label.labelMode = display ? .display : .text
        label.textAlignment = .left
        label.displayErrorInline = false
        label.latex = latex
        guard label.error == nil, label.mathList != nil else { return nil }

        let fitting = label.fittingSize
        // The label centres the formula in its frame, in at least half the font's
        // height; the baseline is the descent plus half of whatever is left over.
        let height = max(fitting.height, label.fontSize / 2)
        let frame = NSRect(x: 0, y: 0, width: ceil(fitting.width), height: ceil(height))
        label.frame = frame
        label.layout()
        guard let descent = label.displayList?.descent else { return nil }
        let baseline = descent + (frame.height - height) / 2

        let held = Held(label)
        let image = NSImage(size: frame.size, flipped: false) { rect in
            MainActor.assumeIsolated {
                held.label.textColor = .labelColor
                held.label.layout()
                held.label.draw(rect)
            }
            return true
        }
        let template = image.copy() as! NSImage
        template.isTemplate = true
        let typeset = Typeset(image: image, template: template, descent: baseline)
        cache.setObject(Box(typeset), forKey: key)
        return typeset
    }

    private final class Box {
        let value: Typeset
        init(_ value: Typeset) { self.value = value }
    }

    /// The label, for a drawing handler that AppKit only ever calls on the main thread.
    private final class Held: @unchecked Sendable {
        let label: MTMathUILabel
        init(_ label: MTMathUILabel) { self.label = label }
    }

    nonisolated(unsafe) private static let cache = NSCache<NSString, Box>()
    /// SwiftMath's shared manager is a mutable static, which Swift 6 refuses; this
    /// one is only touched on the main thread, and keeps the loaded fonts.
    @MainActor private static let fonts = MTFontManager()
}

/// For the bundle's self-test. SwiftMath reads its fonts from a resource bundle
/// that SwiftPM builds beside the executable, and a `.app` holds it only if the
/// packaging copies it to `Contents/Resources`; without it the first formula on
/// screen ends the app with a `fatalError`.
public enum FormulaProbe {
    public static let bundleName = "SwiftMath_SwiftMath.bundle"

    /// The size of a typeset formula, or nil when it does not typeset.
    @MainActor
    public static func typeset(_ latex: String) -> CGSize? {
        Formula.typeset(latex, size: 13, display: true)?.image.size
    }
}

/// A formula in the finished answer's text view.
final class FormulaAttachment: NSTextAttachment {
    let latex: String
    let display: Bool
    let size: CGFloat

    @MainActor
    init?(latex: String, display: Bool, size: CGFloat, maxWidth: CGFloat) {
        guard let typeset = Formula.typeset(latex, size: size, display: display) else { return nil }
        self.latex = latex
        self.display = display
        self.size = size
        super.init(data: nil, ofType: nil)
        image = typeset.image
        // A display formula wider than the column is shown whole and smaller, as
        // the one text view does not scroll sideways.
        let fit = min(1, maxWidth / max(typeset.image.size.width, 1))
        bounds = NSRect(
            x: 0, y: -typeset.descent * fit,
            width: typeset.image.size.width * fit, height: typeset.image.size.height * fit
        )
    }

    required init?(coder: NSCoder) { fatalError("a formula is built from its LaTeX, not decoded") }

    /// The LaTeX as the model wrote it, for the pasteboard.
    var source: String { display ? "\\[\(latex)\\]" : "\\(\(latex)\\)" }

    /// The same formula at another text size, for zoom.
    @MainActor
    func resized(by zoom: CGFloat, maxWidth: CGFloat) -> FormulaAttachment? {
        FormulaAttachment(latex: latex, display: display, size: size * zoom, maxWidth: maxWidth)
    }
}
