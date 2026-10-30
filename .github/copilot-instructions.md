## Jira

When the user asks to create a Jira ticket, update a ticket description or fields, or comment on a Jira issue, read and follow `.claude/skills/jira/SKILL.md`. Run `python3 .claude/skills/jira/jira.py <create|update|update-description|add-comment>` in the terminal; credentials come from the `JIRA_BASE_URL` and `JIRA_PAT` environment variables and must never be written to files or printed.
