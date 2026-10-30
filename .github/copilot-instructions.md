## Jira

When the user asks to create a Jira ticket, update a ticket's description or fields, comment, assign, transition, log work or attach a file, read and follow `.claude/skills/jira/SKILL.md`. Start with `python3 .claude/skills/jira/jira.py ops` to see the available operations, then run `python3 .claude/skills/jira/jira.py <operation> key=value ...` in the terminal. Operations, defaults and per-project settings live in `jira-agent.config.yml`. Credentials come from the `JIRA_BASE_URL` and `JIRA_PAT` environment variables and must never be written to files or printed.
