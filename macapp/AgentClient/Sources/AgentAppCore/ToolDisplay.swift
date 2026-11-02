import AgentClient
import Foundation

/// A tool call's arguments, rendered for the person who has to decide.
///
/// The permission sheet used to print `pretty(arguments)`. A `bash` prompt
/// showed `{"command": "rm -rf build && npm i", "timeout": 30}` and a `write`
/// showed the whole file. The question being asked is "may this command run"
/// and "may this file become this", and a JSON object answers neither: the
/// command is inside a quoted string with its newlines escaped as `\n`, and a
/// 400-line file is the sheet, so the part worth reading is off the bottom.
///
/// So the rendering is per tool: the field that carries the risk gets its own
/// labelled block, monospaced, newlines intact.
///
/// The rule that keeps it honest is the markdown parser's rule — **no argument
/// is ever dropped.** A field this module does not know goes into a final
/// `arguments` block as JSON, and an unknown tool shows all of it. A prompt
/// that hides half a command is worse than one that shows all of it badly, and
/// the tool the model invents next month is exactly the case a per-tool table
/// misses. `toolDisplayChecks` asserts the property over every tool this app
/// can be asked about.
public struct ToolDisplay: Equatable, Sendable {
    /// One labelled block of the rendering.
    public struct Section: Equatable, Sendable {
        public enum Style: Equatable, Sendable {
            /// Monospaced: a command, a file body, a pattern.
            case code
            /// Prose the user reads: a question, a list of options.
            case text
            /// A caption: how much was not shown, a size, a range.
            case note
        }

        public let label: String
        public let text: String
        public let style: Style

        public init(label: String, text: String, style: Style = .code) {
            self.label = label
            self.text = text
            self.style = style
        }
    }

    /// The row's one-line label: a verb and its object.
    ///
    /// The row used to show the tool's name and a folded preview of its result —
    /// `read  1. let line = lines[index] ⏎ 2. let line = …` — which names the
    /// operation and then spends the line on the output the disclosure shows
    /// again. What a timeline row has to answer is *what happened*, in the tense
    /// the state is in: `Ran npm test`, `Read Parser.swift`, `Searching the
    /// parser` while it goes.
    ///
    /// The verb is per tool, the object is the field that carries the meaning
    /// (the command, the path, the pattern), and an unknown tool gets its own
    /// name as the object: a tool the model invents next month shows as
    /// `Ran frobnicate`, which is true, rather than nothing.
    ///
    /// Bounded at `summaryKeep` characters: the row is one line, and a command
    /// of 4,000 characters is a disclosure, not a label.
    public static func summary(tool: String, arguments: JSONValue, running: Bool) -> String {
        let fields = arguments.objectValue ?? [:]
        func field(_ key: String) -> String? {
            guard let value = fields[key] else { return nil }
            switch value {
            case .null: return nil
            case .string, .int, .double, .bool: break
            default: return nil
            }
            let text = value.displayText.trimmingCharacters(in: .whitespacesAndNewlines)
            return text.isEmpty ? nil : text
        }
        /// The first line of a multi-line command: the row is one line.
        func firstLine(_ text: String) -> String {
            text.split(whereSeparator: \.isNewline).first.map(String.init) ?? text
        }
        func one(_ verb: String, _ object: String?) -> String {
            guard let object, !object.isEmpty else { return verb }
            return "\(verb) \(object)"
        }
        func path() -> String? { field("path").map(Self.shorten) }

        let out: String
        switch tool {
        case "bash", "bash_background":
            out = one(running ? "Running" : "Ran", field("command").map { firstLine($0) })
        case "read":
            out = one(running ? "Reading" : "Read", path())
        case "write":
            out = one(running ? "Writing" : "Wrote", path())
        case "edit", "notebook_edit":
            out = one(running ? "Editing" : "Edited", path())
        case "glob", "grep", "awk", "sed":
            let verb = running ? "Searching" : "Searched"
            let pattern = field("pattern") ?? field("expression")
            if let path = path(), let pattern {
                out = "\(verb) \(Self.quote(pattern)) in \(Self.shorten(path))"
            } else {
                out = one(verb, pattern.map(Self.quote))
            }
        case "web_fetch":
            out = one(running ? "Fetching" : "Fetched", field("url"))
        case "web_search":
            out = one(running ? "Searching the web for" : "Searched the web for", field("query").map(Self.quote))
        case "bash_output", "kill_shell":
            out = one(running ? "Reading the shell" : "Read the shell", field("session_id"))
        case "ask_user_question":
            out = one(running ? "Asking" : "Asked", field("question").map { Self.quote(firstLine($0)) })
        case "todo_write", "todo_update":
            out = running ? "Updating the task list" : "Updated the task list"
        case "click", "double_click":
            let at = (field("x").flatMap { x in field("y").map { y in "(\(x), \(y))" } }) ?? "the screen"
            out = "\(running ? "Clicking" : "Clicked") \(tool == "double_click" ? "double " : "")\(at)"
        case "move_mouse":
            out = running ? "Moving the pointer" : "Moved the pointer"
        case "scroll":
            out = running ? "Scrolling" : "Scrolled"
        case "type_text":
            out = one(running ? "Typing" : "Typed", field("text").map(Self.quote))
        case "key_combination":
            out = one(running ? "Pressed" : "Pressed", field("keys"))
        case "screenshot", "browser_screenshot":
            out = running ? "Taking a screenshot" : "Took a screenshot"
        case "git_status":
            out = running ? "Checking git status" : "Checked git status"
        case "git_diff":
            out = one(running ? "Diffing" : "Diffed", path() ?? field("ref"))
        case "git_log":
            out = one(running ? "Reading the git log" : "Read the git log", path())
        case "git_commit":
            out = one(running ? "Committing" : "Committed", field("message").map { Self.quote(firstLine($0)) })
        case "browser_navigate":
            out = one(running ? "Opening" : "Opened", field("url"))
        case "browser_back":
            out = running ? "Going back" : "Went back"
        case "browser_snapshot":
            out = running ? "Reading the page" : "Read the page"
        case "browser_find":
            out = one(running ? "Finding" : "Found", field("text").map(Self.quote))
        case "browser_click":
            out = one(running ? "Clicking" : "Clicked", field("ref").map { "element \($0)" })
        case "browser_type":
            out = one(running ? "Typing" : "Typed", field("text").map(Self.quote))
        case "browser_select_option", "browser_press_key":
            out = one(running ? "Using the page" : "Used the page", field("option") ?? field("key"))
        case "browser_close":
            out = running ? "Closing the browser" : "Closed the browser"
        case "recall_output":
            out = one(running ? "Recalling" : "Recalled", field("ref"))
        default:
            out = one(running ? "Running" : "Ran", tool)
        }
        return out.count > summaryKeep ? String(out.prefix(summaryKeep - 1)) + "…" : out
    }

    /// The SF Symbol a step shows for this tool: what kind of thing it did,
    /// readable at a glance down the timeline without reading the labels.
    public static func symbol(tool: String) -> String {
        switch tool {
        case "bash", "bash_background", "bash_output", "kill_shell": return "terminal"
        case "read", "recall_output": return "doc.text"
        case "write": return "square.and.pencil"
        case "edit", "notebook_edit", "awk", "sed": return "pencil"
        case "glob", "grep", "browser_find": return "magnifyingglass"
        case "web_search": return "globe"
        case "web_fetch", "browser_navigate", "browser_back": return "safari"
        case "browser_snapshot": return "doc.richtext"
        case "browser_click", "browser_type", "browser_select_option", "browser_press_key": return "cursorarrow.click"
        case "click", "double_click", "move_mouse", "scroll": return "cursorarrow.click"
        case "type_text", "key_combination": return "keyboard"
        case "screenshot", "browser_screenshot": return "camera.viewfinder"
        case "todo_write", "todo_update", "todo_list": return "checklist"
        case "ask_user_question": return "questionmark.bubble"
        case "git_status", "git_diff", "git_log", "git_commit": return "arrow.triangle.branch"
        case "wait": return "hourglass"
        default: return "wrench.and.screwdriver"
        }
    }

    /// A path with its home abbreviated, and the tail kept: a row that shows
    /// `/Users/dev/Projects/SoloCoder/macapp/AgentClient/Source` is a row that
    /// shows the directory over the file.
    static func shorten(_ path: String) -> String {
        var text = path
        let home = NSHomeDirectory()
        if text.hasPrefix(home) { text = "~" + text.dropFirst(home.count) }
        guard text.count > pathKeep else { return text }
        return "…" + text.suffix(pathKeep - 1)
    }

    /// A quoted fragment of prose — a pattern, a query, a command's first line —
    /// so the object of the verb reads as an object.
    static func quote(_ text: String) -> String {
        "“\(text)”"
    }

    public static let summaryKeep = 90
    static let pathKeep = 46

    /// The most characters a field shows, and the most lines.
    ///
    /// A `write` of a generated file is 400 lines; the sheet is 220 points of
    /// scroll. The head is the part worth keeping — a file starts at the top, a
    /// command starts at the beginning — which is the same choice a tool result
    /// makes (`Transcript.resultText`) and the opposite of the thinking stream's
    /// (`Transcript.reasoningKeep`), where the part that matters is the part
    /// that is arriving.
    public static let fieldKeep = 1_200
    public static let previewLines = 12

    /// The rendering of one tool call.
    ///
    /// - Parameters:
    ///   - tool: the tool name, as the service reports it.
    ///   - arguments: the call's arguments — a JSON object in practice, and
    ///     anything at all in the case of a model that ignores the schema.
    public static func sections(tool: String, arguments: JSONValue) -> [Section] {
        guard let fields = arguments.objectValue else {
            // Not an object: the model sent a string, a list, nothing. Show it
            // rather than an empty sheet — an empty sheet reads as "no risk".
            guard !arguments.displayText.isEmpty else { return [] }
            return [Section(label: "arguments", text: arguments.displayText, style: .code)]
        }
        guard !fields.isEmpty else { return [] }

        var out: [Section] = []
        var used: Set<String> = []

        /// A string field, claimed. Numbers and flags are shown as their text;
        /// an object or a list is not a field of a sentence, so it is left for
        /// the leftovers block, which renders it as JSON.
        func field(_ key: String) -> String? {
            guard let value = fields[key] else { return nil }
            switch value {
            case .null: return nil
            case .string, .int, .double, .bool: break
            default: return nil
            }
            used.insert(key)
            return value.displayText
        }

        func code(_ label: String, _ key: String) {
            guard let text = field(key), !text.isEmpty else { return }
            append(label: label, text: text, style: .code)
        }

        func append(label: String, text: String, style: Section.Style) {
            let (shown, note) = head(text)
            out.append(Section(label: label, text: shown, style: style))
            if let note { out.append(Section(label: "", text: note, style: .note)) }
        }

        /// A file body: the head of it, and a caption saying how much is not
        /// there. The label is on the caption, not on the body, so the body is
        /// the first thing under the file name.
        func body(_ key: String, label: String = "content") {
            guard let text = field(key) else { return }
            append(label: label, text: text, style: .code)
        }

        func file() { code("file", "path") }

        switch tool {
        case "bash", "bash_background":
            // The command is the whole decision. It keeps its newlines: a
            // multi-line script is the case the user most needs to read.
            code("command", "command")
            code("in", "working_dir")
            if let timeout = field("timeout") {
                out.append(Section(label: "", text: "timeout \(timeout)s", style: .note))
            }

        case "write":
            file()
            body("content")

        case "edit":
            file()
            // A two-sided diff, not two blobs. The question is what changes, and
            // a 40-line `find` above a 40-line `replace` asks the user to spot
            // the difference between them.
            if let find = field("find"), let replace = field("replace") {
                append(label: "change", text: diff(find: find, replace: replace), style: .code)
            } else {
                code("find", "find")
                code("replace", "replace")
            }
            if let count = field("expected_replacements"), count != "1" {
                out.append(Section(label: "", text: "expects \(count) replacements", style: .note))
            }

        case "notebook_edit":
            file()
            if let index = field("cell_index") {
                out.append(Section(label: "", text: "cell \(index)", style: .note))
            }
            body("new_source", label: "new source")

        case "awk", "sed":
            code("expression", "expression")
            code("pattern", "pattern")
            body("input_text", label: "input")

        case "read":
            file()
            if let start = field("line_start"), let end = field("line_end") {
                out.append(Section(label: "", text: "lines \(start)–\(end)", style: .note))
            }

        case "glob", "grep":
            code("pattern", "pattern")
            code("in", "path")

        case "web_fetch":
            code("url", "url")

        case "web_search":
            code("query", "query")

        case "bash_output", "kill_shell":
            code("shell", "session_id")

        case "ask_user_question":
            if let question = field("question") {
                out.append(Section(label: "question", text: question, style: .text))
            }
            if let options = fields["options"]?.arrayValue, !options.isEmpty {
                used.insert("options")
                let lines = options.map { "  • \($0.displayText)" }.joined(separator: "\n")
                append(label: "options", text: lines, style: .text)
            }

        case "click", "double_click", "move_mouse", "scroll":
            // The coordinates are in screenshot pixels, which is the scale the
            // user is looking at, so they are shown as given.
            if let x = field("x"), let y = field("y") {
                out.append(Section(label: "at", text: "(\(x), \(y))", style: .text))
            }
            code("button", "button")

        case "type_text":
            code("text", "text")

        case "key_combination":
            code("keys", "keys")

        case "todo_write", "todo_update":
            // A task list is a list of objects; the count is the useful line and
            // the JSON is the detail. Left to the leftovers block.
            break

        default:
            break
        }

        // Everything not claimed, as JSON. This is the branch that makes the
        // table safe: a tool that is not in it, and a known tool with a field
        // the table does not mention, both show the rest.
        let left = fields.keys.filter { !used.contains($0) }.sorted()
        if !left.isEmpty {
            out.append(
                Section(label: "arguments", text: pretty(fields, keeping: left), style: .code)
            )
        }
        return out
    }

    /// The head of a long field, and the caption saying how much is missing.
    public static func head(_ text: String) -> (String, String?) {
        let lines = text.components(separatedBy: "\n")
        let bytes = text.utf8.count
        guard bytes > fieldKeep || lines.count > previewLines else { return (text, nil) }

        var kept: [String] = []
        var taken = 0
        for line in lines {
            if kept.count == previewLines { break }
            let cost = line.utf8.count + 1  // the newline
            if taken + cost > fieldKeep { break }
            kept.append(line)
            taken += cost
        }
        if kept.isEmpty, let first = lines.first {
            // One line longer than the bound: cut it in characters. A field that
            // is a single 5,000-character line is a real shape (a minified file,
            // a base64 blob), and an empty body with a caption is not a rendering.
            kept = [String(first.prefix(fieldKeep))]
            taken = fieldKeep
        }
        let note = "showing \(kept.count) of \(lines.count) lines, \(taken) of \(bytes) characters"
        return (kept.joined(separator: "\n"), note)
    }

    /// A two-sided diff of an `edit`.
    ///
    /// Not a real diff: computing the longest common subsequence of two blobs
    /// the user is about to approve is more machinery than the decision needs,
    /// and a "diff" that is subtly wrong about which lines changed is worse than
    /// an honest "this, then that". So: every removed line, every added line,
    /// in order, prefixed.
    static func diff(find: String, replace: String) -> String {
        let removed = find.components(separatedBy: "\n").map { "- \($0)" }
        let added = replace.components(separatedBy: "\n").map { "+ \($0)" }
        return (removed + added).joined(separator: "\n")
    }

    /// The JSON of the keys the per-tool table did not claim.
    private static func pretty(_ fields: [String: JSONValue], keeping keys: [String]) -> String {
        var kept: [String: JSONValue] = [:]
        for key in keys { kept[key] = fields[key] }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(kept), let text = String(data: data, encoding: .utf8) else {
            return JSONValue.object(kept).displayText
        }
        // The braces of a one-key object are noise in a caption-sized block.
        let (shown, _) = head(text)
        return shown
    }
}
