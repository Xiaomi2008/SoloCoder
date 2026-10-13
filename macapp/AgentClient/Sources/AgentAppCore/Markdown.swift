import Foundation

// The block structure of a markdown answer.
//
// The model answers in markdown and a plain `Text` shows the markup: `## Plan`,
// `**bold**`, and a fenced block whose alignment depends on leading spaces all
// render as literal characters. So the answer needs block structure before it
// needs typography.
//
// Blocks are parsed here, in the core, and inline runs (`**bold**`, `` `code` ``,
// a link) are left to `AttributedString(markdown:)` in the view. That split is
// deliberate: the block rules are where the answers actually break, they are
// testable without a screen, and a fenced block must not be run through an inline
// parser — a code sample containing `**` or `_` would lose characters to it.
//
// This is not CommonMark. It is the subset a coding agent emits, and where the
// real grammar and this one differ, the difference is in a comment. The rule
// that governs the whole file: **no input line is ever dropped.** A parser that
// mis-classifies a line must render it as text, because the answer is the
// product and a dropped line is a lie about what the model said.

// MARK: - Blocks

/// One numbered item: the model's own marker and its text.
///
/// A struct, not a tuple: Swift does not synthesize `Equatable` for an enum whose
/// payload is a tuple, and this enum is compared by the checks.
public struct MarkdownItem: Equatable, Sendable {
    public var label: String
    public var text: String

    public init(label: String, text: String) {
        self.label = label
        self.text = text
    }
}

public enum MarkdownBlock: Equatable, Sendable {
    /// `# Title` … `###### Title`.
    case heading(level: Int, text: String)
    /// Consecutive plain lines, joined with their newlines kept. A model uses one
    /// newline inside a paragraph to mean a line break, and joining with spaces
    /// would move a list of paths onto one line.
    case paragraph(String)
    /// A fenced block, verbatim: leading spaces, `**`, and indentation survive.
    /// `language` is the fence's info string, which is what the model called the
    /// block, not a lexer name.
    case code(language: String?, text: String)
    /// `- item`, `* item`, `+ item`. A continuation line joins the item above it.
    case bullets([String])
    /// `1. item`, `1) item`, with the model's own number kept: a list that starts
    /// at 5 means 5, and renumbering it to 1 changes what it says.
    case numbered([MarkdownItem])
    /// `> quoted`, markers stripped.
    case quote([String])
    /// A pipe table: the first row is the header, the `---` row is dropped.
    case table(rows: [[String]])
    /// `---`, `***`, `___`.
    case rule
}

// MARK: - Parser

public enum Markdown {
    /// The blocks of one answer, in order.
    public static func blocks(_ text: String) -> [MarkdownBlock] {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        var blocks: [MarkdownBlock] = []
        var index = 0

        while index < lines.count {
            let line = lines[index]
            if isBlank(line) {
                index += 1
                continue
            }

            // Fence first: a fenced block can contain anything, including the
            // markers every other rule here keys on.
            if let fence = fenceOpener(line) {
                let (body, next) = readFence(lines, from: index, marker: fence)
                blocks.append(.code(language: fence.language, text: body))
                index = next
                continue
            }

            if isRule(line) {
                blocks.append(.rule)
                index += 1
                continue
            }

            if let heading = headingOf(line) {
                blocks.append(heading)
                index += 1
                continue
            }

            if isQuote(line) {
                var quoted: [String] = []
                while index < lines.count, isQuote(lines[index]) {
                    quoted.append(stripQuote(lines[index]))
                    index += 1
                }
                blocks.append(.quote(quoted))
                continue
            }

            // A table is a header row plus its `---` delimiter row. Both have to
            // be here, because a bare `|` line is a model writing a shell pipe.
            if index + 1 < lines.count, isTableRow(line), isTableDelimiter(lines[index + 1]) {
                var rows: [[String]] = [cells(of: line)]
                index += 2
                while index < lines.count, isTableRow(lines[index]) {
                    rows.append(cells(of: lines[index]))
                    index += 1
                }
                blocks.append(.table(rows: rows))
                continue
            }

            if isBullet(line) {
                let (items, next) = readList(lines, from: index, numbered: false)
                blocks.append(.bullets(items.map(\.text)))
                index = next
                continue
            }

            if isNumbered(line) {
                let (items, next) = readList(lines, from: index, numbered: true)
                blocks.append(.numbered(items))
                index = next
                continue
            }

            var paragraph: [String] = []
            while index < lines.count, !isBlank(lines[index]), !startsBlock(lines[index]) {
                paragraph.append(lines[index])
                index += 1
            }
            // A line that starts a block ends the paragraph, so the only way to
            // reach here with nothing collected is a line this parser has no rule
            // for. Render it, do not skip it.
            if paragraph.isEmpty {
                paragraph.append(lines[index])
                index += 1
            }
            blocks.append(.paragraph(paragraph.joined(separator: "\n")))
        }

        return blocks
    }

    // MARK: rules

    private static func isBlank(_ line: String) -> Bool {
        line.trimmingCharacters(in: .whitespaces).isEmpty
    }

    /// `# Title` through `###### Title`. A `#` with no space after it (`#tag`) is
    /// a word, not a heading.
    private static func headingOf(_ line: String) -> MarkdownBlock? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.hasPrefix("#") else { return nil }
        let hashes = trimmed.prefix(while: { $0 == "#" })
        guard hashes.count <= 6, trimmed.count > hashes.count else { return nil }
        let rest = trimmed.dropFirst(hashes.count)
        guard rest.first?.isWhitespace == true else { return nil }
        return .heading(level: hashes.count, text: rest.trimmingCharacters(in: .whitespaces))
    }

    /// `---`, `***`, `___`, with the spaces CommonMark allows between the marks.
    /// CommonMark also reads a `---` under a paragraph line as a setext H2; that
    /// is not in the subset a coding agent emits, and treating it as a rule
    /// renders both lines as text, which loses nothing.
    private static func isRule(_ line: String) -> Bool {
        let marks = line.trimmingCharacters(in: .whitespaces).filter { !$0.isWhitespace }
        guard marks.count >= 3 else { return false }
        return marks.allSatisfy { $0 == "-" } || marks.allSatisfy { $0 == "*" } || marks.allSatisfy { $0 == "_" }
    }

    private static func isQuote(_ line: String) -> Bool {
        line.trimmingCharacters(in: .whitespaces).hasPrefix(">")
    }

    private static func stripQuote(_ line: String) -> String {
        var rest = line.trimmingCharacters(in: .whitespaces).dropFirst()
        if rest.first?.isWhitespace == true { rest = rest.dropFirst() }
        return String(rest)
    }

    private static func isBullet(_ line: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard let mark = trimmed.first, mark == "-" || mark == "*" || mark == "+" else { return false }
        // `- ` is an item; `-` alone is a rule handled above; `->` is prose.
        return trimmed.count > 1 && trimmed[trimmed.index(after: trimmed.startIndex)].isWhitespace
    }

    private static func isNumbered(_ line: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        let digits = trimmed.prefix(while: \.isNumber)
        guard !digits.isEmpty, digits.count <= 9, trimmed.count > digits.count else { return false }
        let mark = trimmed[digits.endIndex]
        guard mark == "." || mark == ")" else { return false }
        // `1.5 miles` is a number, not a list.
        return trimmed.index(after: digits.endIndex) < trimmed.endIndex
            && trimmed[trimmed.index(after: digits.endIndex)].isWhitespace
    }

    private static func isTableRow(_ line: String) -> Bool {
        !isBlank(line) && line.contains("|")
    }

    private static func isTableDelimiter(_ line: String) -> Bool {
        let marks = line.trimmingCharacters(in: .whitespaces).filter { !$0.isWhitespace }
        guard marks.contains("-"), marks.contains("|") else { return false }
        return marks.allSatisfy { $0 == "-" || $0 == "|" || $0 == ":" }
    }

    private static func cells(of line: String) -> [String] {
        var parts = line.split(separator: "|", omittingEmptySubsequences: false).map {
            $0.trimmingCharacters(in: .whitespaces)
        }
        if parts.first?.isEmpty == true { parts.removeFirst() }
        if parts.last?.isEmpty == true { parts.removeLast() }
        return parts
    }

    /// True for a line that opens a block, so a paragraph stops in front of it.
    /// A model writes "Here is the plan:" and then a list on the next line; the
    /// sentence and the list are two blocks.
    private static func startsBlock(_ line: String) -> Bool {
        fenceOpener(line) != nil || isRule(line) || headingOf(line) != nil || isQuote(line)
            || isBullet(line) || isNumbered(line)
    }

    // MARK: fences and lists

    private struct Fence {
        let marker: Character
        let length: Int
        let language: String?
    }

    /// ``` and ~~~, with the info string. The opening fence's length sets how
    /// many markers close it, which is what lets a block contain ```` ``` ````.
    private static func fenceOpener(_ line: String) -> Fence? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard let marker = trimmed.first, marker == "`" || marker == "~" else { return nil }
        let length = trimmed.prefix(while: { $0 == marker }).count
        guard length >= 3 else { return nil }
        let info = trimmed.dropFirst(length).trimmingCharacters(in: .whitespaces)
        // A fence's info string is one word in practice (`swift`); a whole
        // sentence after the markers is the model writing prose, not naming a
        // language, so it stays out of the block.
        let language = info.split(separator: " ").first.map(String.init)
        return Fence(marker: marker, length: length, language: language)
    }

    /// The body of a fenced block and the line after it.
    ///
    /// An unterminated fence is the common case, not the edge case: a run cut
    /// off mid-answer leaves one open. Everything to the end of the answer is
    /// then code, because the alternative is rendering a code block's contents as
    /// prose — with its `#` lines turned into headings.
    private static func readFence(_ lines: [String], from start: Int, marker fence: Fence) -> (String, Int) {
        var body: [String] = []
        var index = start + 1
        while index < lines.count {
            let trimmed = lines[index].trimmingCharacters(in: .whitespaces)
            let run = trimmed.prefix(while: { $0 == fence.marker }).count
            if run >= fence.length, trimmed.dropFirst(run).trimmingCharacters(in: .whitespaces).isEmpty {
                return (body.joined(separator: "\n"), index + 1)
            }
            body.append(lines[index])
            index += 1
        }
        return (body.joined(separator: "\n"), index)
    }

    /// A list, and the line after it.
    ///
    /// A continuation line — indented, or simply a plain line under an item —
    /// joins the item above it. A blank line inside a list is a loose list, so
    /// the list continues if the next non-blank line is another item.
    ///
    /// `label` is the marker as written, for a numbered item: a list that starts
    /// at 5 means 5. A bullet's label is empty — `-`, `*` and `+` are one kind of
    /// item, and which glyph draws it is the view's business, not the wire's.
    private static func readList(
        _ lines: [String], from start: Int, numbered: Bool
    ) -> (items: [MarkdownItem], Int) {
        var items: [MarkdownItem] = []
        var index = start
        while index < lines.count {
            let line = lines[index]
            let isItem = numbered ? isNumbered(line) : isBullet(line)
            if isItem {
                items.append(splitMarker(line, numbered: numbered))
                index += 1
                continue
            }
            if isBlank(line) {
                // Look past the blank: a list continues if the items do.
                var probe = index
                while probe < lines.count, isBlank(lines[probe]) { probe += 1 }
                let continues = probe < lines.count && (numbered ? isNumbered(lines[probe]) : isBullet(lines[probe]))
                if continues {
                    index = probe
                    continue
                }
                break
            }
            if !startsBlock(line), !items.isEmpty {
                // A subscript write, not a read-then-append: `let item = items[i]`
                // shares the string with the element, and appending to the copy
                // writes the whole string back. See `Transcript.appendDelta`.
                items[items.count - 1].text += "\n" + line.trimmingCharacters(in: .whitespaces)
                index += 1
                continue
            }
            break
        }
        return (items, index)
    }

    private static func splitMarker(_ line: String, numbered: Bool) -> MarkdownItem {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        if numbered {
            let digits = trimmed.prefix(while: \.isNumber)
            let mark = trimmed[digits.endIndex]
            var rest = trimmed.dropFirst(digits.count + 1)
            if rest.first?.isWhitespace == true { rest = rest.dropFirst() }
            return MarkdownItem(label: "\(digits)\(mark)", text: String(rest))
        }
        return MarkdownItem(label: "", text: String(trimmed.dropFirst(2)).trimmingCharacters(in: .whitespaces))
    }
}
