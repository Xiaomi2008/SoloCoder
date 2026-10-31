import AgentAppCore
import AgentAppUI
import AgentClient
import AppKit
import SwiftUI

// The app shell: a chat over the event stream, a tool timeline, a permission
// sheet, a model picker, a session browser.
//
// Everything here renders `AppModel`, and `AppModel` renders the transcript the
// service's events produced. There is no second source of truth for what
// happened in a run — which is why a view can be this thin.

/// A SwiftPM executable is not a bundle, so the process starts as a
/// background-only app: no Dock tile, no menu bar, and a window that opens
/// behind everything with nothing on screen saying anything launched. The user
/// sees a command that returned and no app.
///
/// `.regular` is the line that makes it a normal app; the delayed raise is
/// because SwiftUI has not created the window at `didFinishLaunching`.
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// ⌘V with an image on the pasteboard. The focused `TextField` disables the
    /// system Paste item for an image-only clipboard — it has no text to paste —
    /// so the event is intercepted before the field sees it. A clipboard with
    /// text is left alone: the field's own paste still handles it, with its
    /// cursor position and undo intact. The rule is `ClipboardImage`'s, and only
    /// the composer's ⌘V is taken: a paste in Settings or the search box is theirs.
    private var pasteMonitor: Any?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if SelfTest.requested() {
            setvbuf(stdout, nil, _IONBF, 0)
            print("argv: \(CommandLine.arguments)")
            // The gate reads stdout and an exit code. `.prohibited` keeps the
            // Dock tile and the menu bar away, and closing the windows keeps a
            // window away from whoever is at the screen.
            NSApp.setActivationPolicy(.prohibited)
            for window in NSApp.windows { window.close() }
            Task { @MainActor in
                exit(await SelfTest.run())
            }
            return
        }
        // A process launched from a shell starts prohibited (no Dock tile, no
        // menu bar, no activation). `.regular` is the line that makes it a
        // normal app. The enum is 0=regular, 1=accessory, 2=prohibited — the
        // reverse of what the numbers suggest, and getting it backwards turns a
        // visible app into a background one.
        NSApp.setActivationPolicy(.regular)
        positionAndRaiseWindows(after: 0.2)
        ImagePaste.install()
        pasteMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            guard flags.contains(.command),
                !flags.contains(.shift),
                !flags.contains(.option),
                event.charactersIgnoringModifiers?.lowercased() == "v",
                ImagePaste.composerIsFocused(in: event.window),
                ClipboardImage.shouldPasteAsImage()
            else { return event }
            NotificationCenter.default.post(name: .solocoderPasteImage, object: nil)
            return nil
        }
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        raiseWindows(after: 0.05)
    }

    /// Closing the last window quits the app — except under `--self-test`, where
    /// the windows are closed on purpose and the run is not over.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        !SelfTest.requested()
    }

    /// Position the app's window on the focused screen and raise it.
    ///
    /// This runs once, at launch. SwiftUI restores a window at its saved frame,
    /// which may be on a display the user is not looking at; positioning it at
    /// launch puts it where the focus is. It must not run again on activation,
    /// or a window the user dragged would snap back to the top-center.
    ///
    /// SwiftUI keeps hidden helper windows in `NSApp.windows` — the alert host,
    /// the menu bar's window. Ordering *those* to the front shows them: a small
    /// empty panel on top of everything, which is what a user sees instead of
    /// the app. Titled and visible is the filter.
    private func positionAndRaiseWindows(after delay: TimeInterval) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            let main = NSApp.windows.first { $0.isVisible && !$0.title.isEmpty }
            // `center()` centers on the screen the window is already on, which
            // for a window restored at a saved frame means a display the user is
            // not looking at. Center on the screen with the focus, near the top.
            if let main, let screen = NSScreen.main {
                let visible = screen.visibleFrame
                let origin = CGPoint(
                    x: visible.midX - main.frame.width / 2,
                    y: visible.maxY - main.frame.height - 40
                )
                main.setFrameOrigin(origin)
            }
            main?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    /// Raise the window without moving it. Activation happens every time the
    /// user clicks the app, so this is where a dragged window must stay put.
    private func raiseWindows(after delay: TimeInterval) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            let main = NSApp.windows.first { $0.isVisible && !$0.title.isEmpty }
            main?.makeKeyAndOrderFront(nil)
        }
    }
}

@main
struct SoloCoderApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("SoloCoder") {
            RootView()
                .environmentObject(model)
                // Under `--self-test` the view's model must stay idle: the
                // self-test spawns its own sidecar, and both would use this
                // process's one socket path.
                .task { if !SelfTest.requested() { model.start() } }
        }
        .defaultSize(width: 900, height: 620)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New run") { model.newRun() }
                    .keyboardShortcut("n", modifiers: .command)
                Button("Command Palette") { model.paletteOpen = true }
                    .keyboardShortcut("k", modifiers: .command)
            }
            // The system Paste item is greyed for an image-only pasteboard in a
            // `TextField`. This is the same action, named, with a shortcut that
            // does not collide with text paste.
            CommandGroup(after: .pasteboard) {
                Button("Paste Image") {
                    NotificationCenter.default.post(name: .solocoderPasteImage, object: nil)
                }
                .keyboardShortcut("v", modifiers: [.command, .shift])
            }
        }

        Settings {
            SettingsView()
                .environmentObject(model)
        }
    }
}
