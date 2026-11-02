import AgentAppCore
import AppKit
import Combine
import SwiftUI

/// "SoloCoder is using your computer — Show · Stop", floating at the top of
/// the screen while computer use has hidden the app.
///
/// The computer-use tools hide the app before they look or act, so its window
/// neither covers what the agent sees nor takes its typing. Hidden with
/// nothing else on screen, a working run looked like the app had quit. The
/// pill is a non-activating panel: it stays up while the app is hidden
/// (`canHide = false`), clicking it does not pull focus from the app the agent
/// is driving, and it is left out of screen captures (`sharingType = .none`),
/// so the agent's screenshots do not show it.
@MainActor
public final class WorkingPill {
    private let model: AppModel
    private var panel: NSPanel?
    private var observers: [NSObjectProtocol] = []
    private var running: AnyCancellable?
    private var isRunning = false

    public init(model: AppModel) {
        self.model = model
        let center = NotificationCenter.default
        for name in [NSApplication.didHideNotification, NSApplication.didUnhideNotification] {
            observers.append(
                center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated { self?.update() }
                }
            )
        }
        // `$transcript` fires before the property changes, so the new value is
        // read from the publisher, not from the model.
        running = model.$transcript
            .map(\.state.isRunning)
            .removeDuplicates()
            .sink { [weak self] running in
                self?.isRunning = running
                self?.update()
            }
    }

    private func update() {
        if NSApp.isHidden && isRunning {
            present()
        } else {
            panel?.orderOut(nil)
        }
    }

    private func present() {
        let panel = self.panel ?? makePanel()
        self.panel = panel
        if let screen = NSScreen.main {
            let size = panel.contentView?.fittingSize ?? panel.frame.size
            panel.setContentSize(size)
            let visible = screen.visibleFrame
            panel.setFrameOrigin(CGPoint(x: visible.midX - size.width / 2, y: visible.maxY - size.height - 10))
        }
        panel.orderFrontRegardless()
    }

    private func makePanel() -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 44),
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: true
        )
        panel.level = .floating
        panel.isFloatingPanel = true
        panel.canHide = false
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.sharingType = .none
        panel.isMovableByWindowBackground = true
        panel.contentView = NSHostingView(rootView: WorkingPillView().environmentObject(model))
        return panel
    }
}

struct WorkingPillView: View {
    @EnvironmentObject var model: AppModel
    @State private var pulse = false

    /// The latest tool call, "Took a screenshot" or "Running: open -a …";
    /// "Thinking…" between calls.
    private var activity: String {
        for line in model.transcript.lines.reversed() {
            if case .tool(let row) = line.kind {
                return ToolDisplay.summary(tool: row.name, arguments: row.arguments, running: row.state == .running)
            }
        }
        return "Thinking…"
    }

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(Theme.accent)
                .frame(width: 8, height: 8)
                .opacity(pulse ? 0.35 : 1)
                .animation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true), value: pulse)
                .onAppear { pulse = true }
            VStack(alignment: .leading, spacing: 1) {
                Text("SoloCoder is using your computer")
                    .font(.system(size: 12, weight: .semibold))
                Text(activity)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .frame(maxWidth: 240, alignment: .leading)
            Button("Show") { AppModel.revealIfHidden() }
                .buttonStyle(PillButtonStyle(prominent: false))
                .help("Bring SoloCoder back; the run keeps going")
            Button("Stop") { model.cancel() }
                .buttonStyle(PillButtonStyle(prominent: true))
                .help("Stop the run")
        }
        .padding(.leading, 14)
        .padding(.trailing, 8)
        .padding(.vertical, 7)
        .background(.regularMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(Color.primary.opacity(0.1), lineWidth: 1))
        .fixedSize()
        .padding(6)  // room for the shadow inside the borderless panel
    }
}

private struct PillButtonStyle: ButtonStyle {
    let prominent: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 11, weight: .medium))
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .foregroundStyle(prominent ? Color.white : Color.primary)
            .background(
                Capsule().fill(prominent ? Theme.danger : Color.primary.opacity(0.08))
            )
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}
