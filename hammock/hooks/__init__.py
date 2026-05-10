"""Claude Code hooks shipped with Hammock.

Each hook is a standalone Python script invoked by ``claude`` per the
hooks contract documented at <https://docs.claude.com/en/docs/claude-code/hooks>.
We ship them under the ``hammock`` package so the runner can resolve
their absolute path via ``importlib.resources`` at job-submit time and
write them into ``<job_dir>/.claude/settings.json``.
"""
