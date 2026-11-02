# CLI design

The look of `openagent` in a terminal. Both engines (Rust in `openagent-rs`,
Python in `SoloCoder`) render from this page, and a scripted session must print
the same text in either one.

## Modes

| Mode | When | Behaviour |
| --- | --- | --- |
| Live | stdout is a terminal, `TERM` is not `dumb`, no `-p` | Spinner, in-place lines, markdown, wrapping, line editor, key menus |
| Plain | otherwise | Line-oriented, deltas printed verbatim, 100 column truncation |

`NO_COLOR` removes colour in either mode and changes nothing else. Output stays
inline: no alternate screen, so scrollback, copy and paste, and pipes work.

## Glyphs

| Glyph | Meaning |
| --- | --- |
| `●` | Start of an assistant message or a tool call |
| `⎿` | A tool's result |
| `✗` | A failed message |
| `⚠` | A permission question |
| `❯` | Input prompt and the selected menu row |
| `·` | Meta information |
| `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` | Spinner |

No emoji. No other markers.

## Colour roles

| Role | Truecolor | 16 colour | Used for |
| --- | --- | --- | --- |
| accent | `#7AA2F7` | cyan | Tool names, headings, prompt, spinner |
| success | `#73C991` | green | Result glyph, added lines, granted |
| error | `#F7768E` | red | Failures, removed lines, denied |
| warn | `#E0AF68` | yellow | Permission glyph, stops, inline code |
| muted | dim | dim | Results, meta, hints, footer |

Truecolor applies when `COLORTERM` is `truecolor` or `24bit`.

## Blocks

Header, live mode, once:

    ╭─ openagent ─────────────────────────────╮
    │ model  provider/name · 200k context     │
    │ cwd    ~/Projects/thing                 │
    ╰─────────────────────────────────────────╯
    /help for commands · ! runs a shell command · Ctrl-D quits

Header, plain mode: `Using provider/name (context N)`.

Tool call. The name is the tool's name in CamelCase. The argument is the one
that identifies the call, on one line, cut at 80 characters with `…`.

    ● Read(src/main.rs)
      ⎿ 42 lines

| Tool | Argument | Result |
| --- | --- | --- |
| `read` | `path` | `N lines` |
| `write` | `path` | `Wrote N lines` |
| `edit` | `path` | `Added A lines, removed R lines`, then the diff |
| `bash` | `command` | First 5 lines of output, then `… +N lines`; `(no output)` |
| `grep`, `glob` | `pattern` | First 5 lines, then `… +N lines` |
| others | `path`, `command`, `pattern`, `query`, `url`, then the first string argument | First 3 lines, then `… +N lines` |

A result whose text starts with `Error` is shown in the error colour, 5 lines.
Paths under the working directory are shown relative to it. A result line is cut
to the terminal width, or 100 columns in plain mode, with `…`.

Diff rows come from the edit tool's unified diff, at most 40, then `… +N lines`:

         12   unchanged line
         13 - removed line
         13 + added line

A second consecutive call of `read`, `glob` or `grep` collapses into the block
above it in live mode, showing the last 5:

    ● Read 2 files
      ⎿ src/a.rs · 42 lines
      ⎿ src/b.rs · 10 lines

Permission question. `bash` shows the command, `edit` shows the find and replace
text as a diff, `write` shows the first 5 lines of the content, anything else
shows `key: value` rows.

    ⚠ Bash wants to run
        cargo test
        reason

Live mode answers with an arrow-key menu (`y`, `a`, `n` also work; Esc means no):

    ❯ Yes
      Yes, for this session
      No

Plain mode prints `allow [y/n/a]? ` and reads a line. The outcome prints as
`· permission allowed (once)` or `· permission denied (once)`.

Assistant text, live mode: `●` on the first row, two columns of indent after.
Text wraps at the terminal width and streams with the unfinished row redrawn in
place.

| Markdown | Shown as |
| --- | --- |
| `# heading` | Bold accent, without the hashes |
| `- item`, `* item`, `+ item` | `• item`, continuation rows aligned |
| `1. item` | As written, continuation rows aligned |
| `> quote` | `│ quote` in muted |
| `---`, `***`, `___` | A rule across the width |
| fenced code | Indented 2 more, highlighted line by line, not wrapped |
| `\| table \|` | As written, not wrapped |
| `**bold**`, `*italic*`, `` `code` `` | Styled, markers hidden |
| `[text](url)` | As written, so the URL stays visible and clickable |

Runs of blank lines collapse to one. Leading and trailing blanks are dropped.

Working indicator, live mode, on one in-place line:

    ⠋ Thinking… 12s · ~340 tokens · esc to interrupt

The label is `Thinking`, or `Running Bash` while a tool runs. Tokens are
characters streamed divided by 4.

Footer after each turn, muted: `  provider/name · context 12% · turn 3`. After
a `-p` run: `provider/name · context 12% · session sess_…`.

`/context` shows the numbers and a 30 column bar:

    ⎿ context 12,345 / 200,000 tokens (6%)
      ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░

## Input

Live mode uses a line editor with history at `~/.openagent/cli_history*`, mode
`0600`. Enter submits. A trailing `\`, an unclosed code fence, Ctrl-J or
Alt-Enter continues on a new line. `/` shows the commands, Tab completes them.
Ctrl-C clears the line and Ctrl-D quits.

During a run, Esc or Ctrl-C cancels the run and keeps the session.
