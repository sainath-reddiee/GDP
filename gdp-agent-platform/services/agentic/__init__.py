"""Copilot workspace: one conversational agent over the platform's existing API routes (pure logic).

The agent reads and drafts through the same routes the pages call, as the signed-in person, so every privilege,
approval and four-eyes rule of the API still applies. Nothing here touches Snowflake directly: the API passes in a
`complete` function (the model call) and a `call` function (an in-process request to the API).

  plan.py       the eight steps of the plan rail
  changeset.py  drafted changes: one per asset, ordered for publishing, grouped for the diff workbench
  preview.py    diffs for the workbench (files, table model columns, STTM lines)
  tools.py      the tool catalog and argument checks
  prompt.py     the prompt for one model step
  loop.py       one conversational turn: model step, tool calls, question or reply
"""
