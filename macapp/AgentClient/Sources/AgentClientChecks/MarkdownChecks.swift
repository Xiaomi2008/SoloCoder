import AgentAppCore
import Foundation

// The markdown block parser.
//
// The property these checks are built around is the one the parser is written to
// guarantee: **no input line is ever dropped.** A mis-classified line must come
// out as text. Most of the checks below are therefore "this line is in a block of
// the right kind, with its content intact", and the last one runs a whole messy
// answer through and looks for every line.

func markdownChecks(_ checks: Checks) {
    checks.note("markdown")

    // MARK: paragraphs and headings

    checks.equal(
        Markdown.blocks("Just a sentence."),
        [.paragraph("Just a sentence.")],
        "a plain answer is one paragraph"
    )

    checks.equal(
        Markdown.blocks("line one\nline two"),
        [.paragraph("line one\nline two")],
        "a paragraph keeps its newline — a model means a line break by it"
    )

    checks.equal(
        Markdown.blocks("# Title\n\n## Section\n\n### Sub"),
        [.heading(level: 1, text: "Title"), .heading(level: 2, text: "Section"), .heading(level: 3, text: "Sub")],
        "heading levels"
    )

    checks.equal(
        Markdown.blocks("#hashtag"),
        [.paragraph("#hashtag")],
        "`#tag` is a word, not a heading"
    )

    checks.equal(
        Markdown.blocks("####### seven"),
        [.paragraph("####### seven")],
        "seven hashes is not a heading"
    )

    // MARK: fenced code

    let fenced = Markdown.blocks("```swift\nfunc f() {\n    let x = 1\n}\n```")
    checks.equal(fenced.count, 1, "one fenced block")
    if case .code(let language, let body) = fenced.first {
        checks.equal(language, "swift", "the fence's info string is the language")
        checks.equal(body, "func f() {\n    let x = 1\n}", "a code block keeps its indentation verbatim")
    } else {
        checks.expect(false, "a fenced block is code, got \(fenced)")
    }

    checks.equal(
        Markdown.blocks("```\n**bold** in code, `not` parsed\n```"),
        [.code(language: nil, text: "**bold** in code, `not` parsed")],
        "inline markers inside a fence survive — the fence is not run through an inline parser"
    )

    if case .code(_, let body) = Markdown.blocks("```python\nprint(1)\nprint(2)").first {
        checks.equal(body, "print(1)\nprint(2)", "an unterminated fence is code to the end")
    } else {
        checks.expect(false, "an unterminated fence is code to the end, got \(Markdown.blocks("```python\nprint(1)"))")
    }

    checks.equal(
        Markdown.blocks("~~~\n```\nnot a close\n~~~"),
        [.code(language: nil, text: "```\nnot a close")],
        "a ~~~ fence is closed by ~~~, so a ``` inside it is content"
    )

    checks.equal(
        Markdown.blocks("Run `ls` now."),
        [.paragraph("Run `ls` now.")],
        "one backtick is not a fence"
    )

    // MARK: lists

    checks.equal(
        Markdown.blocks("- one\n- two"),
        [.bullets(["one", "two"])],
        "a bullet list"
    )

    checks.equal(
        Markdown.blocks("* star\n+ plus"),
        [.bullets(["star", "plus"])],
        "`*` and `+` are bullets too"
    )

    checks.equal(
        Markdown.blocks("- one\n  continued\n- two"),
        [.bullets(["one\ncontinued", "two"])],
        "a continuation line joins the item above it"
    )

    checks.equal(
        Markdown.blocks("- one\n\n- two"),
        [.bullets(["one", "two"])],
        "a blank line inside a list is a loose list, not two lists"
    )

    checks.equal(
        Markdown.blocks("1. first\n2. second"),
        [
            .numbered([MarkdownItem(label: "1.", text: "first"), MarkdownItem(label: "2.", text: "second")]),
        ],
        "a numbered list"
    )

    checks.equal(
        Markdown.blocks("1) paren"),
        [.numbered([MarkdownItem(label: "1)", text: "paren")])],
        "`1)` is a numbered item"
    )

    checks.equal(
        Markdown.blocks("Walk 1.5 miles."),
        [.paragraph("Walk 1.5 miles.")],
        "`1.5 miles` is a number, not a list"
    )

    checks.equal(
        Markdown.blocks("Here is the plan:\n- fetch\n- write"),
        [.paragraph("Here is the plan:"), .bullets(["fetch", "write"])],
        "a paragraph stops in front of the list on the next line"
    )

    // MARK: quotes, rules, tables

    checks.equal(
        Markdown.blocks("> quoted\n> more"),
        [.quote(["quoted", "more"])],
        "a quote, markers stripped"
    )

    checks.equal(Markdown.blocks("---"), [.rule], "`---` is a rule")
    checks.equal(Markdown.blocks("- - -"), [.rule], "`- - -` is a rule")
    checks.equal(Markdown.blocks("***"), [.rule], "`***` is a rule")
    checks.equal(Markdown.blocks("- item"), [.bullets(["item"])], "`- item` is a bullet, not a rule")

    let table = Markdown.blocks("| file | lines |\n| --- | --- |\n| a.swift | 12 |\n| b.swift | 7 |")
    checks.equal(
        table,
        [.table(rows: [["file", "lines"], ["a.swift", "12"], ["b.swift", "7"]])],
        "a pipe table: header, delimiter row dropped, rows kept"
    )

    checks.equal(
        Markdown.blocks("cat a.txt | wc -l"),
        [.paragraph("cat a.txt | wc -l")],
        "a shell pipe is not a table — a table needs its `---` row"
    )

    // MARK: the answer a model actually writes

    let answer = """
        I read the file and found the bug.

        ## Cause
        The fence parser dropped the last line.

        ```swift
        func parse(_ s: String) {
            // the fix
        }
        ```

        **Fix**
        1. keep the tail
        2. trim at the cap

        | file | changed |
        | --- | --- |
        | Transcript.swift | 12 lines |

        > note: the answer is uncapped.
        """
    let blocks = Markdown.blocks(answer)
    checks.equal(blocks.count, 8, "the whole answer is 8 blocks, got \(blocks.count): \(blocks)")

    // The property that matters: every line of the answer reaches a block. A
    // parser that mis-classifies a line has to render it, not lose it.
    let allText = blocks.map { block in
        switch block {
        case .heading(_, let text), .paragraph(let text), .code(_, let text): return text
        case .bullets(let items), .quote(let items): return items.joined(separator: "\n")
        case .numbered(let items): return items.map(\.text).joined(separator: "\n")
        case .table(let rows): return rows.joined(separator: [" "]).joined(separator: "\n")
        case .rule: return ""
        }
    }.joined(separator: "\n")
    for line in answer.split(separator: "\n") {
        let content = line.trimmingCharacters(in: .whitespaces)
        // Blank lines, rules and fence markers are structure: the parser turns
        // them into a block's kind, and the language it read off a fence is
        // checked where the fence is.
        if content.isEmpty || content == "---" || content.hasPrefix("```") || content.hasPrefix("~~~") {
            continue
        }
        let needle = content
            .replacingOccurrences(of: "- ", with: "")
            .replacingOccurrences(of: "> ", with: "")
            .replacingOccurrences(of: "1. ", with: "")
            .replacingOccurrences(of: "2. ", with: "")
            .replacingOccurrences(of: "|", with: " ")
        for part in needle.split(separator: " ").filter({ $0.count > 3 }) {
            checks.expect(allText.contains(part), "the answer's `\(part)` reaches a block")
        }
    }
}
