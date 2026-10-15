import AppKit
import SwiftUI

// The visual language of the window.
//
// The app used to render in stock SwiftUI: system blue accents, default
// `DisclosureGroup` chrome, a box behind every tool row, and a status bar of
// eight controls sharing one 30-point strip. It worked, and it read like a
// debugger. The reference for the replacement is Bionic, whose window is calmer
// for four reasons, in order of how much each one costs to imitate:
//
// 1. **Borders are hairlines, not fills.** Its separators are the foreground at
//    4%, 7% and 12% alpha. A 12%-alpha rule reads as an edge without adding a
//    grey rectangle, which is why a screen full of its rows does not look like a
//    table.
// 2. **One accent, used sparingly.** Violet `#6141FF` in the light appearance,
//    `#8C80FF` in the dark one — the pair is in its own bundle, and the dark
//    value is lighter because a saturated violet on near-black vibrates.
// 3. **Surfaces are neutral, not tinted.** `#F3F3F3`/`#E8E8E8` light,
//    `#1E1E1E`/`#292929` dark. No blue cast, so the accent stays the only
//    coloured thing on the screen and keeps its meaning.
// 4. **A reading column.** The transcript is ~760 pt wide and centred; the text
//    is not 1,400 characters per line at full window width.
//
// The values are the ones its renderer ships with, read out of the installed
// bundle — not a guess at what it looks like. Every token below is a dynamic
// colour, so the window follows the system appearance without a `colorScheme`
// plumbing through every view.
enum Theme {
    // MARK: colour

    /// A separator: the foreground at 7%.
    static let hairline = dynamic(.black.withAlphaComponent(0.07), .white.withAlphaComponent(0.07))
    /// An edge that has to survive being next to a filled surface: 12%.
    static let edge = dynamic(.black.withAlphaComponent(0.12), .white.withAlphaComponent(0.12))
    /// A recessed fill — a hover, a chip, a code block: 4%.
    static let fill = dynamic(.black.withAlphaComponent(0.04), .white.withAlphaComponent(0.04))
    /// A fill that has to read as raised against `fill`: 7%.
    static let fillRaised = dynamic(.black.withAlphaComponent(0.07), .white.withAlphaComponent(0.07))

    /// The window's ground, and the surface a card sits on.
    static let surface = dynamic(srgb(243), srgb(30))
    static let surfaceRaised = dynamic(srgb(232), srgb(41))

    static let accent = dynamic(srgb(97, 65, 255), srgb(140, 128, 255))
    static let success = dynamic(srgb(54, 163, 83), srgb(54, 163, 83))
    static let warning = dynamic(srgb(244, 153, 80), srgb(244, 153, 80))
    static let danger = dynamic(srgb(214, 78, 64), srgb(232, 106, 92))

    /// The secondary text of a row: a duration, a path, a caption.
    static let muted = Color.primary.opacity(0.55)
    static let faint = Color.primary.opacity(0.38)

    // MARK: metrics

    /// The transcript's width. A reading column, not the window: at 1,400 pt a
    /// paragraph of prose is 180 characters wide and the eye loses the line it is
    /// on, which is the one thing a chat of long answers has to get right.
    static let columnWidth: CGFloat = 760
    static let sidebarWidth: CGFloat = 232

    static let radius: CGFloat = 12
    static let radiusSmall: CGFloat = 7

    // MARK: helpers

    private static func srgb(_ white: Int) -> NSColor {
        NSColor(srgbRed: CGFloat(white) / 255, green: CGFloat(white) / 255, blue: CGFloat(white) / 255, alpha: 1)
    }

    private static func srgb(_ red: Int, _ green: Int, _ blue: Int) -> NSColor {
        NSColor(
            srgbRed: CGFloat(red) / 255, green: CGFloat(green) / 255, blue: CGFloat(blue) / 255, alpha: 1
        )
    }

    /// A colour that resolves per appearance.
    ///
    /// `NSColor(name:dynamicProvider:)` is the AppKit way, and it means a view
    /// never has to ask which appearance it is in: the provider runs at draw
    /// time, so a window that moves to an external display in dark mode repaints
    /// without any of these views knowing.
    private static func dynamic(_ light: NSColor, _ dark: NSColor) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
        })
    }
}

/// A hairline border that does not change the view's size.
struct Hairline: ViewModifier {
    var radius: CGFloat = Theme.radius
    var color: Color = Theme.hairline

    func body(content: Content) -> some View {
        content.overlay(
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .strokeBorder(color, lineWidth: 1)
        )
    }
}

extension View {
    func hairline(_ radius: CGFloat = Theme.radius, color: Color = Theme.hairline) -> some View {
        modifier(Hairline(radius: radius, color: color))
    }
}
