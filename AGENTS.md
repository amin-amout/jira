# Agent instructions

## Jira

When the user asks to create a Jira ticket, update a ticket description or fields, or comment on a Jira issue, follow `.claude/skills/jira/SKILL.md` (read it first). Use `python3 .claude/skills/jira/jira.py <create|update|update-description|add-comment>`; credentials come from `JIRA_BASE_URL` and `JIRA_PAT` environment variables and must never be written to files or printed.
