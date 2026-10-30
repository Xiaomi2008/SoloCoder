import AgentAppCore
import AgentClient
import AppKit
import SwiftUI

/// The only view that observes the panel's model, so a frame redraws the
/// panel and nothing else — `Detail` passes the model in without observing it.
struct BrowserPanelSlot: View {
    @ObservedObject var panel: BrowserPanelModel
    /// Remembered: a page is unreadable at a third of its size, and how much
    /// of the window to give it is the user's call.
    @AppStorage("openagent.browserPanelWidth") private var width = 560.0
    @State private var dragStart: Double?

    var body: some View {
        if panel.isVisible {
            HStack(spacing: 0) {
                // The left edge is the handle: drag it to resize.
                Rectangle()
                    .fill(Theme.hairline)
                    .frame(width: 1)
                    .padding(.horizontal, 3)
                    .contentShape(Rectangle())
                    .onHover { inside in
                        if inside { NSCursor.resizeLeftRight.push() } else { NSCursor.pop() }
                    }
                    .gesture(
                        DragGesture(minimumDistance: 1)
                            .onChanged { drag in
                                let start = dragStart ?? width
                                dragStart = start
                                width = min(max(start - drag.translation.width, 320), 1100)
                            }
                            .onEnded { _ in dragStart = nil }
                    )
                    .padding(.horizontal, -3)
                BrowserPanelView(panel: panel)
                    .frame(width: width)
            }
            .transition(.move(edge: .trailing))
        }
    }
}

/// The agent's browser, live: what it sees, where it is, and a way to take
/// the wheel.
struct BrowserPanelView: View {
    @EnvironmentObject var model: AppModel
    @ObservedObject var panel: BrowserPanelModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "globe")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.muted)
                VStack(alignment: .leading, spacing: 1) {
                    Text(panel.title.isEmpty ? "Agent's browser" : panel.title)
                        .font(.system(size: 12, weight: .semibold))
                        .lineLimit(1)
                    Text(panel.url)
                        .font(.caption2)
                        .foregroundStyle(Theme.faint)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                }
                Spacer(minLength: 4)
                Button {
                    model.takeOver()
                } label: {
                    Label("Take over", systemImage: "hand.raised")
                }
                .controlSize(.small)
                .help("Stop the agent and bring its browser window forward, so you can act yourself.")
                Button {
                    withAnimation(.easeOut(duration: 0.15)) {
                        panel.dismiss(runId: model.transcript.runId)
                    }
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .controlSize(.small)
                .help("Hide the live view for this run")
            }
            .padding(.horizontal, 12)
            .frame(height: 44)

            Rectangle().fill(Theme.hairline).frame(height: 1)

            if let image = panel.image {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.medium)
                    .aspectRatio(contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.edge, lineWidth: 1))
                    .padding(12)
                    .accessibilityLabel("Live view of \(panel.title.isEmpty ? panel.url : panel.title)")
            }
            Spacer(minLength: 0)
            HStack(spacing: 5) {
                Circle()
                    .fill(model.transcript.state.isRunning ? Theme.success : Theme.faint)
                    .frame(width: 6, height: 6)
                Text(model.transcript.state.isRunning ? "Live — the agent's own browser" : "Last view — the run has ended")
                    .font(.caption2)
                    .foregroundStyle(Theme.faint)
            }
            .padding(12)
        }
        .background(Theme.surface)
    }
}
