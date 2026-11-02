# Agent Instructions

## Approval for invasive actions

Always obtain the user's explicit approval immediately before taking an
invasive action. This includes terminating or restarting workloads and
processes, deleting or mutating resources or data, sending messages, and other
actions that can disrupt users or production systems.

Earlier approval based on assumptions does not apply if investigation reveals
that the target is active or the impact differs from what the user understood.
Stop, explain the current state and expected impact, and wait for renewed
approval.

## Writing for humans

Whenever you write or edit prose that people will read (PR descriptions, design
docs, READMEs, Slack messages and canvases, review comments, summaries), follow
[write-like-me.md](write-like-me.md) (`/Users/vsowrira/write-like-me.md`). For
substantial drafts, run its audit, critique and rewrite passes; for short
messages, apply its rules before sending.

## Spark

To find a running Spark job's UI on Falcon, follow [SPARK.md](SPARK.md)
(`/Users/vsowrira/SPARK.md`).

## Maintaining this file

This file, `write-like-me.md` and `SPARK.md` are versioned in
[venkata91/myconf](https://github.com/venkata91/myconf) under `agents/`. When
you change any of them, commit the same change there.

Every session loads this file, so keep it small and evidence-based:

- Add a rule only for a mistake actually observed or a preference I've stated.
  Write the rule, not the story behind it.
- Put long or situational guidance in its own file and link to it, as with
  `write-like-me.md`. Only this file is loaded every time.
- Before merging, shortening or deleting a rule, check that the remaining text
  still requires every behavior the old text did. If you can't tell, keep it.
- In each commit message, say what changed and why, and give the word count
  before and after (`wc -w`).
