import Foundation

// Syntax colour for a code block.
//
// A lexer, not a parser: one pass over the text that finds the spans a reader
// looks for first — comments, strings, keywords, numbers, type names and calls —
// and leaves everything else in the text colour. That is what an editor's
// highlighting is mostly doing for the eye, and a grammar per language would be
// a dependency or a project for the rest of it.
//
// Two rules keep it from doing harm:
//
// - **No language, no colour.** A fence without a tag is as often a log, a tree
//   or a table as it is code, and a guessed `#` comment or `'` string turns half
//   of a log grey. An unknown tag is the same: plain text is never wrong.
// - **Spans are UTF-16 offsets.** Both renderers want them — `NSAttributedString`
//   ranges are UTF-16, and `AttributedString` indices are built from them — and
//   the lexer walks code units, so a non-ASCII character is one more unit of
//   "other", never a split scalar.

public enum CodeTokenKind: Equatable, Sendable {
    case keyword
    case string
    case comment
    case number
    case type
    case call
    /// A decorator, a key, a shell variable, a lifetime.
    case attribute
    case added
    case removed
    case meta
}

public struct CodeToken: Equatable, Sendable {
    /// UTF-16 offsets into the highlighted text.
    public let range: Range<Int>
    public let kind: CodeTokenKind

    public init(_ range: Range<Int>, _ kind: CodeTokenKind) {
        self.range = range
        self.kind = kind
    }
}

public enum Highlight {
    /// The spans to colour, in order and not overlapping.
    public static func tokens(_ text: String, language: String?) -> [CodeToken] {
        guard let name = canonical(language) else { return [] }
        if name == "diff" { return diff(text) }
        guard let grammar = grammars[name] else { return [] }
        var lexer = Lexer(units: Array(text.utf16), grammar: grammar)
        return lexer.run()
    }

    /// The fence tag a file at this path would get, for a tool's file body.
    public static func language(forPath path: String) -> String? {
        let name = (path as NSString).lastPathComponent.lowercased()
        if name == "makefile" || name == "dockerfile" { return "shell" }
        let ext = (name as NSString).pathExtension
        return canonical(ext)
    }

    /// Whether a block in this language is coloured at all.
    public static func supports(_ language: String?) -> Bool {
        canonical(language) != nil
    }

    static func canonical(_ language: String?) -> String? {
        guard let language else { return nil }
        // `python title="x.py"`, `{.rust}`: the tag is the first word.
        let tag = language.lowercased()
            .trimmingCharacters(in: CharacterSet(charactersIn: "{}. "))
            .split(whereSeparator: { $0 == " " || $0 == "," || $0 == "{" }).first.map(String.init) ?? ""
        let name = aliases[tag] ?? tag
        return name == "diff" || grammars[name] != nil ? name : nil
    }

    private static let aliases: [String: String] = [
        "py": "python", "python3": "python", "pyi": "python",
        "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript", "node": "javascript",
        "ts": "javascript", "tsx": "javascript", "typescript": "javascript",
        "sh": "shell", "bash": "shell", "zsh": "shell", "console": "shell", "shellscript": "shell",
        "terminal": "shell", "fish": "shell",
        "rs": "rust",
        "golang": "go",
        "h": "c", "cc": "c", "cpp": "c", "c++": "c", "cxx": "c", "hpp": "c", "m": "c", "mm": "c",
        "objc": "c", "objective-c": "c", "cuda": "c", "cu": "c", "metal": "c",
        "kt": "kotlin", "kts": "kotlin",
        "cs": "csharp", "c#": "csharp",
        "rb": "ruby",
        "yml": "yaml",
        "jsonc": "json", "json5": "json",
        "patch": "diff",
        "html": "markup", "xml": "markup", "svg": "markup", "plist": "markup", "xhtml": "markup", "vue": "markup",
        "psql": "sql", "postgresql": "sql", "mysql": "sql", "sqlite": "sql",
    ]

    // MARK: diff

    /// A diff is coloured by line, not by token: the sign is the whole meaning.
    private static func diff(_ text: String) -> [CodeToken] {
        var out: [CodeToken] = []
        var start = 0
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let length = line.utf16.count
            let kind: CodeTokenKind? =
                line.hasPrefix("+++") || line.hasPrefix("---") || line.hasPrefix("@@") ? .meta
                : line.hasPrefix("+") ? .added
                : line.hasPrefix("-") ? .removed
                : nil
            if let kind, length > 0 { out.append(CodeToken(start..<start + length, kind)) }
            start += length + 1
        }
        return out
    }

    // MARK: grammars

    struct Grammar {
        var keywords: Set<String> = []
        var literals: Set<String> = []
        var builtinTypes: Set<String> = []
        var lineComments: [String] = []
        /// `#` starts a comment only after whitespace: `$#` and `a#b` are words.
        var hashNeedsSpace = false
        var blockComment: (open: String, close: String)?
        var quotes: Set<UInt16> = [Highlight.quote, Highlight.apostrophe]
        /// `"""`/`'''` strings that span lines.
        var tripleQuotes = false
        /// Quotes that may span lines: JavaScript's backtick.
        var multilineQuotes: Set<UInt16> = []
        var capitalizedTypes = true
        var calls = true
        /// `@decorator`.
        var decorators = false
        /// `#include`, `#if`: a word after `#` is a keyword.
        var directives = false
        /// `$NAME`, `${NAME}`.
        var variables = false
        /// The first word of a command is the call: `git` in `git status`.
        var commands = false
        var caseInsensitive = false
        /// A word or string followed by this is a key.
        var keySeparator: UInt16?
        /// Words may contain `-`: `max-width`, `on-failure`.
        var dashInWords = false
        var lifetimes = false
        var markup = false
    }

    private static let cFamily = ["if", "else", "for", "while", "do", "switch", "case", "default", "break",
                                  "continue", "return", "goto", "struct", "enum", "union", "typedef",
                                  "static", "const", "extern", "volatile", "inline", "sizeof", "class",
                                  "public", "private", "protected", "virtual", "override", "template",
                                  "typename", "namespace", "using", "new", "delete", "this", "throw",
                                  "try", "catch", "auto", "register", "constexpr", "noexcept", "operator",
                                  "friend", "explicit", "mutable", "final", "decltype", "static_cast",
                                  "dynamic_cast", "reinterpret_cast", "const_cast", "co_await", "co_return",
                                  "co_yield", "kernel", "device", "constant", "threadgroup", "__global__",
                                  "__device__", "__host__", "__shared__"]
    private static let cTypes = ["int", "char", "float", "double", "void", "long", "short", "unsigned",
                                 "signed", "bool", "size_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t",
                                 "int8_t", "int16_t", "int32_t", "int64_t", "id", "BOOL", "half", "uint",
                                 "float2", "float3", "float4"]

    static let grammars: [String: Grammar] = {
        var g: [String: Grammar] = [:]

        g["swift"] = Grammar(
            keywords: ["import", "let", "var", "func", "class", "struct", "enum", "protocol", "extension",
                       "if", "else", "guard", "switch", "case", "default", "for", "in", "while", "repeat",
                       "return", "break", "continue", "fallthrough", "throw", "throws", "rethrows", "try",
                       "catch", "do", "defer", "where", "as", "is", "init", "deinit", "subscript", "self",
                       "Self", "super", "static", "final", "public", "private", "fileprivate", "internal",
                       "open", "override", "mutating", "nonmutating", "lazy", "weak", "unowned", "inout",
                       "some", "any", "async", "await", "actor", "nonisolated", "isolated", "typealias",
                       "associatedtype", "operator", "convenience", "required", "dynamic", "indirect",
                       "get", "set", "willSet", "didSet", "consuming", "borrowing", "sending", "package",
                       "macro"],
            literals: ["true", "false", "nil"],
            lineComments: ["//"], blockComment: ("/*", "*/"), quotes: [quote], tripleQuotes: true,
            decorators: true, directives: true
        )
        g["python"] = Grammar(
            keywords: ["def", "class", "return", "if", "elif", "else", "for", "while", "in", "not", "and",
                       "or", "is", "import", "from", "as", "with", "try", "except", "finally", "raise",
                       "pass", "break", "continue", "lambda", "yield", "global", "nonlocal", "assert",
                       "del", "async", "await", "match", "case", "self", "cls"],
            literals: ["True", "False", "None"],
            builtinTypes: ["int", "str", "float", "bool", "list", "dict", "set", "tuple", "bytes", "object",
                           "type"],
            lineComments: ["#"], tripleQuotes: true, decorators: true
        )
        g["javascript"] = Grammar(
            keywords: ["const", "let", "var", "function", "return", "if", "else", "for", "while", "do",
                       "switch", "case", "default", "break", "continue", "new", "delete", "typeof",
                       "instanceof", "in", "of", "class", "extends", "super", "this", "import", "export",
                       "from", "as", "async", "await", "yield", "try", "catch", "finally", "throw", "void",
                       "static", "get", "set", "interface", "type", "enum", "implements", "public",
                       "private", "protected", "readonly", "declare", "namespace", "abstract", "keyof",
                       "satisfies"],
            literals: ["true", "false", "null", "undefined", "NaN", "Infinity"],
            builtinTypes: ["string", "number", "boolean", "any", "unknown", "never", "object", "bigint",
                           "symbol"],
            lineComments: ["//"], blockComment: ("/*", "*/"), quotes: [quote, apostrophe, backtick],
            multilineQuotes: [backtick], decorators: true
        )
        g["json"] = Grammar(
            literals: ["true", "false", "null"], lineComments: ["//"], blockComment: ("/*", "*/"),
            quotes: [quote], capitalizedTypes: false, calls: false, keySeparator: colon
        )
        g["shell"] = Grammar(
            keywords: ["if", "then", "else", "elif", "fi", "for", "in", "do", "done", "while", "until",
                       "case", "esac", "function", "return", "export", "local", "readonly", "declare",
                       "set", "unset", "source", "exit", "select", "time", "sudo", "exec", "eval"],
            literals: ["true", "false"],
            lineComments: ["#"], hashNeedsSpace: true, multilineQuotes: [quote, apostrophe],
            capitalizedTypes: false, calls: false, variables: true, commands: true, dashInWords: true
        )
        g["rust"] = Grammar(
            keywords: ["fn", "let", "mut", "const", "static", "struct", "enum", "trait", "impl", "for",
                       "in", "if", "else", "match", "loop", "while", "break", "continue", "return", "use",
                       "mod", "pub", "crate", "super", "self", "Self", "as", "where", "type", "unsafe",
                       "extern", "ref", "move", "async", "await", "dyn", "box", "yield", "macro_rules"],
            literals: ["true", "false", "None", "Some", "Ok", "Err"],
            builtinTypes: ["i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32", "u64", "u128",
                           "usize", "f32", "f64", "bool", "char", "str"],
            lineComments: ["//"], blockComment: ("/*", "*/"), directives: false, lifetimes: true
        )
        g["go"] = Grammar(
            keywords: ["package", "import", "func", "var", "const", "type", "struct", "interface", "map",
                       "chan", "if", "else", "for", "range", "switch", "case", "default", "break",
                       "continue", "return", "go", "defer", "select", "fallthrough", "goto"],
            literals: ["true", "false", "nil", "iota"],
            builtinTypes: ["int", "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32",
                           "uint64", "float32", "float64", "string", "bool", "byte", "rune", "error", "any"],
            lineComments: ["//"], blockComment: ("/*", "*/"), quotes: [quote, apostrophe, backtick],
            multilineQuotes: [backtick], capitalizedTypes: false
        )
        g["c"] = Grammar(
            keywords: Set(cFamily + ["self", "nil", "YES", "NO"]),
            literals: ["true", "false", "NULL", "nullptr"], builtinTypes: Set(cTypes),
            lineComments: ["//"], blockComment: ("/*", "*/"), directives: true
        )
        g["java"] = Grammar(
            keywords: ["public", "private", "protected", "class", "interface", "enum", "extends",
                       "implements", "static", "final", "abstract", "void", "new", "return", "if", "else",
                       "for", "while", "do", "switch", "case", "default", "break", "continue", "try",
                       "catch", "finally", "throw", "throws", "import", "package", "this", "super",
                       "instanceof", "synchronized", "volatile", "transient", "native", "var", "record",
                       "sealed", "permits", "yield"],
            literals: ["true", "false", "null"],
            builtinTypes: ["int", "long", "short", "byte", "char", "float", "double", "boolean"],
            lineComments: ["//"], blockComment: ("/*", "*/"), decorators: true
        )
        g["kotlin"] = Grammar(
            keywords: ["fun", "val", "var", "class", "object", "interface", "data", "sealed", "enum",
                       "open", "override", "private", "public", "internal", "protected", "abstract",
                       "companion", "if", "else", "when", "for", "while", "do", "return", "break",
                       "continue", "in", "is", "as", "import", "package", "this", "super", "try", "catch",
                       "finally", "throw", "suspend", "inline", "lateinit", "by", "init", "typealias"],
            literals: ["true", "false", "null"],
            lineComments: ["//"], blockComment: ("/*", "*/"), quotes: [quote], tripleQuotes: true,
            decorators: true
        )
        g["csharp"] = Grammar(
            keywords: ["using", "namespace", "class", "struct", "interface", "enum", "record", "public",
                       "private", "protected", "internal", "static", "readonly", "const", "void", "new",
                       "return", "if", "else", "for", "foreach", "in", "while", "do", "switch", "case",
                       "default", "break", "continue", "try", "catch", "finally", "throw", "this", "base",
                       "var", "async", "await", "override", "virtual", "abstract", "sealed", "get", "set",
                       "out", "ref", "is", "as", "where", "yield", "partial"],
            literals: ["true", "false", "null"],
            builtinTypes: ["int", "long", "short", "byte", "char", "float", "double", "bool", "string",
                           "object", "decimal"],
            lineComments: ["//"], blockComment: ("/*", "*/"), directives: true
        )
        g["ruby"] = Grammar(
            keywords: ["def", "end", "class", "module", "if", "elsif", "else", "unless", "while", "until",
                       "for", "in", "do", "return", "yield", "begin", "rescue", "ensure", "raise", "then",
                       "case", "when", "self", "super", "require", "require_relative", "attr_reader",
                       "attr_accessor", "and", "or", "not", "lambda", "proc"],
            literals: ["true", "false", "nil"],
            lineComments: ["#"]
        )
        g["sql"] = Grammar(
            keywords: ["select", "from", "where", "and", "or", "not", "insert", "into", "values", "update",
                       "set", "delete", "create", "table", "drop", "alter", "add", "index", "join", "left",
                       "right", "inner", "outer", "on", "as", "group", "by", "order", "having", "limit",
                       "offset", "distinct", "union", "all", "primary", "key", "foreign", "references",
                       "default", "unique", "in", "is", "like", "between", "exists", "case", "when",
                       "then", "else", "end", "with", "returning", "asc", "desc", "view", "if"],
            literals: ["null", "true", "false"],
            builtinTypes: ["int", "integer", "text", "varchar", "char", "bigint", "boolean", "real",
                           "float", "date", "timestamp", "serial", "blob", "numeric", "json", "jsonb"],
            lineComments: ["--"], blockComment: ("/*", "*/"), quotes: [apostrophe],
            capitalizedTypes: false, caseInsensitive: true
        )
        g["yaml"] = Grammar(
            literals: ["true", "false", "null", "yes", "no", "on", "off"],
            lineComments: ["#"], hashNeedsSpace: true, capitalizedTypes: false, calls: false,
            keySeparator: colon, dashInWords: true
        )
        g["toml"] = Grammar(
            literals: ["true", "false"], lineComments: ["#"], tripleQuotes: true, capitalizedTypes: false,
            calls: false, keySeparator: equals, dashInWords: true
        )
        g["css"] = Grammar(
            keywords: ["important", "media", "import", "keyframes", "supports"],
            blockComment: ("/*", "*/"), capitalizedTypes: false, calls: true, keySeparator: colon,
            dashInWords: true
        )
        g["markup"] = Grammar(
            blockComment: ("<!--", "-->"), capitalizedTypes: false, calls: false, keySeparator: equals,
            dashInWords: true, markup: true
        )
        return g
    }()

    static let quote = UInt16(UInt8(ascii: "\""))
    static let apostrophe = UInt16(UInt8(ascii: "'"))
    static let backtick = UInt16(UInt8(ascii: "`"))
    static let colon = UInt16(UInt8(ascii: ":"))
    static let equals = UInt16(UInt8(ascii: "="))
}

// MARK: the lexer

private struct Lexer {
    let units: [UInt16]
    let grammar: Highlight.Grammar
    var at = 0
    var out: [CodeToken] = []
    /// At the start of a shell command: the next word is the command.
    var commandStart = true
    /// Inside `<...>` of markup; outside it is text and is left alone.
    var inTag = false
    var afterTagOpen = false

    init(units: [UInt16], grammar: Highlight.Grammar) {
        self.units = units
        self.grammar = grammar
    }

    mutating func run() -> [CodeToken] {
        while at < units.count {
            let start = at
            let unit = units[at]

            if let (open, close) = grammar.blockComment, matches(open) {
                at += open.utf16.count
                skip(to: close)
                emit(start, .comment)
                continue
            }

            if grammar.markup {
                markupStep(unit)
                continue
            }

            if lineComment(at: at) {
                while at < units.count, units[at] != newline { at += 1 }
                emit(start, .comment)
                continue
            }

            if grammar.quotes.contains(unit) || grammar.multilineQuotes.contains(unit) {
                if grammar.lifetimes, unit == Highlight.apostrophe, isLifetime() {
                    at += 1
                    while at < units.count, isWord(units[at]) { at += 1 }
                    emit(start, .attribute)
                    continue
                }
                string(unit)
                emit(start, .string)
                key(from: start)
                commandStart = false
                continue
            }

            if grammar.variables, unit == dollar {
                variable()
                if at > start + 1 { emit(start, .attribute) }
                continue
            }

            if (grammar.decorators && unit == atSign) || (grammar.directives && unit == hash) {
                if at + 1 < units.count, isWordStart(units[at + 1]) {
                    at += 1
                    while at < units.count, isWord(units[at]) { at += 1 }
                    emit(start, unit == atSign ? .attribute : .keyword)
                    continue
                }
            }

            if isDigit(unit), !followsWord(start) {
                while at < units.count, isWord(units[at]) || units[at] == dot { at += 1 }
                // `1..<n`: the range operator is not part of the number.
                while at > start + 1, units[at - 1] == dot { at -= 1 }
                emit(start, .number)
                commandStart = false
                continue
            }

            if isWordStart(unit) {
                word(start)
                continue
            }

            if grammar.commands {
                if unit == newline || unit == semicolon || unit == pipe || unit == ampersand
                    || unit == openParen || unit == backtickUnit {
                    commandStart = true
                } else if !isSpace(unit) {
                    commandStart = false
                }
            }
            at += 1
        }
        return out
    }

    // MARK: pieces

    private mutating func word(_ start: Int) {
        while at < units.count, isWord(units[at]) || (grammar.dashInWords && units[at] == dash && at > start) {
            at += 1
        }
        let text = String(decoding: units[start..<at], as: UTF16.self)
        let lookup = grammar.caseInsensitive ? text.lowercased() : text

        if grammar.commands {
            let wasStart = commandStart
            commandStart = false
            if grammar.keywords.contains(lookup) {
                emit(start, .keyword)
                commandStart = true
                return
            }
            if grammar.literals.contains(lookup) {
                emit(start, .keyword)
                return
            }
            // `VAR=value` is an assignment, not a command.
            if wasStart, peek() != Highlight.equals {
                // A path or a dotted name is still the command: `./run.sh`, `python3.11`.
                while at < units.count, units[at] == dot || units[at] == slash || isWord(units[at]) || units[at] == dash {
                    at += 1
                }
                emit(start, .call)
                return
            }
            return
        }

        if grammar.keywords.contains(lookup) || grammar.literals.contains(lookup) {
            emit(start, .keyword)
        } else if grammar.builtinTypes.contains(lookup) {
            emit(start, .type)
        } else if key(from: start) {
            return
        } else if grammar.calls, nextNonSpace() == openParen {
            emit(start, .call)
        } else if grammar.capitalizedTypes, isUpper(units[start]) {
            emit(start, .type)
        }
    }

    /// A word or string followed by the key separator is recoloured as a key.
    @discardableResult
    private mutating func key(from start: Int) -> Bool {
        guard let separator = grammar.keySeparator else { return false }
        var probe = at
        while probe < units.count, units[probe] == space || units[probe] == tab { probe += 1 }
        guard probe < units.count, units[probe] == separator else { return false }
        // `a: b` in YAML, `"a":` in JSON — but not `::` or `==`.
        if probe + 1 < units.count, units[probe + 1] == separator { return false }
        if out.last?.range.lowerBound == start { out.removeLast() }
        out.append(CodeToken(start..<at, .attribute))
        return true
    }

    private mutating func string(_ quoteUnit: UInt16) {
        let triple = grammar.tripleQuotes && matches(String(repeating: String(UnicodeScalar(UInt8(quoteUnit))), count: 3))
        if triple {
            at += 3
            let close = String(repeating: String(UnicodeScalar(UInt8(quoteUnit))), count: 3)
            while at < units.count, !matches(close) {
                at += units[at] == backslash ? 2 : 1
            }
            at = min(at + 3, units.count)
            return
        }
        let spans = grammar.multilineQuotes.contains(quoteUnit)
        at += 1
        while at < units.count {
            let unit = units[at]
            if unit == backslash, quoteUnit != Highlight.apostrophe || !grammar.commands {
                at += 2
                continue
            }
            if unit == quoteUnit {
                at += 1
                return
            }
            if unit == newline, !spans { return }
            at += 1
        }
        at = min(at, units.count)
    }

    private mutating func variable() {
        at += 1
        guard at < units.count else { return }
        if units[at] == openBrace {
            while at < units.count, units[at] != closeBrace, units[at] != newline { at += 1 }
            if at < units.count, units[at] == closeBrace { at += 1 }
        } else if isWordStart(units[at]) {
            while at < units.count, isWord(units[at]) { at += 1 }
        } else if isDigit(units[at]) || "@#?$!*-".utf16.contains(units[at]) {
            at += 1
        }
    }

    private mutating func markupStep(_ unit: UInt16) {
        let start = at
        if !inTag {
            if unit == lessThan {
                inTag = true
                afterTagOpen = true
                at += 1
                if at < units.count, units[at] == slash || units[at] == bang || units[at] == question { at += 1 }
                emit(start, .meta)
            } else {
                at += 1
            }
            return
        }
        if unit == greaterThan || (unit == slash && peekAt(at + 1) == greaterThan) {
            at += unit == slash ? 2 : 1
            emit(start, .meta)
            inTag = false
            return
        }
        if unit == Highlight.quote || unit == Highlight.apostrophe {
            at += 1
            while at < units.count, units[at] != unit { at += 1 }
            at = min(at + 1, units.count)
            emit(start, .string)
            return
        }
        if isWordStart(unit) {
            while at < units.count, isWord(units[at]) || units[at] == dash || units[at] == colonUnit { at += 1 }
            emit(start, afterTagOpen ? .keyword : .attribute)
            afterTagOpen = false
            return
        }
        at += 1
    }

    // MARK: lookups

    private func lineComment(at index: Int) -> Bool {
        for marker in grammar.lineComments where matches(marker) {
            if marker == "#", grammar.hashNeedsSpace, index > 0, !isSpace(units[index - 1]) { continue }
            return true
        }
        return false
    }

    private func isLifetime() -> Bool {
        // `'a` and `'static` are lifetimes; `'a'` and `'\n'` are characters.
        guard at + 1 < units.count, isWordStart(units[at + 1]) else { return false }
        return peekAt(at + 2) != Highlight.apostrophe
    }

    private func matches(_ marker: String) -> Bool {
        var index = at
        for unit in marker.utf16 {
            guard index < units.count, units[index] == unit else { return false }
            index += 1
        }
        return true
    }

    private mutating func skip(to close: String) {
        while at < units.count, !matches(close) { at += 1 }
        at = min(at + close.utf16.count, units.count)
    }

    private func followsWord(_ index: Int) -> Bool {
        index > 0 && isWord(units[index - 1])
    }

    private func peek() -> UInt16? { peekAt(at) }

    private func peekAt(_ index: Int) -> UInt16? { index < units.count ? units[index] : nil }

    private func nextNonSpace() -> UInt16? {
        var probe = at
        while probe < units.count, units[probe] == space { probe += 1 }
        return peekAt(probe)
    }

    private mutating func emit(_ start: Int, _ kind: CodeTokenKind) {
        guard at > start else { return }
        out.append(CodeToken(start..<at, kind))
    }
}

private let newline = UInt16(UInt8(ascii: "\n"))
private let space = UInt16(UInt8(ascii: " "))
private let tab = UInt16(UInt8(ascii: "\t"))
private let backslash = UInt16(UInt8(ascii: "\\"))
private let dollar = UInt16(UInt8(ascii: "$"))
private let atSign = UInt16(UInt8(ascii: "@"))
private let hash = UInt16(UInt8(ascii: "#"))
private let dot = UInt16(UInt8(ascii: "."))
private let dash = UInt16(UInt8(ascii: "-"))
private let slash = UInt16(UInt8(ascii: "/"))
private let semicolon = UInt16(UInt8(ascii: ";"))
private let pipe = UInt16(UInt8(ascii: "|"))
private let ampersand = UInt16(UInt8(ascii: "&"))
private let openParen = UInt16(UInt8(ascii: "("))
private let openBrace = UInt16(UInt8(ascii: "{"))
private let closeBrace = UInt16(UInt8(ascii: "}"))
private let lessThan = UInt16(UInt8(ascii: "<"))
private let greaterThan = UInt16(UInt8(ascii: ">"))
private let bang = UInt16(UInt8(ascii: "!"))
private let question = UInt16(UInt8(ascii: "?"))
private let colonUnit = UInt16(UInt8(ascii: ":"))
private let backtickUnit = UInt16(UInt8(ascii: "`"))

private func isDigit(_ unit: UInt16) -> Bool { unit >= 48 && unit <= 57 }
private func isUpper(_ unit: UInt16) -> Bool { unit >= 65 && unit <= 90 }
private func isWordStart(_ unit: UInt16) -> Bool {
    (unit >= 65 && unit <= 90) || (unit >= 97 && unit <= 122) || unit == 95
}
private func isWord(_ unit: UInt16) -> Bool { isWordStart(unit) || isDigit(unit) }
private func isSpace(_ unit: UInt16) -> Bool { unit == space || unit == tab || unit == newline }
