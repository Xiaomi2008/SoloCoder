import AgentClient
import Foundation

/// What a session is doing, for its row in the sidebar.
public enum SessionActivity: Equatable, Sendable {
    case running
    case needsApproval
    case inBackground(Int)
    /// A run ended while the session was not on screen, and it has not been seen.
    case finished
    case failed

    public var label: String {
        switch self {
        case .running: "Working…"
        case .needsApproval: "Needs your approval"
        case .inBackground(let count): count == 1 ? "1 task in background" : "\(count) tasks in background"
        case .finished: "Finished"
        case .failed: "Failed"
        }
    }

    /// A run or task is still going, so the session cannot be deleted.
    public var isLive: Bool {
        switch self {
        case .running, .needsApproval, .inBackground: true
        case .finished, .failed: false
        }
    }
}

/// One row of the sidebar: a stored session, or a live one not on disk yet (the
/// service writes a session when its run ends).
public struct SidebarEntry: Identifiable, Equatable, Sendable {
    public let id: String
    public let row: SessionRow
    public let activity: SessionActivity?
    public var isSaved: Bool { !row.path.isEmpty }
}

/// Words sent while a run was going, until they reach the model. `steerId`
/// is the service's id once `run.steer` took them; `refused` means the run had
/// already decided to end, so they go out as the next prompt instead.
public struct PendingSteer: Identifiable, Equatable, Sendable {
    public let id = UUID()
    public let text: String
    public let images: [String]
    public var steerId: String?
    public var refused = false

    public init(text: String, images: [String] = [], steerId: String? = nil) {
        self.text = text
        self.images = images
        self.steerId = steerId
    }

    /// Take the one an event delivered: by id, or by its words while the
    /// `run.steer` answer is still on its way.
    static func take(_ delivered: SteeringDelivered, from list: inout [PendingSteer]) -> PendingSteer? {
        let index =
            list.firstIndex { $0.steerId == delivered.steerId }
            ?? list.firstIndex { $0.steerId == nil && $0.text == delivered.text }
        return index.map { list.remove(at: $0) }
    }
}

/// What a session's next automatic run uses. Fixed when the session leaves the
/// screen, because the toolbar then belongs to whichever session is showing.
struct RunSettings: Equatable, Sendable {
    var workingDir: String
    var model: String
    var permissionMode: PermissionMode
    var reasoningEffort: ReasoningEffort?
}

/// A session that is not on screen and still matters. The one on screen lives in
/// `AppModel`'s published properties, and trades places with a slot on a switch.
struct SessionSlot: Sendable {
    let id: UUID
    var sessionId: String?
    var transcript: Transcript
    var settings: RunSettings
    var contextUsed: Int?
    var compactAt: Int?
    var lastStats: MessageStats?
    var runContextWindow: Int?
    var promptToAnswer: PermissionRequest?
    var busy = false
    var backgroundTasks: [BackgroundTaskRow] = []
    var pendingSteers: [PendingSteer] = []
    var wakeDue = false
    /// How a run ended out of sight, until the session is opened.
    var unseen: SessionActivity?

    var key: String { sessionId ?? id.uuidString }

    var activity: SessionActivity? {
        Self.activity(
            prompt: promptToAnswer != nil, busy: busy || transcript.state.isRunning,
            background: backgroundTasks.count, unseen: unseen
        )
    }

    /// Dropped otherwise: a finished, seen session reloads from disk.
    var isWorthKeeping: Bool { wakeDue || activity != nil }

    static func activity(prompt: Bool, busy: Bool, background: Int, unseen: SessionActivity?) -> SessionActivity? {
        if prompt { return .needsApproval }
        if busy { return .running }
        if background > 0 { return .inBackground(background) }
        return unseen
    }
}

extension Transcript {
    /// The first thing the person asked, which names a session not yet saved.
    var firstPrompt: String? {
        for line in lines where !line.automatic {
            if case .user(let text) = line.kind { return text }
        }
        return nil
    }

    var promptCount: Int {
        lines.filter { if case .user = $0.kind { return !$0.automatic } else { return false } }.count
    }
}
