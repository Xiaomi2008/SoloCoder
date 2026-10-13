import AgentAppCore
import AgentAppUI
import AgentClient
import AppKit
import SwiftUI

// A window on the views, with a transcript fed in by hand.
//
// The views are the only code no automated check reaches, and until now the only
// way to see them was to launch the app: spawn the service, connect, choose a
// working directory through a panel, and get a real model to produce an answer
// shaped like the one you are trying to render. That is a ten-minute round trip
// for a change to a row, and it cannot show the cases a model may not produce
// today — a failed tool, a refused permission, a 40-line result, a table.
//
// So this target builds the events those cases produce, feeds them through the
// real `AppModel` (the real state machine, the real delta buffer, the real
// `Transcript`), and opens the real `RootView` on the result. What is on screen
// is what the service's events say, not a drawing of it.
//
//   swift run ui-preview
//
// It spawns no service and needs no model. `solocoder` is the app; this is the
// screen.

private func event(_ fields: [String: JSONValue]) -> AgentEvent? {
    guard let data = try? JSONEncoder().encode(fields) else { return nil }
    return try? AgentEvent.decode(from: data)
}

private func object(_ fields: [String: JSONValue]) -> JSONValue { .object(fields) }

/// The answer a coding agent writes: headings, prose with inline runs, a fenced
/// block, both list kinds, a table, a quote, a rule.
private let answer = """
    I read the parser and found the bug. Two things were dropping text.

    ## Cause

    The fence reader returned early, so an **unterminated** fence lost its body,
    and the list reader wrote `items[i]` back through a `let`, which copies the
    whole string — see `Transcript.appendDelta`.

    ```swift
    private func readFence(_ lines: [String], from start: Int) -> (String, Int) {
        var body: [String] = []
        var index = start + 1
        while index < lines.count {
            // An unterminated fence is the common case, not the edge case.
            body.append(lines[index])
            index += 1
        }
        return (body.joined(separator: "\\n"), index)
    }
    ```

    **The fix**

    1. keep the tail, not the head, for the thinking stream
    2. bound the string, not the idiom — the copy is the cost
    3. parse markdown once, on the finished line

    | file | lines | why |
    | --- | --- | --- |
    | Markdown.swift | 240 | the block parser |
    | Transcript.swift | 40 | the result is kept, not previewed |

    > The answer is uncapped: it is the product.
    """

private let toolOutput = (1...40).map { "  \($0). let line = lines[index]" }.joined(separator: "\n")

/// The events of a run that shows every row kind the views have.
///
/// `UI_PREVIEW_SHORT=1` stops after the answer, so a capture of a 600-point
/// window shows the top of the timeline — the thinking line and the start of the
/// answer — instead of only its tail.
private func script() -> [AgentEvent] {
    var events: [AgentEvent] = []
    var dropped: [String] = []

    func add(_ fields: [String: JSONValue]) {
        if let event = event(fields) {
            events.append(event)
        } else {
            // A malformed event here is invisible on screen: the row it should
            // produce is simply not there, and a spinner that never stops is the
            // only sign. Say what was dropped.
            dropped.append(fields["type"]?.stringValue ?? "?")
        }
    }

    add(["type": .string("run_started"), "run_id": .string("r1")])

    // The thinking, then the answer it produced.
    add([
        "type": .string("message_started"), "run_id": .string("r1"), "message_id": .string("m1"),
    ])
    for chunk in ["Looking at the parser first", " — the fence reader is the suspect", ". Two bugs."] {
        add([
            "type": .string("reasoning_delta"), "run_id": .string("r1"), "message_id": .string("m1"),
            "delta": .string(chunk),
        ])
    }
    add([
        "type": .string("message_completed"), "run_id": .string("r1"), "message_id": .string("m1"),
        "output_text": .string(answer),
    ])

    // A tool that worked, with output long enough to need the disclosure.
    add([
        "type": .string("tool_call_started"), "run_id": .string("r1"), "message_id": .string("m1"),
        "tool_call_id": .string("t1"), "tool_name": .string("read"),
        "arguments": object(["path": .string("/Users/dev/Parser.swift"), "limit": .int(40)]),
    ])
    add([
        "type": .string("tool_call_completed"), "run_id": .string("r1"), "message_id": .string("m1"),
        "tool_call_id": .string("t1"), "tool_name": .string("read"), "result": .string(toolOutput),
    ])

    // A permission asked, granted for the session — the row the sheet leaves.
    add([
        "type": .string("permission_requested"), "run_id": .string("r1"), "request_id": .string("p1"),
        "tool_name": .string("bash"), "reason": .string("exec: runs a shell command"),
        "arguments": object(["command": .string("swift run agent-checks")]),
    ])
    add([
        "type": .string("permission_resolved"), "run_id": .string("r1"), "request_id": .string("p1"),
        "granted": .bool(true), "scope": .string("session"),
        "reason": .string("approved by the user for this session"),
    ])

    // A tool that failed, a compaction, and a refusal that made no tool row.
    add([
        "type": .string("tool_call_started"), "run_id": .string("r1"), "message_id": .string("m1"),
        "tool_call_id": .string("t2"), "tool_name": .string("grep"),
        "arguments": object(["pattern": .string("readFence"), "path": .string(".")]),
    ])
    add([
        "type": .string("tool_call_failed"), "run_id": .string("r1"), "message_id": .string("m1"),
        "tool_call_id": .string("t2"), "tool_name": .string("grep"),
        "error": .string("grep: ./build: No such file or directory"),
    ])
    add([
        "type": .string("context_compaction_started"), "run_id": .string("r1"),
        "reason": .string("context exceeded the window"),
    ])
    add([
        "type": .string("context_compaction_completed"), "run_id": .string("r1"),
        "reason": .string("context exceeded the window"),
    ])
    add([
        "type": .string("permission_requested"), "run_id": .string("r1"), "request_id": .string("p2"),
        "tool_name": .string("write"), "reason": .string("write: replaces a file"),
        "arguments": object(["path": .string("/etc/hosts"), "content": .string("127.0.0.1 evil")]),
    ])
    add([
        "type": .string("permission_resolved"), "run_id": .string("r1"), "request_id": .string("p2"),
        "granted": .bool(false), "scope": .string("once"), "reason": .string("user refused"),
    ])

    // A prompt left unanswered, for the sheet preview. The sheet is the one view
    // the scripted stream cannot otherwise show: it is presented while a
    // `permission_requested` is pending, and every prompt this script makes it
    // also answers. So the prompt is made worth reading — a multi-line command
    // with a timeout, a 30-line file — and the stream stops there.
    // One prompt, left unanswered, for the sheet preview. The sheet is the one
    // view the scripted stream cannot otherwise show: it is presented while a
    // `permission_requested` is pending, and every prompt this script makes it
    // also answers. So the prompt is made worth reading — a multi-line command
    // with a timeout, a 30-line file — and the stream stops there.
    //
    // Exactly one, not both: `.sheet(item:)` presents the first pending request
    // and keeps its content, so a stream with two prompts shows the first one
    // and a preview of the `write` sheet would be a picture of the `bash` one.
    let sheet = ProcessInfo.processInfo.environment["UI_PREVIEW_SHEET"]
    switch sheet {
    case "write":
        add([
            "type": .string("permission_requested"), "run_id": .string("r1"), "request_id": .string("p4"),
            "tool_name": .string("write"), "reason": .string("write: modifies files in the session directory"),
            "arguments": object([
                "path": .string("/Users/dev/Projects/SoloCoder/scripts/release.sh"),
                "content": .string(
                    (1...30).map { "echo step \($0) — building the release" }.joined(separator: "\n")
                ),
            ]),
        ])
    case .some:
        add([
            "type": .string("permission_requested"), "run_id": .string("r1"), "request_id": .string("p3"),
            "tool_name": .string("bash"), "reason": .string("exec: runs arbitrary commands"),
            "arguments": object([
                "command": .string("set -euo pipefail\nrm -rf .build\nswift build -c release\nswift run agent-checks"),
                "timeout": .int(120),
                "working_dir": .string("/Users/dev/Projects/SoloCoder"),
            ]),
        ])
    case nil:
        break
    }

    // An event this client has never heard of, and the answer after it.
    add(["type": .string("quantum_leap"), "run_id": .string("r1"), "why": .string("forward compatibility")])
    add(["type": .string("message_started"), "run_id": .string("r1"), "message_id": .string("m2")])
    for chunk in ["Both fixes are in; ", "**235 checks pass**."] {
        add([
            "type": .string("message_delta"), "run_id": .string("r1"), "message_id": .string("m2"),
            "delta": .string(chunk),
        ])
    }
    add([
        "type": .string("message_completed"), "run_id": .string("r1"), "message_id": .string("m2"),
        "output_text": .string("Both fixes are in; **235 checks pass**."),
    ])
    add([
        "type": .string("run_completed"), "run_id": .string("r1"), "final_message_id": .string("m2"),
        "result": object([
            "run_id": .string("r1"), "final_message_id": .string("m2"),
            "output_text": .string("done"), "stop_reason": .string("end_turn"),
        ]),
    ])
    if !dropped.isEmpty, let data = "ui-preview: events dropped (malformed): \(dropped)\n".data(using: .utf8) {
        FileHandle.standardError.write(data)
    }
    // The sheet preview stops at the prompt and leaves it pending, so the real
    // state machine presents the real sheet. No terminal event: a finished run
    // would clear `pendingPermission` and the sheet would never open.
    if let sheet = ProcessInfo.processInfo.environment["UI_PREVIEW_SHEET"] {
        let wanted = sheet == "write" ? "p4" : "p3"
        if let index = events.firstIndex(where: {
            if case .permissionRequested(let e) = $0 { return e.requestId == wanted }
            return false
        }) {
            return Array(events.prefix(index + 1))
        }
    }

    // Short mode stops after the answer, and keeps the terminal event: a preview
    // that ends mid-run shows "Streaming" and a Cancel button, which reads as a
    // hang and is not a thing anyone is looking for in a screenshot.
    let short = ProcessInfo.processInfo.environment["UI_PREVIEW_SHORT"] != nil
    guard short else { return events }
    var trimmed = Array(events.prefix(6))
    if let last = events.last, last.isTerminal { trimmed.append(last) }
    return trimmed
}

/// A process launched from a shell starts `.prohibited`: no Dock tile, no menu
/// bar, a window behind everything. Same line the app needs.
final class PreviewDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            NSApp.windows.first { $0.isVisible && !$0.title.isEmpty }?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}

struct UIPreviewApp: App {
    @NSApplicationDelegateAdaptor(PreviewDelegate.self) private var delegate
    @StateObject private var model: AppModel = {
        let model = AppModel()
        model.connected = true
        model.status = "openagent 0.4 · protocol 1 (preview, no service)"
        model.workingDir = "/Users/dev/Projects/SoloCoder"
        model.modelPolicy = "local_first"
        model.transcript.addUserMessage("Find and fix the bug in the fence parser.")
        model.ingest(AsyncStream { continuation in
            for event in script() { continuation.yield(event) }
            continuation.finish()
        })
        return model
    }()

    var body: some Scene {
        WindowGroup("SoloCoder preview") {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 780, idealWidth: 900, minHeight: 560, idealHeight: 700)
        }
    }
}
