import AgentAppCore
import AgentClient
import Foundation

// The permission sheet's rendering of a tool call.
//
// The property these checks are built around is the one the renderer is written
// to guarantee: **no argument is ever dropped.** A field the per-tool table does
// not know, a tool the table does not know, a call whose arguments are not an
// object at all — each must show what was asked for. That is the same rule
// `markdownChecks` runs on the block parser, for the same reason: a rendering
// that quietly omits half of a `bash` command is not a conservative rendering,
// it is a prompt that hides the risk.
//
// The rest of the checks are the shapes the sheet is for: a command shown as a
// command, a file body shown as its head with a caption saying how much is
// missing, an edit shown as a diff.

func toolDisplayChecks(_ checks: Checks) {
    print("tool display")
    checks.note("arguments")

    func object(_ fields: [String: JSONValue]) -> JSONValue { .object(fields) }
    func s(_ text: String) -> JSONValue { .string(text) }

    /// Everything the rendering says, in one string, for "is this in there".
    func rendered(_ sections: [ToolDisplay.Section]) -> String {
        sections.map { "\($0.label)\n\($0.text)" }.joined(separator: "\n")
    }

    // MARK: the command is the decision

    let bash = ToolDisplay.sections(
        tool: "bash",
        arguments: object(["command": s("rm -rf build && npm i"), "timeout": .int(30)])
    )
    checks.equal(bash.first?.label, "command", "bash: the first block is the command")
    checks.equal(bash.first?.text, "rm -rf build && npm i", "bash: the command, not a JSON string")
    checks.expect(!rendered(bash).contains("{"), "bash: no JSON braces in the prompt")
    checks.expect(rendered(bash).contains("timeout 30s"), "bash: the timeout is shown")

    // A multi-line script is the case the escaping hurt most: `cmd -c "a\nb"`
    // on one line is not readable, and the sheet is where it has to be.
    let script = ToolDisplay.sections(
        tool: "bash",
        arguments: object(["command": s("set -e\nmake test\nmake publish")])
    )
    checks.equal(script.first?.text, "set -e\nmake test\nmake publish", "bash: newlines survive")

    // MARK: a file body is its head, and says so

    let big = (1...400).map { "let line\($0) = \($0)" }.joined(separator: "\n")
    let write = ToolDisplay.sections(
        tool: "write",
        arguments: object(["path": s("/tmp/gen.swift"), "content": s(big)])
    )
    checks.equal(write.first?.label, "file", "write: the path first")
    checks.equal(write.first?.text, "/tmp/gen.swift", "write: the path is the value")
    checks.equal(write.dropFirst().first?.label, "content", "write: then the content")
    checks.expect(
        write.dropFirst().first?.text.hasPrefix("let line1 = 1\nlet line2 = 2") ?? false,
        "write: the head of the file"
    )
    checks.expect(
        rendered(write).contains("showing 12 of 400 lines"),
        "write: the caption says how much is not shown"
    )
    checks.expect(!rendered(write).contains("let line400"), "write: the tail is not shown")

    // One 5,000-character line is a real shape — a minified bundle, a base64
    // blob — and a body that renders as nothing is not a rendering.
    let oneLine = ToolDisplay.sections(
        tool: "write",
        arguments: object(["path": s("/tmp/min.js"), "content": s(String(repeating: "a", count: 5_000))])
    )
    let oneLineBody = oneLine.first { $0.label == "content" }
    checks.expect((oneLineBody?.text.utf8.count ?? 0) > 1_000, "write: a single long line is cut, not dropped")
    checks.expect(
        rendered(oneLine).contains("of 5000 characters"),
        "write: the caption counts characters, not only lines"
    )

    // A short file is shown whole, with no caption: a note about a bound that
    // was never applied is noise.
    let small = ToolDisplay.sections(
        tool: "write",
        arguments: object(["path": s("/tmp/a.txt"), "content": s("one\ntwo\n")])
    )
    checks.expect(!rendered(small).contains("showing"), "write: a short file needs no caption")

    // MARK: an edit is a diff

    let edit = ToolDisplay.sections(
        tool: "edit",
        arguments: object([
            "path": s("Parser.swift"), "find": s("index += 1"), "replace": s("index += 1\nskipped = true"),
        ])
    )
    let change = edit.first { $0.label == "change" }
    checks.equal(change?.text, "- index += 1\n+ index += 1\n+ skipped = true", "edit: a two-sided diff")

    // MARK: the read-only tools

    checks.expect(
        rendered(ToolDisplay.sections(
            tool: "read", arguments: object(["path": s("a.swift"), "line_start": .int(10), "line_end": .int(20)])
        )).contains("lines 10–20"),
        "read: the range"
    )
    let grep = ToolDisplay.sections(
        tool: "grep", arguments: object(["pattern": s("readFence"), "path": s(".")])
    )
    checks.equal(grep.first?.text, "readFence", "grep: the pattern first")
    checks.expect(
        rendered(ToolDisplay.sections(tool: "web_fetch", arguments: object(["url": s("https://x.test/y")])))
            .contains("https://x.test/y"),
        "web_fetch: the url"
    )
    checks.expect(
        rendered(ToolDisplay.sections(tool: "type_text", arguments: object(["text": s("git status")])))
            .contains("git status"),
        "type_text: the text it will type"
    )
    checks.expect(
        rendered(ToolDisplay.sections(
            tool: "click", arguments: object(["x": .int(120), "y": .int(40), "button": s("right")])
        )).contains("(120, 40)"),
        "click: the coordinates"
    )

    // MARK: the never-drop property

    // Every scalar argument of every call must appear in the rendering, and
    // every long one must be represented by the caption that says it was cut.
    // The table is the tools this app can be asked about, each with a field the
    // per-tool table does not mention, so the leftovers branch is exercised too.
    let calls: [(String, [String: JSONValue])] = [
        ("bash", ["command": s("ls"), "timeout": .int(5), "working_dir": s("/tmp/oa"), "run_in_background": .bool(true)]),
        ("write", ["path": s("/tmp/a"), "content": s("hi"), "create_parents": .bool(true)]),
        ("edit", ["path": s("/tmp/a"), "find": s("x"), "replace": s("y"), "expected_replacements": .int(2)]),
        ("read", ["path": s("/tmp/a"), "line_start": .int(1), "line_end": .int(9)]),
        ("glob", ["pattern": s("**/*.py"), "path": s("/tmp"), "max_results": .int(50)]),
        ("grep", ["pattern": s("TODO"), "path": s("."), "regex": .bool(true), "context_lines": .int(3)]),
        ("awk", ["pattern": s("{print $1}"), "input_text": s("a b\nc d")]),
        ("sed", ["expression": s("s/a/b/"), "input_text": s("a")]),
        ("notebook_edit", ["path": s("n.ipynb"), "cell_index": .int(3), "new_source": s("print(1)"), "cell_type": s("code")]),
        ("web_search", ["query": s("swift concurrency"), "num_results": .int(5)]),
        ("web_fetch", ["url": s("https://x.test")]),
        ("bash_background", ["command": s("sleep 60"), "working_dir": s("/tmp")]),
        ("bash_output", ["session_id": s("sh1"), "tail_lines": .int(20)]),
        ("kill_shell", ["session_id": s("sh1")]),
        ("ask_user_question", ["question": s("Which one?"), "multi_select": .bool(false)]),
        ("todo_write", ["tasks": .array([.object(["subject": s("fix the fence")])])]),
        ("double_click", ["x": .int(1), "y": .int(2)]),
        ("key_combination", ["keys": s("cmd+q")]),
        ("scroll", ["x": .int(3), "y": .int(4), "clicks": .int(-2)]),
        // A tool this table has never heard of: everything goes to the leftovers.
        ("quantum_leap", ["target": s("q3"), "amplitude": .double(0.5), "qubits": .int(2)]),
    ]

    var dropped: [String] = []
    for (tool, fields) in calls {
        let text = rendered(ToolDisplay.sections(tool: tool, arguments: object(fields)))
        for (key, value) in fields {
            let scalar = value.stringValue ?? value.displayText
            switch value {
            case .string, .int, .double, .bool:
                if scalar.isEmpty { continue }
                if !text.contains(scalar) { dropped.append("\(tool).\(key) = \(scalar)") }
            default:
                // A list or an object: the key name is the trace, from the
                // leftovers block.
                if !text.contains(key) { dropped.append("\(tool).\(key) (structured)") }
            }
        }
    }
    checks.equal(dropped, [], "every argument of every call reaches the rendering")

    // MARK: the shapes that are not an object

    checks.equal(
        ToolDisplay.sections(tool: "bash", arguments: .null),
        [],
        "no arguments: no blocks — the tool name and the reason are the prompt"
    )
    checks.equal(ToolDisplay.sections(tool: "bash", arguments: .object([:])), [], "an empty object: no blocks")
    let notAnObject = ToolDisplay.sections(tool: "bash", arguments: s("rm -rf /"))
    checks.equal(notAnObject.count, 1, "arguments that are not an object are shown, not dropped")
    checks.expect(notAnObject.first?.text.contains("rm -rf /") ?? false, "…with the text the model sent")

    // A known tool with a field the table does not mention: the field shows up.
    let extra = ToolDisplay.sections(
        tool: "bash", arguments: object(["command": s("ls"), "--dry-run": .bool(true)])
    )
    checks.expect(rendered(extra).contains("--dry-run"), "a field the table does not know is still shown")

    // MARK: the bounds

    let (shown, note) = ToolDisplay.head((1...50).map { "line \($0)" }.joined(separator: "\n"))
    checks.equal(shown.components(separatedBy: "\n").count, ToolDisplay.previewLines, "head keeps the preview lines")
    checks.expect(note?.contains("of 50 lines") ?? false, "head reports the bound")
    let (short, noNote) = ToolDisplay.head("one line")
    checks.equal(short, "one line", "a short field is shown whole")
    checks.expect(noNote == nil, "…and says nothing about a bound")
}
