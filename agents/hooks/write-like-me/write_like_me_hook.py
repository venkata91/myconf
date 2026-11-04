#!/usr/bin/env python3
"""Copilot CLI hook that enforces ~/write-like-me.md on prose the agent publishes.

Modes (first argument):
  context   sessionStart / subagentStart: inject the writing rule.
  gate      preToolUse: lint the prose a tool call is about to publish and deny
            the call with the exact violations when it breaks the guide.
  lint      Lint stdin (or file arguments) and print violations. For testing.

The gate fails open on its own bugs (prints {} and exits 0), because a crash in
a command preToolUse hook would deny every tool call.
"""

import json
import os
import re
import shlex
import sys

GUIDE = os.path.expanduser(os.environ.get("WRITE_LIKE_ME", "~/write-like-me.md"))

CONTEXT = (
    "WRITING RULE (enforced by a preToolUse hook): for any prose a person will read "
    "(PR titles and descriptions, commit messages, review comments and replies, Slack "
    "messages and canvases, Linear comments and docs, design docs, READMEs and other "
    "Markdown, summaries), read {guide} first and follow it exactly. For substantial "
    "drafts run its three passes (audit, critique, rewrite). For short messages apply "
    "the same checks before sending. The hook rejects publishing calls that contain "
    "em-dashes, semicolons or the guide's banned words, so draft the text in a file, "
    "fix every violation, then publish it with a literal path (for example "
    "`gh pr edit N --body-file /path/body.md`). Wrap real technical terms that "
    "collide with a banned word in backticks."
).format(guide=GUIDE)

# Each rule: (label, compiled regex). Matching is done on prose with code,
# quotes, URLs and HTML comments removed.
BANNED = [
    ("em-dash (use a comma, parentheses, or split the sentence)", re.compile("\u2014")),
    ("semicolon (split the sentence)", re.compile(r";")),
    ('"what no one else is talking about"', re.compile(r"what no one else is talking about", re.I)),
    ('"what nobody tells you"', re.compile(r"what nobody tells you", re.I)),
    ('"loop"', re.compile(r"\bloops?\b", re.I)),
    ('"signal"', re.compile(r"\bsignal(s|ed|ing|led|ling)?\b", re.I)),
    ('"cascade"', re.compile(r"\bcascad(e|es|ed|ing)\b", re.I)),
    ('"drift"', re.compile(r"\bdrift(s|ed|ing)?\b", re.I)),
    ('"quiet"/"quietly"', re.compile(r"\bquiet(ly)?\b", re.I)),
    ('"tapestry"', re.compile(r"\btapestr(y|ies)\b", re.I)),
    ('"realm"', re.compile(r"\brealms?\b", re.I)),
    ('"landscape"', re.compile(r"\blandscapes?\b", re.I)),
    ('"in conclusion"', re.compile(r"\bin conclusion\b", re.I)),
    ('"ultimately"', re.compile(r"\bultimately\b", re.I)),
    ('"to summarize"', re.compile(r"\bto summari[sz]e\b", re.I)),
    ('"unlock"', re.compile(r"\bunlock(s|ed|ing)?\b", re.I)),
    ('"unleash"', re.compile(r"\bunleash(es|ed|ing)?\b", re.I)),
    ('"harness"', re.compile(r"\bharness(es|ed|ing)?\b", re.I)),
    ('"not just X but Y" framing', re.compile(r"\bnot (just|only|merely)\b[^.!?\n]{1,80}?\bbut\b", re.I)),
    ('"it\'s not X, it\'s Y" framing', re.compile(r"\bit(?:'|\u2019)?s not\b[^.!?\n]{1,80}?[,;]\s*it(?:'|\u2019)?s\b", re.I)),
    ("slogan phrasing", re.compile(r"that(?:'|\u2019)?s the whole trick|worth stating plainly", re.I)),
]


def strip_non_prose(text):
    """Remove text that isn't ours or isn't prose: code, quotes, URLs, comments."""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", " ", text)
    text = re.sub(r"(?ms)^[ \t]*(```|~~~).*\Z", " ", text)  # unterminated fence
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"(?m)^[ \t]*>.*$", " ", text)  # quoted reviewer text
    text = re.sub(r"\]\([^)]*\)", "]", text)  # markdown link targets
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"&[a-zA-Z]+;|&#\d+;", " ", text)  # HTML entities
    # Trailers such as Co-authored-by and footers added by tooling.
    text = re.sub(r"(?m)^(Co-authored-by|Copilot-Session|Signed-off-by):.*$", " ", text)
    return text


def lint(text):
    prose = strip_non_prose(text)
    problems = []
    for label, rx in BANNED:
        for m in rx.finditer(prose):
            start = max(0, m.start() - 40)
            snippet = prose[start:m.end() + 40].replace("\n", " ").strip()
            problems.append("{}: ...{}...".format(label, re.sub(r"\s+", " ", snippet)))
    return problems


# --- extracting the prose a tool call publishes -------------------------------

PUBLISH_BASH = re.compile(
    r"\bgh\s+(pr|issue)\s+(create|edit|comment|review|close|merge)\b"
    r"|\bgh\s+release\s+(create|edit)\b"
    r"|\bgh\s+api\b"
    r"|\bgit\s+commit\b"
    r"|\bgit\s+tag\s+.*-[am]\b"
)

TEXT_FLAGS = {"--body", "-b", "--title", "-t", "--message", "-m", "--notes", "--subject"}
FILE_FLAGS = {"--body-file", "--notes-file", "--file"}
API_FIELD_FLAGS = {"-f", "--raw-field", "-F", "--field"}
API_TEXT_KEYS = {"body", "title", "message", "description", "text", "comment", "notes"}


class Unreadable(Exception):
    pass


def shell_vars(command):
    out = dict(os.environ)
    for m in re.finditer(r"(?:^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)", command):
        val = m.group(2)
        if val[:1] in "\"'":
            val = val[1:-1]
        out[m.group(1)] = val
    return out


def read_path(path, variables, cwd):
    if path == "-":
        return None  # stdin, covered by heredoc extraction
    expanded = re.sub(
        r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?",
        lambda m: variables.get(m.group(1), m.group(0)),
        os.path.expanduser(path),
    )
    if "$" in expanded or "`" in expanded:
        raise Unreadable(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd or os.getcwd(), expanded)
    try:
        with open(expanded, encoding="utf-8") as f:
            return f.read()
    except OSError:
        raise Unreadable(path)


HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(.*?)^[ \t]*\2[ \t]*$", re.S | re.M)
OPERATORS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&"}


def segments(command):
    """Split a shell command into simple commands, keeping quoted text intact."""
    lexer = shlex.shlex(HEREDOC.sub(" ", command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        raise Unreadable("could not parse the shell command")
    out, cur = [], []
    for tok in tokens:
        if tok in OPERATORS or set(tok) <= set(";&|()"):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def publish_kind(tokens):
    words = [t for t in tokens if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t)]
    for i, t in enumerate(words):
        nxt = words[i + 1:i + 3]
        if os.path.basename(t) == "gh" and nxt:
            if nxt[0] == "api":
                return "api"
            if len(nxt) == 2 and nxt[0] in ("pr", "issue") and nxt[1] in ("create", "edit", "comment", "review", "close", "merge"):
                return "gh"
            if len(nxt) == 2 and nxt[0] == "release" and nxt[1] in ("create", "edit"):
                return "gh"
        if os.path.basename(t) == "git" and nxt and nxt[0] in ("commit", "tag"):
            return "git"
    return None


def bash_texts(command, cwd):
    if not PUBLISH_BASH.search(command):
        return []
    variables = shell_vars(command)
    docs = [m.group(3) for m in HEREDOC.finditer(command)]
    texts, published = [], False
    for tokens in segments(command):
        kind = publish_kind(tokens)
        if not kind:
            continue
        published = True
        i = 0
        while i < len(tokens):
            flag, eq, inline = tokens[i].partition("=")
            if eq and flag.startswith("--"):
                val, step = inline, 1
            else:
                val, step = (tokens[i + 1] if i + 1 < len(tokens) else None), 2
            if kind == "api" and flag in API_FIELD_FLAGS and val:
                key, _, fval = val.partition("=")
                if key.lower() in API_TEXT_KEYS:
                    if fval.startswith("@"):
                        content = read_path(fval[1:], variables, cwd)
                        if content is not None:
                            texts.append(content)
                    else:
                        texts.append(fval)
                i += step
                continue
            if kind != "api" and flag in TEXT_FLAGS and val is not None:
                texts.append(val)
                i += step
                continue
            if val and (flag in FILE_FLAGS or (flag == "-F" and kind in ("git", "gh"))):
                content = read_path(val, variables, cwd)
                if content is not None:
                    texts.append(content)
                i += step
                continue
            i += 1
    if not published:
        return []
    checked = []
    for t in texts:
        if re.search(r"\$\(|`[^`]*`", strip_non_prose(t)):
            if docs:
                continue  # e.g. -m "$(cat <<'EOF' ...)", the heredoc is linted below
            raise Unreadable("command substitution in the message text")
        checked.append(t)
    return checked + docs


TEXT_KEYS = {"body", "text", "message", "content", "markdown", "description", "comment", "title", "notes", "summary"}


def string_fields(value, keys=TEXT_KEYS):
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str) and k.lower() in keys:
                out.append(v)
            elif isinstance(v, (dict, list)):
                out.extend(string_fields(v, keys))
    elif isinstance(value, list):
        for v in value:
            out.extend(string_fields(v, keys))
    return out


MCP_PUBLISH = re.compile(
    r"slack.*(send_message|update_message|schedule_message|canvas_edit|canvas_create|reminders_add)"
    r"|linear.*(save_comment|save_document|save_issue|save_diff_comment|save_status_update|save_project|save_release_note|submit_diff_review)"
    r"|github.*(create_pull_request|update_pull_request|add_.*comment|create_.*review|submit_.*review|create_issue|update_issue|reply)",
    re.I,
)

EXEMPT_DIRS = [os.path.expanduser(p) for p in ("~/.copilot", "~/.relay", "~/.claude")] + ["/tmp", "/private/tmp", "/var/folders"]


def markdown_texts(tool, args):
    path = args.get("path") or args.get("file_path") or ""
    if not path.lower().endswith((".md", ".mdx", ".markdown")):
        return []
    real = os.path.realpath(os.path.expanduser(path))
    if any(real.startswith(os.path.realpath(d) + os.sep) for d in EXEMPT_DIRS):
        return []
    if tool == "create":
        return [args.get("file_text", "")]
    old = set((args.get("old_str") or "").splitlines())
    new_lines = [ln for ln in (args.get("new_str") or "").splitlines() if ln not in old]
    return ["\n".join(new_lines)]


def texts_for(tool, args, cwd):
    name = (tool or "").lower()
    if name in ("bash", "powershell"):
        return bash_texts(args.get("command", ""), cwd)
    if name in ("create", "edit", "str_replace_editor"):
        return markdown_texts(name, args)
    if MCP_PUBLISH.search(name):
        return string_fields(args)
    return []


def gate(payload):
    tool = payload.get("toolName") or payload.get("tool_name") or ""
    args = payload.get("toolArgs", payload.get("tool_input", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    if not isinstance(args, dict):
        return {}
    cwd = payload.get("cwd") or os.getcwd()
    try:
        texts = texts_for(tool, args, cwd)
    except Unreadable as e:
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "write-like-me gate: couldn't read the prose this call publishes ({}). Write the text "
                "to a file and pass a literal path (for example --body-file /abs/path.md or "
                "-F body=@/abs/path.md) so it can be checked against {}."
            ).format(e, GUIDE),
        }
    problems = []
    for t in texts:
        problems.extend(lint(t))
    if not problems:
        return {}
    shown = problems[:12]
    more = len(problems) - len(shown)
    return {
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "write-like-me gate: this text breaks {guide}. Re-read the guide and redo its audit, "
            "critique and rewrite passes, then retry. Violations:\n- {items}{more}\n"
            "If a flagged word is a real technical term you can't reword, wrap it in backticks and "
            "say so in your note to the author."
        ).format(guide=GUIDE, items="\n- ".join(shown), more="\n- ...and {} more".format(more) if more > 0 else ""),
    }


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "gate"
    if mode == "lint":
        files = sys.argv[2:]
        data = "".join(open(f, encoding="utf-8").read() for f in files) if files else sys.stdin.read()
        problems = lint(data)
        print("\n".join(problems) if problems else "clean")
        return 1 if problems else 0
    raw = sys.stdin.read()
    if mode == "context":
        print(json.dumps({"additionalContext": CONTEXT}))
        return 0
    try:
        payload = json.loads(raw) if raw.strip() else {}
        out = gate(payload)
    except Exception as e:  # never let a hook bug deny every tool call
        sys.stderr.write("write-like-me gate error (allowing): {!r}\n".format(e))
        out = {}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
