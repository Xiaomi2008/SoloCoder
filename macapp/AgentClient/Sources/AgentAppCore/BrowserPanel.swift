import AgentClient
import AppKit
import Combine
import Foundation

/// The agent's browser, live, beside the chat.
///
/// Its own `ObservableObject`, not fields on `AppModel`: frames arrive up to
/// four times a second, and every `AppModel` publish re-runs the toolbar, the
/// pickers and every visible transcript line. Only the panel observes this.
@MainActor
public final class BrowserPanelModel: ObservableObject {
    @Published public private(set) var image: NSImage?
    @Published public private(set) var url = ""
    @Published public private(set) var title = ""
    /// Whether the panel is on screen. It opens on a run's first frame.
    @Published public private(set) var isVisible = false
    /// How many frames were shown. Not `@Published`; the checks read it.
    public private(set) var framesShown = 0

    /// The run whose panel the user closed. Its later frames stay hidden; the
    /// next run's first frame opens the panel again.
    private var dismissedRunId: String?

    public init() {}

    public func show(_ frame: BrowserFrame) {
        guard let data = Data(base64Encoded: frame.data), let picture = NSImage(data: data) else { return }
        image = picture
        url = frame.url
        title = frame.title
        framesShown += 1
        if frame.runId != dismissedRunId, !isVisible {
            isVisible = true
        }
    }

    /// The user closed the panel; it stays closed for the rest of this run.
    public func dismiss(runId: String?) {
        dismissedRunId = runId
        isVisible = false
    }

    /// The bundle Playwright's Chromium runs as.
    static let chromiumBundleIds: Set<String> = ["com.google.chrome.for.testing", "org.chromium.Chromium"]

    /// Raise the agent's real browser window over this app.
    ///
    /// Playwright's `bring_to_front` selects the tab, but macOS does not let a
    /// background process put its window over the frontmost app; the app has
    /// to activate it.
    static func activateAgentBrowser(pid: Int?) -> Bool {
        // By pid when the service knows it: with the "chrome" channel the
        // agent's browser shares Google Chrome's bundle id with the user's own
        // windows, and activating by bundle would raise those too.
        if let pid, pid > 0, let app = NSRunningApplication(processIdentifier: pid_t(pid)) {
            return app.activate()
        }
        let apps = NSWorkspace.shared.runningApplications.filter {
            chromiumBundleIds.contains($0.bundleIdentifier ?? "")
        }
        for app in apps {
            app.activate()
        }
        return !apps.isEmpty
    }
}
