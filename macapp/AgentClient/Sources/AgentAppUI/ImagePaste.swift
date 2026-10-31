import AgentAppCore
import AppKit
import ObjectiveC
import SwiftUI

/// Paste an image into the composer from wherever Paste is: ⌘V, the Edit menu,
/// and the field's own right-click menu.
///
/// The composer's text is edited by an `NSTextView` (the window's field editor),
/// and that view validates Paste against the text types it can read. An
/// image-only clipboard has none, so its context-menu Paste is grey — and a
/// SwiftUI `onPasteCommand` never gets asked, because the text view answers
/// `paste:` first. The only place to change that answer is the text view, so
/// its validation and `paste:` are exchanged for versions that, in the composer
/// and with an image on the clipboard, enable the item and attach the image.
/// Everywhere else — the sidebar search, Settings, any other field — they are
/// the originals.
public enum ImagePaste {
    /// The composer's field, marked by `ComposerAnchor`. Weak: the composer
    /// comes and goes with the window.
    @MainActor static weak var anchor: NSView?

    /// Whether `view` is the composer's text, or inside it.
    @MainActor static func isComposer(_ view: NSView) -> Bool {
        guard let anchor, let window = anchor.window, view.window === window else { return false }
        let field = anchor.convert(anchor.bounds, to: nil)
        return field.intersects(view.convert(view.bounds, to: nil))
    }

    /// Whether the key window's focused text is the composer's — the ⌘V
    /// monitor's gate, so a paste in Settings or the search box stays theirs.
    @MainActor public static func composerIsFocused(in window: NSWindow?) -> Bool {
        guard let view = window?.firstResponder as? NSView else { return false }
        return isComposer(view)
    }

    /// Exchange the text view's paste handling. Idempotent; call once at launch.
    @MainActor public static func install() {
        guard !installed else { return }
        installed = true
        let cls: AnyClass = NSTextView.self
        exchange(cls, #selector(NSTextView.validateMenuItem(_:)), #selector(NSTextView.solo_validateMenuItem(_:)))
        exchange(
            cls,
            #selector(NSTextView.validateUserInterfaceItem(_:)),
            #selector(NSTextView.solo_validateUserInterfaceItem(_:))
        )
        exchange(cls, #selector(NSTextView.paste(_:)), #selector(NSTextView.solo_paste(_:)))
    }

    @MainActor private static var installed = false

    /// Add the original as an override first, so an inherited method is
    /// exchanged on `NSTextView` and not on its superclass.
    private static func exchange(_ cls: AnyClass, _ original: Selector, _ replacement: Selector) {
        guard let originalMethod = class_getInstanceMethod(cls, original),
            let replacementMethod = class_getInstanceMethod(cls, replacement)
        else { return }
        class_addMethod(cls, original, method_getImplementation(originalMethod), method_getTypeEncoding(originalMethod))
        guard let ownOriginal = class_getInstanceMethod(cls, original) else { return }
        method_exchangeImplementations(ownOriginal, replacementMethod)
    }
}

extension NSTextView {
    /// In the composer, with an image to paste.
    @MainActor fileprivate var pastesImage: Bool {
        ImagePaste.isComposer(self) && ClipboardImage.shouldPasteAsImage()
    }

    // After `install`, each of these names the original implementation, so the
    // calls that look recursive are the calls through to AppKit.

    @objc fileprivate func solo_validateMenuItem(_ item: NSMenuItem) -> Bool {
        if item.action == #selector(paste(_:)), MainActor.assumeIsolated({ pastesImage }) { return true }
        return solo_validateMenuItem(item)
    }

    @objc fileprivate func solo_validateUserInterfaceItem(_ item: any NSValidatedUserInterfaceItem) -> Bool {
        if item.action == #selector(paste(_:)), MainActor.assumeIsolated({ pastesImage }) { return true }
        return solo_validateUserInterfaceItem(item)
    }

    @objc fileprivate func solo_paste(_ sender: Any?) {
        if MainActor.assumeIsolated({ pastesImage }) {
            NotificationCenter.default.post(name: .solocoderPasteImage, object: nil)
            return
        }
        solo_paste(sender)
    }
}

/// Marks the composer's field for `ImagePaste`: a zero-cost view laid out
/// behind the field, whose frame is the field's.
struct ComposerAnchor: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        ImagePaste.anchor = view
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        ImagePaste.anchor = nsView
    }
}
