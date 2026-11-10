import AgentAppCore
import Foundation

// Syntax colour. The checks are of the spans a reader would call wrong at a
// glance: a keyword left plain, a string that runs past its quote, a comment
// marker inside a string, colour on a block that never said its language.

func highlightChecks(_ checks: Checks) {
    checks.note("highlight")

    /// The text of each span of `kind`.
    func spans(_ text: String, _ language: String?, _ kind: CodeTokenKind) -> [String] {
        let units = Array(text.utf16)
        return Highlight.tokens(text, language: language)
            .filter { $0.kind == kind }
            .map { String(decoding: units[$0.range], as: UTF16.self) }
    }

    checks.equal(Highlight.tokens("let x = 1 // one", language: nil), [], "no language, no colour")
    checks.equal(Highlight.tokens("let x = 1", language: "brainfudge"), [], "an unknown language is plain")

    let swift = "import SwiftUI\n// a comment\nlet name: String = \"a // not a comment\"\nfunc f() { print(42) }"
    checks.equal(spans(swift, "swift", .keyword), ["import", "let", "func"], "swift keywords")
    checks.equal(spans(swift, "swift", .comment), ["// a comment"], "a `//` inside a string is not a comment")
    checks.equal(spans(swift, "swift", .string), ["\"a // not a comment\""], "the string ends at its quote")
    checks.equal(spans(swift, "swift", .type), ["SwiftUI", "String"], "capitalised names are types")
    checks.equal(spans(swift, "swift", .call), ["f", "print"], "a name before `(` is a call")
    checks.equal(spans(swift, "swift", .number), ["42"], "numbers")
    checks.equal(spans("for i in 0..<n {}", "swift", .number), ["0"], "a range's dots are not the number's")
    checks.equal(spans("@MainActor\n#if DEBUG", "swift", .attribute), ["@MainActor"], "swift attribute")
    checks.equal(spans("@MainActor\n#if DEBUG", "swift", .keyword), ["#if"], "swift directive")
    checks.equal(spans("let s = \"\"\"\nline \"one\"\n\"\"\"\nlet t = 1", "swift", .string),
                 ["\"\"\"\nline \"one\"\n\"\"\""], "a multi-line string is one span")

    let python = "def f(x):\n    # note\n    return f'{x}' if x else None  # tail"
    checks.equal(spans(python, "python", .keyword), ["def", "return", "if", "else", "None"], "python keywords")
    checks.equal(spans(python, "python", .comment), ["# note", "# tail"], "python comments")
    checks.equal(spans("x = \"\"\"doc\n# not a comment\"\"\"", "py", .comment), [], "a `#` in a docstring")

    let shell = "export PATH=$HOME/bin:$PATH # set\ngit status | grep -c 'a#b' && echo \"${USER}\""
    checks.equal(spans(shell, "bash", .keyword), ["export"], "shell keyword")
    checks.equal(spans(shell, "bash", .call), ["git", "grep", "echo"], "a command's first word is the call")
    checks.equal(spans(shell, "bash", .attribute), ["$HOME", "$PATH"], "shell variables outside strings")
    checks.equal(spans(shell, "bash", .comment), ["# set"], "`#` only after a space is a comment")
    checks.equal(spans("echo $#", "sh", .comment), [], "`$#` is not a comment")

    checks.equal(spans("{\"name\": \"x\", \"n\": 2, \"ok\": true}", "json", .attribute), ["\"name\"", "\"n\"", "\"ok\""],
                 "a JSON key is a key")
    checks.equal(spans("{\"name\": \"x\", \"n\": 2, \"ok\": true}", "json", .string), ["\"x\""], "and a value a string")

    checks.equal(spans("fn f<'a>(x: &'a str) -> char { 'c' }", "rust", .string), ["'c'"], "a char literal")
    checks.equal(spans("fn f<'a>(x: &'a str) -> char { 'c' }", "rust", .attribute), ["'a", "'a"], "a lifetime")
    checks.equal(spans("SELECT id FROM users WHERE name = 'x' -- why", "sql", .keyword),
                 ["SELECT", "FROM", "WHERE"], "SQL keywords in any case")
    checks.equal(spans("name: app\non-failure: retry # why", "yaml", .attribute), ["name", "on-failure"], "yaml keys")
    checks.equal(spans("<div class=\"a\">hi 'there'</div>", "html", .keyword), ["div", "div"], "tag names")
    checks.equal(spans("<div class=\"a\">hi 'there'</div>", "html", .string), ["\"a\""],
                 "text between tags is not lexed")

    checks.equal(spans("- old\n+ new\n  same", "diff", .removed), ["- old"], "a removed line")
    checks.equal(spans("- old\n+ new\n  same", "diff", .added), ["+ new"], "an added line")

    checks.equal(Highlight.language(forPath: "/tmp/gen.swift"), "swift", "a path's extension names its language")
    checks.equal(Highlight.language(forPath: "src/App.TSX"), "javascript", "in any case")
    checks.equal(Highlight.language(forPath: "notes.txt"), nil, "a text file is plain")
    checks.expect(Highlight.supports("python title=\"x.py\""), "a tag's first word is the language")

    // Non-ASCII text before a token must not shift the span off the token.
    let accented = "let café = \"é\" // ü"
    checks.equal(spans(accented, "swift", .comment), ["// ü"], "spans are UTF-16 offsets")

    // Every span is in bounds, in order and disjoint, whatever the input.
    let messy = "\"unterminated\n/* open\n'''\n${\n#\n@\n" + swift + python + shell
    for language in ["swift", "python", "shell", "javascript", "rust", "c", "json", "yaml", "sql", "html", "diff"] {
        let tokens = Highlight.tokens(messy, language: language)
        let count = messy.utf16.count
        var end = 0
        var ok = true
        for token in tokens {
            if token.range.lowerBound < end || token.range.upperBound > count || token.range.isEmpty { ok = false }
            end = token.range.upperBound
        }
        checks.expect(ok, "\(language): spans are disjoint and in bounds")
    }
}
