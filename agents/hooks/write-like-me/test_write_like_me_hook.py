import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import write_like_me_hook as hook  # noqa: E402

GOOD = "We drain the worker before the host reboots. It keeps serving shuffle data until then."
BAD = "We drain the worker \u2014 it keeps serving data; the drain is quiet."


def decide(tool, args, cwd=None):
    return hook.gate({"toolName": tool, "toolArgs": args, "cwd": cwd or HERE})


def denied(out):
    return out.get("permissionDecision") == "deny"


class LintTest(unittest.TestCase):
    def test_flags_em_dash_semicolon_and_banned_words(self):
        labels = " ".join(hook.lint(BAD))
        for want in ("em-dash", "semicolon", "quiet"):
            self.assertIn(want, labels)

    def test_ignores_code_quotes_urls_and_trailers(self):
        text = (
            "Fixed the bug.\n\n```\nfor o in a b; do echo $o; done\n```\n"
            "Run `kill -SIGTERM` to stop the control `loop`.\n"
            "> reviewer said: this drifts \u2014 badly; fix it\n"
            "See https://example.com/a;b and [docs](https://x.y/a;b).\n"
            "Co-authored-by: Copilot <x@y>\n"
        )
        self.assertEqual(hook.lint(text), [])

    def test_framing_patterns(self):
        self.assertTrue(hook.lint("This is not just a fix but a redesign."))
        self.assertTrue(hook.lint("It's not a bug, it's a feature."))


class GateTest(unittest.TestCase):
    def test_ignores_unrelated_tools_and_commands(self):
        self.assertEqual(decide("view", {"path": "/x.md"}), {})
        self.assertEqual(decide("bash", {"command": "ls; echo a \u2014 b"}), {})
        self.assertEqual(decide("bash", {"command": "gh pr view 1 --json body"}), {})
        self.assertEqual(decide("bash", {"command": "gh api repos/o/r/pulls/1/comments --jq '.[]|.body'"}), {})

    def test_gh_inline_body(self):
        self.assertTrue(denied(decide("bash", {"command": 'gh pr comment 1 --body "%s"' % BAD})))
        self.assertEqual(decide("bash", {"command": 'gh pr comment 1 --body "%s"' % GOOD}), {})

    def test_semicolon_inside_quoted_message_is_prose_not_shell(self):
        out = decide("bash", {"command": 'cd x && git commit -q -m "fix it; ship"'})
        self.assertTrue(denied(out))
        self.assertIn("semicolon", out["permissionDecisionReason"])

    def test_body_file_with_shell_variable(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(BAD)
        try:
            cmd = 'F=%s\ngh pr edit 46510 --body-file "$F" && echo done' % f.name
            self.assertTrue(denied(decide("bash", {"command": cmd})))
            with open(f.name, "w") as g:
                g.write(GOOD + "\n\n## Testing Done\n\n```\nfor o in a; do x; done\n```\n")
            self.assertEqual(decide("bash", {"command": cmd}), {})
        finally:
            os.unlink(f.name)

    def test_gh_api_review_reply_field(self):
        cmd = "gh api -X POST repos/o/r/pulls/1/comments/2/replies -f body='%s' --jq .html_url" % BAD.replace("'", "")
        self.assertTrue(denied(decide("bash", {"command": cmd})))
        cmd = "gh api -X POST repos/o/r/pulls/1/comments/2/replies -f body='done' --jq .html_url"
        self.assertEqual(decide("bash", {"command": cmd}), {})

    def test_commit_heredoc(self):
        cmd = "git add . && git commit -q -F - <<'EOF'\nfix(x): thing\n\nWe drain first; then exit.\nEOF\ngit push"
        self.assertTrue(denied(decide("bash", {"command": cmd})))
        cmd = 'git commit -m "$(cat <<\'EOF\'\nfix(x): thing\n\nWe drain first. Then exit.\nEOF\n)"'
        self.assertEqual(decide("bash", {"command": cmd}), {})

    def test_unreadable_body_is_denied(self):
        out = decide("bash", {"command": 'gh pr create --title t --body "$(cat body.md)"'})
        self.assertTrue(denied(out))
        self.assertIn("literal path", out["permissionDecisionReason"])

    def test_slack_and_linear_mcp(self):
        self.assertTrue(denied(decide("slack-slack_send_message", {"channel": "C1", "text": BAD})))
        self.assertTrue(denied(decide("slack-slack_canvas_edit", {"canvas_id": "F1", "content": BAD})))
        self.assertTrue(denied(decide("linear-server-save_comment", {"issueId": "1", "body": BAD})))
        self.assertEqual(decide("slack-slack_send_message", {"channel": "C1", "text": GOOD}), {})
        self.assertEqual(decide("slack-slack_search_messages", {"query": BAD}), {})

    def test_markdown_writes_only_lint_added_lines(self):
        with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as d:
            path = os.path.join(d, "README.md")
            self.assertTrue(denied(decide("create", {"path": path, "file_text": BAD})))
            old = "Existing line \u2014 left alone."
            self.assertEqual(decide("edit", {"path": path, "old_str": old, "new_str": old + "\n" + GOOD}), {})
            self.assertTrue(denied(decide("edit", {"path": path, "old_str": old, "new_str": old + "\n" + BAD})))
            self.assertEqual(decide("create", {"path": os.path.join(d, "main.go"), "file_text": BAD}), {})

    def test_session_artifacts_are_exempt(self):
        path = os.path.expanduser("~/.copilot/session-state/x/files/plan.md")
        self.assertEqual(decide("create", {"path": path, "file_text": BAD}), {})


class CliTest(unittest.TestCase):
    def run_hook(self, mode, stdin):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "write_like_me_hook.py"), mode],
            input=stdin, capture_output=True, text=True, check=False,
        )

    def test_context_mode(self):
        res = self.run_hook("context", "{}")
        self.assertEqual(res.returncode, 0)
        self.assertIn("write-like-me.md", json.loads(res.stdout)["additionalContext"])

    def test_gate_fails_open_on_garbage(self):
        res = self.run_hook("gate", "not json")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout), {})

    def test_gate_denies_via_stdin(self):
        payload = {"toolName": "bash", "toolArgs": json.dumps({"command": 'gh pr comment 1 -b "a; b"'}), "cwd": "/"}
        res = self.run_hook("gate", json.dumps(payload))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout)["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
