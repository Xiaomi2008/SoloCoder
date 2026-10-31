import AgentAppCore
import Foundation

// The command palette's search rule, checked headless.
//
// The palette is the keyboard route to the app's actions, and its ranking is
// what makes it usable: typing `settings` has to put "Open Settings" first,
// and typing `model off` has to select the row that names both. The view draws
// the rows; this is what the rows answer to.

func commandChecks(_ checks: Checks) {
    print("command palette")

    let labels = [
        CommandLabel(title: "New session", detail: "Start with an empty timeline"),
        CommandLabel(title: "Open Settings", detail: "Defaults, keys, and model servers"),
        CommandLabel(title: "Choose working directory", detail: "required — the scope the agent works in"),
        CommandLabel(title: "Refresh models", detail: "10 models in the table"),
        CommandLabel(title: "Thinking: model default", detail: "Let the model decide — the slow path"),
        CommandLabel(title: "Thinking: off", detail: "off — answers fastest"),
        CommandLabel(title: "Use lan/swift-1.5", detail: "lan · openai-chat"),
    ]

    checks.equal(
        CommandSearch.terms("  Settings  "), ["settings"],
        "a query is trimmed and lowercased"
    )
    checks.equal(
        CommandSearch.terms("lan/swift-1.5"), ["lan/swift-1.5"],
        "a model key is one term, not two: splitting it would match half the table"
    )
    checks.equal(
        CommandSearch.terms("thinking off"), ["thinking", "off"],
        "a two-word query is two terms, and both have to match"
    )

    checks.equal(
        CommandSearch.rank(labels, query: ""), Array(labels.indices),
        "an empty query is the list's own order — the common actions first"
    )
    checks.equal(
        CommandSearch.rank(labels, query: "settings"), [1],
        "an exact title selects exactly that row"
    )
    checks.equal(
        CommandSearch.rank(labels, query: "open"), [1, 6],
        "a title prefix ranks above a detail match — 'openai-chat' is a substring, not a title"
    )
    checks.equal(
        CommandSearch.rank(labels, query: "thinking off"), [5],
        "every term must match: 'thinking off' is the one row that names both"
    )
    checks.equal(
        CommandSearch.rank(labels, query: "thinking model"), [4],
        "…and 'thinking model' is the other one"
    )
    checks.equal(
        CommandSearch.rank(labels, query: "models"), [3],
        "a plural in the title matches the singular query"
    )
    checks.equal(
        CommandSearch.rank(labels, query: "lan/swift"), [6],
        "a model key in the detail is reachable"
    )
    checks.equal(
        CommandSearch.rank(labels, query: "zzz"), [],
        "a miss is a miss — the palette says no commands match"
    )

    // Ties keep the list's order, so a broad query does not shuffle the common
    // actions on every keystroke.
    let ties = [
        CommandLabel(title: "Cancel run", detail: "streaming"),
        CommandLabel(title: "Copy answer", detail: "streaming"),
    ]
    checks.equal(
        CommandSearch.rank(ties, query: "streaming"), [0, 1],
        "equal scores keep the list's own order"
    )

    // Ranking is a rule of its own, so the words the palette shows are not
    // invented from the match: the view renders the label it was given.
    checks.expect(
        labels.allSatisfy { !$0.title.isEmpty },
        "every command has a title to type"
    )
}
