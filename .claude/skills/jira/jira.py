#!/usr/bin/env python3
"""Minimal Jira client: create | update | update-description | add-comment.

Reads a flat YAML template, fills {{git placeholders}}, applies --set overrides,
and calls the Jira REST API. Python 3 standard library only.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request

VER = os.environ.get("JIRA_API_VERSION", "2")  # "2" = Server/DC (and Cloud); "3" = Cloud (ADF)
LIST_KEYS = ("labels", "components")


def die(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


def git(*args):
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def git_vars():
    commit = git("log", "-1", "--pretty=%H")
    subject = git("log", "-1", "--pretty=%s")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if commit:
        ctx = "Commit: %s\nBranch: %s\nSubject: %s" % (commit, branch, subject)
    else:
        ctx = "Manual ticket – no commit hash available"
    return {
        "commit": commit or "no commit hash available",
        "commit_subject": subject or "Manual ticket",
        "branch": branch or "unknown",
        "git_context": ctx,
    }


def parse_yaml(text):
    """Flat YAML subset: 'key: value', quoted strings, [a, b] lists, '|' blocks, full-line # comments."""
    data, lines, i = {}, text.splitlines(), 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw or raw[0] in " \t":
            die("cannot parse template line: " + raw)
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        if val in ("|", ">"):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
                block.append(lines[i])
                i += 1
            data[key] = textwrap.dedent("\n".join(block)).strip("\n")
        elif val[:1] in ("'", '"'):
            end = val.rfind(val[0])
            if end == 0:
                die("unterminated quote in template line: " + raw)
            data[key] = val[1:end]
        else:
            val = re.split(r"\s#", val)[0].strip()
            if val.startswith("[") and val.endswith("]"):
                data[key] = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
            else:
                data[key] = val
    return data


def fill(value, variables):
    if isinstance(value, list):
        return [fill(x, variables) for x in value]
    if not isinstance(value, str):
        return value

    def sub(m):
        if m.group(1) not in variables:
            die("unknown placeholder {{%s}}" % m.group(1))
        return variables[m.group(1)]

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, value)


def text_body(text):
    if VER != "3":
        return text
    paras = [{"type": "paragraph", "content": [{"type": "text", "text": ln}]}
             for ln in text.splitlines() if ln.strip()]
    return {"type": "doc", "version": 1, "content": paras}


def build_fields(cfg):
    cloud = bool(os.environ.get("JIRA_EMAIL"))  # Cloud identifies users by accountId; Server/DC by name
    f = {}
    if "project" in cfg:
        f["project"] = {"key": cfg["project"]}
    if "issuetype" in cfg:
        f["issuetype"] = {"name": cfg["issuetype"]}
    for k in ("summary", "duedate"):
        if k in cfg:
            f[k] = cfg[k]
    if "description" in cfg:
        f["description"] = text_body(cfg["description"])
    if "priority" in cfg:
        f["priority"] = {"name": cfg["priority"]}
    if "assignee" in cfg:
        f["assignee"] = {("accountId" if cloud else "name"): cfg["assignee"]}
    if "labels" in cfg:
        f["labels"] = cfg["labels"]
    if "components" in cfg:
        f["components"] = [{"name": c} for c in cfg["components"]]
    if "duration" in cfg:
        f["timetracking"] = {"originalEstimate": cfg["duration"]}
    return f


def need(cfg, *keys):
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        die("missing required field(s): " + ", ".join(missing))


def call(method, path, payload, dry_run):
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    url = "%s/rest/api/%s%s" % (base or "$JIRA_BASE_URL", VER, path)
    if dry_run:
        print(method, url)
        print(json.dumps(payload, indent=2))
        sys.exit(0)
    token = os.environ.get("JIRA_PAT") or os.environ.get("JIRA_TOKEN")
    if not base or not token:
        die("set JIRA_BASE_URL and JIRA_PAT (or JIRA_TOKEN)")
    email = os.environ.get("JIRA_EMAIL")
    if email:
        auth = "Basic " + base64.b64encode(("%s:%s" % (email, token)).encode()).decode()
    else:
        auth = "Bearer " + token
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method=method,
        headers={"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        die("HTTP %d %s: %s" % (e.code, e.reason, e.read().decode("utf-8", "replace")))
    except urllib.error.URLError as e:
        die("connection failed: %s" % e.reason)
    return json.loads(body) if body else {}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["create", "update", "update-description", "add-comment"])
    ap.add_argument("--template", help="YAML template (default: templates/<command>.yaml next to this script)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a template field")
    ap.add_argument("--dry-run", action="store_true", help="print the request instead of sending it")
    args = ap.parse_args()

    path = args.template or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "templates", args.command + ".yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = parse_yaml(fh.read())
    except OSError as e:
        die("cannot read template: %s" % e)
    for item in args.set:
        k, sep, v = item.partition("=")
        if not sep:
            die("--set expects KEY=VALUE, got: " + item)
        cfg[k.strip()] = v
    cfg = {k: fill(v, git_vars()) for k, v in cfg.items() if v not in ("", [])}
    for k in LIST_KEYS:
        if isinstance(cfg.get(k), str):
            cfg[k] = [x.strip() for x in cfg[k].split(",") if x.strip()]

    if args.command == "create":
        need(cfg, "project", "issuetype", "summary")
        res = call("POST", "/issue", {"fields": build_fields(cfg)}, args.dry_run)
        if res:
            print("Created %s: %s/browse/%s" % (res["key"], os.environ["JIRA_BASE_URL"].rstrip("/"), res["key"]))
    elif args.command == "update":
        need(cfg, "issue")
        fields = build_fields({k: v for k, v in cfg.items() if k not in ("issue", "project", "issuetype")})
        if not fields:
            die("nothing to update: set at least one field besides 'issue'")
        call("PUT", "/issue/" + cfg["issue"], {"fields": fields}, args.dry_run)
        print("Updated " + cfg["issue"])
    elif args.command == "update-description":
        need(cfg, "issue", "description")
        call("PUT", "/issue/" + cfg["issue"],
             {"fields": {"description": text_body(cfg["description"])}}, args.dry_run)
        print("Updated description of " + cfg["issue"])
    else:
        need(cfg, "issue", "body")
        payload = {"body": text_body(cfg["body"])}
        if cfg.get("visibility_role"):
            payload["visibility"] = {"type": "role", "value": cfg["visibility_role"]}
        res = call("POST", "/issue/%s/comment" % cfg["issue"], payload, args.dry_run)
        print("Added comment %s to %s" % (res.get("id", ""), cfg["issue"]))


if __name__ == "__main__":
    main()
