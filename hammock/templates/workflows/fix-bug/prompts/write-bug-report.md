Read the user's request and produce a structured bug report.

The bug report should:
- Distil the user's text into a concise, neutral summary of what's broken.
- Capture clear repro steps if the request describes them; leave the list empty if the request is too vague.
- Note the expected versus actual behaviour where the user has stated either.

You are running with the project's repository as your working directory. Read any project documentation (`CLAUDE.md`, README, ADRs) that helps you ground the report in the codebase's terminology — but do not fabricate details the user did not provide.
