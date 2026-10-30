#!/usr/bin/env python3
"""Config-driven Jira client. Operations, payloads and defaults live in jira-agent.config.yml.

  jira.py ops [name]                       list operations / show one
  jira.py <operation> [key=value ...]      run an operation (--dry-run prints the request)

Python 3 standard library only.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

PH = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
ENV = re.compile(r"\$\{([A-Za-z_]\w*)(?::-([^}]*))?\}")
API = {"version": "2"}


def die(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- YAML subset
# Block maps/lists, "|" / "|-" block scalars, [a, b] / {a: b} flow values, quoted or plain scalars
# (true/false/null/numbers are typed), full-line and trailing "#" comments. No anchors, no tags.

def read_quoted(s, i):
    q, out, i = s[i], [], i + 1
    while i < len(s):
        c = s[i]
        if q == '"' and c == "\\" and i + 1 < len(s):
            out.append({"n": "\n", "t": "\t"}.get(s[i + 1], s[i + 1]))
            i += 2
        elif c == q:
            if q == "'" and s[i + 1:i + 2] == "'":
                out.append("'")
                i += 2
            else:
                return "".join(out), i + 1
        else:
            out.append(c)
            i += 1
    raise ValueError("unterminated quote in: " + s)


def typed(s):
    if s in ("true", "false"):
        return s == "true"
    if s in ("", "null", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def flow(s, i):
    def ws(j):
        while j < len(s) and s[j] in " \t":
            j += 1
        return j

    i = ws(i)
    if i >= len(s):
        raise ValueError("unexpected end of flow value: " + s)
    c = s[i]
    if c == "[":
        out, i = [], ws(i + 1)
        while s[i:i + 1] != "]":
            v, i = flow(s, i)
            out.append(v)
            i = ws(i)
            if s[i:i + 1] == ",":
                i = ws(i + 1)
            elif s[i:i + 1] != "]":
                raise ValueError("expected ',' or ']' in: " + s)
        return out, i + 1
    if c == "{":
        out, i = {}, ws(i + 1)
        while s[i:i + 1] != "}":
            if s[i:i + 1] in ("'", '"'):
                k, i = read_quoted(s, i)
            else:
                j = i
                while j < len(s) and s[j] not in ":,}":
                    j += 1
                k, i = s[i:j].strip(), j
            i = ws(i)
            if s[i:i + 1] != ":":
                raise ValueError("expected ':' in: " + s)
            v, i = flow(s, i + 1)
            out[k] = v
            i = ws(i)
            if s[i:i + 1] == ",":
                i = ws(i + 1)
            elif s[i:i + 1] != "}":
                raise ValueError("expected ',' or '}' in: " + s)
        return out, i + 1
    if c in ("'", '"'):
        return read_quoted(s, i)
    j = i
    while j < len(s) and s[j] not in ",]}":
        j += 1
    return typed(s[i:j].strip()), j


def scalar(s):
    if s[:1] in ("[", "{"):
        v, i = flow(s, 0)
    elif s[:1] in ("'", '"'):
        v, i = read_quoted(s, 0)
    else:
        return typed(s)
    if s[i:].strip():
        raise ValueError("unexpected text after value: " + s)
    return v


def strip_comment(s):
    q, i = None, 0
    while i < len(s):
        c = s[i]
        if q:
            if c == "\\" and q == '"':
                i += 1
            elif c == q:
                q = None
        elif c in "\"'" and (i == 0 or s[i - 1] in " [{,:"):
            q = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
        i += 1
    return s.strip()


def split_key(s):
    """'key: rest' -> (key, rest); None if the line is not a mapping entry."""
    if s[:1] in ("'", '"'):
        try:
            key, i = read_quoted(s, 0)
        except ValueError:
            return None
        if s[i:i + 1] == ":" and s[i + 1:i + 2] in ("", " "):
            return key, s[i + 1:].strip()
        return None
    m = re.match(r"([^\s:\[{'\"#][^:]*?)\s*:(?:\s+(.*))?$", s)
    return (m.group(1), m.group(2) or "") if m else None


def parse_yaml(text):
    lines = text.splitlines()
    pos = 0

    def err(msg):
        raise ValueError("line %d: %s" % (pos + 1, msg))

    def indent_of(line):
        return len(line) - len(line.lstrip(" "))

    def skip():
        nonlocal pos
        while pos < len(lines) and (not lines[pos].strip() or lines[pos].lstrip().startswith("#")):
            pos += 1

    def is_dash(body):
        return body == "-" or body.startswith("- ")

    def block(ind):
        skip()
        return seq(ind) if is_dash(lines[pos].strip()) else mapping(ind)

    def block_scalar(ind):
        nonlocal pos
        blk = []
        while pos < len(lines) and (not lines[pos].strip() or indent_of(lines[pos]) > ind):
            blk.append(lines[pos])
            pos += 1
        width = min([indent_of(x) for x in blk if x.strip()] or [0])
        return "\n".join(x[width:] if x.strip() else "" for x in blk).strip("\n")

    def nested(ind):
        """Value of an empty 'key:' / '-': a deeper block, or None."""
        skip()
        if pos < len(lines):
            cur = indent_of(lines[pos])
            if cur > ind or (cur == ind and is_dash(lines[pos].strip())):
                return block(cur)
        return None

    def mapping(ind):
        nonlocal pos
        out = {}
        while True:
            skip()
            if pos >= len(lines) or indent_of(lines[pos]) < ind:
                return out
            if indent_of(lines[pos]) > ind:
                err("unexpected indentation")
            body = lines[pos].strip()
            if is_dash(body):
                return out
            kv = split_key(body)
            if kv is None:
                err("expected 'key: value', got: " + body)
            key, rest = kv[0], strip_comment(kv[1])
            pos += 1
            if rest in ("|", "|-"):
                out[key] = block_scalar(ind)
            elif rest == "":
                out[key] = nested(ind)
            else:
                try:
                    out[key] = scalar(rest)
                except ValueError as e:
                    raise ValueError("line %d: %s" % (pos, e))

    def seq(ind):
        nonlocal pos
        out = []
        while True:
            skip()
            if pos >= len(lines) or indent_of(lines[pos]) != ind or not is_dash(lines[pos].strip()):
                return out
            line = lines[pos]
            start = ind + 1
            while start < len(line) and line[start] == " ":
                start += 1
            rest = strip_comment(line[start:])
            if rest == "":
                pos += 1
                out.append(nested(ind))
            elif split_key(rest) is not None:
                lines[pos] = " " * start + line[start:]
                out.append(mapping(start))
            else:
                pos += 1
                try:
                    out.append(scalar(rest))
                except ValueError as e:
                    raise ValueError("line %d: %s" % (pos, e))

    try:
        skip()
        result = block(indent_of(lines[pos])) if pos < len(lines) else {}
        skip()
        if pos < len(lines):
            err("unexpected content")
        return result
    except ValueError as e:
        raise ValueError(str(e) if str(e).startswith("line ") else "line %d: %s" % (pos + 1, e))


def interpolate(node):
    """Replace ${VAR} / ${VAR:-default} in config values (never in keys)."""
    if isinstance(node, dict):
        return {k: interpolate(v) for k, v in node.items()}
    if isinstance(node, list):
        return [interpolate(v) for v in node]
    if isinstance(node, str):
        return ENV.sub(lambda m: os.environ.get(m.group(1)) or m.group(2) or "", node)
    return node


# ------------------------------------------------------------------ templating

def empty(v):
    return v is None or (isinstance(v, (str, list, dict)) and not v)


def to_text(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(to_text(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def f_list(v, _):
    if empty(v):
        return []
    return v if isinstance(v, list) else [x.strip() for x in str(v).split(",") if x.strip()]


def f_rich(v, _):
    """Plain text on API v2; Atlassian Document Format on API v3."""
    if empty(v):
        return ""
    v = to_text(v)
    if API["version"] != "3":
        return v
    paras = [{"type": "paragraph", "content": [{"type": "text", "text": ln}]}
             for ln in v.splitlines() if ln.strip()]
    return {"type": "doc", "version": 1, "content": paras}


def f_number(v, _):
    if empty(v):
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        die("not a number: %r" % (v,))
    return int(f) if f == int(f) else f


FILTERS = {
    "list": f_list,
    "rich": f_rich,
    "number": f_number,
    "upper": lambda v, _: to_text(v).upper(),
    "lower": lambda v, _: to_text(v).lower(),
    "default": lambda v, a: a if empty(v) else v,  # {{ x | default:Medium }}
    "then": lambda v, a: "" if empty(v) else a,    # {{ x | then:role }} -> literal only when x is set
}


def apply(expr, look):
    name, *filters = [p.strip() for p in expr.split("|")]
    v = look(name)
    for f in filters:
        fname, _, arg = f.partition(":")
        fn = FILTERS.get(fname.strip())
        if fn is None:
            die("unknown filter '%s' in {{ %s }}" % (fname.strip(), expr))
        v = fn(v, arg.strip())
    return v


def render_str(s, look):
    m = PH.fullmatch(s.strip())
    if m:  # whole value is one placeholder: keep its type (list, number, ADF document...)
        return apply(m.group(1), look)
    return PH.sub(lambda m: to_text(apply(m.group(1), look)), s)


def render(node, look):
    """Render placeholders; entries that end up empty are dropped (optional by omission)."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            key, val = to_text(render_str(str(k), look)), render(v, look)
            if key and not empty(val):
                out[key] = val
        return out
    if isinstance(node, list):
        return [r for r in (render(x, look) for x in node) if not empty(r)]
    if isinstance(node, str):
        return render_str(node, look)
    return node


def make_look(cli, defaults, builtins):
    depth = [0]

    def look(name):
        head, *path = name.split(".")
        if head in cli:
            v = cli[head]  # command-line values are literal: never re-scanned for placeholders
        elif head in defaults:
            depth[0] += 1
            if depth[0] > 40:
                die("placeholder loop while resolving '%s'" % head)
            v = render(defaults[head], look)
            depth[0] -= 1
        else:
            v = builtins.get(head)
        for k in path:
            if isinstance(v, dict):
                v = v.get(k)
            elif isinstance(v, list) and k.isdigit() and int(k) < len(v):
                v = v[int(k)]
            else:
                return None
        return v

    return look


def merge(a, b):
    """Deep-merge dicts (b wins); a null in b deletes the key."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if v is None:
                out.pop(k, None)
            else:
                out[k] = merge(a[k], v) if k in a else v
        return out
    return b


def expand(node, registry, seen=()):
    """Resolve `extends: name | [names]` against a registry (templates or operations)."""
    if not isinstance(node, dict) or "extends" not in node:
        return node
    names = node["extends"] if isinstance(node["extends"], list) else [node["extends"]]
    out = {}
    for n in names:
        if n not in registry or n in seen:
            die("bad 'extends: %s' (unknown or circular)" % n)
        out = merge(out, expand(registry[n], registry, seen + (n,)))
    return merge(out, {k: v for k, v in node.items() if k != "extends"})


def refs(node):
    return [e.split("|")[0].split(".")[0].strip() for e in PH.findall(json.dumps(node, ensure_ascii=False))]


# ------------------------------------------------------------------ git / config

def git_vars(cfg):
    g = cfg.get("git") or {}

    def run(*args):
        try:
            return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL,
                                           encoding="utf-8", errors="replace").strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    parts = (run("log", "-1", "--pretty=format:%H%x00%h%x00%an%x00%s").split("\0") + [""] * 4)[:4]
    h, short, author, subject = parts
    b = {"commit_hash": h, "commit_short": short, "author": author,
         "commit_subject": subject or to_text(g.get("fallback_title")),
         "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
         "repo": os.path.basename(run("rev-parse", "--show-toplevel"))}
    if h:
        b["git_context"] = to_text(render_str(to_text(g.get("context")), lambda n: b.get(n)))
    else:
        b["git_context"] = to_text(g.get("fallback_context"))
    return b


def load_config(explicit):
    here = os.path.dirname(os.path.abspath(__file__))
    env = os.environ.get("JIRA_AGENT_CONFIG")
    for path, must in ((explicit, True), (env, True), ("jira-agent.config.yml", False),
                       (os.path.join(here, "jira-agent.config.yml"), False)):
        if not path:
            continue
        if os.path.isfile(path):
            break
        if must:
            die("config file not found: " + path)
    else:
        die("no jira-agent.config.yml found (searched cwd and %s)" % here)
    try:
        with open(path, encoding="utf-8") as fh:
            return interpolate(parse_yaml(fh.read())) or {}
    except (OSError, ValueError) as e:
        die("%s: %s" % (path, e))


# ------------------------------------------------------------------------ HTTP

def explain(body):
    try:
        j = json.loads(body)
        parts = list(j.get("errorMessages") or []) + ["%s: %s" % kv for kv in (j.get("errors") or {}).items()]
        if parts:
            return "; ".join(parts)
    except (ValueError, AttributeError):
        pass
    return body.strip()[:2000]


def send(method, url, headers, data, timeout):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        hint = {401: " (check JIRA_PAT / JIRA_EMAIL)", 403: " (permission denied)",
                404: " (issue or project not found, or no permission to see it)"}.get(e.code, "")
        die("HTTP %d %s%s: %s" % (e.code, e.reason, hint, explain(e.read().decode("utf-8", "replace"))))
    except urllib.error.URLError as e:
        die("connection failed: %s" % e.reason)
    try:
        return status, (json.loads(raw) if raw.strip() else {})
    except ValueError:
        return status, {"text": raw}


def multipart(path):
    if not os.path.isfile(path):
        die("file not found: " + path)
    b = "----jira" + os.urandom(8).hex()
    with open(path, "rb") as fh:
        content = fh.read()
    name = os.path.basename(path).replace('"', "_")
    head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n' % (b, name)).encode("utf-8")
    return head + content + ("\r\n--%s--\r\n" % b).encode(), "multipart/form-data; boundary=" + b


# ------------------------------------------------------------------------ CLI

def list_ops(cfg, builtins, name):
    ops = cfg.get("operations") or {}
    if name and name not in ops:
        die("unknown operation '%s'. Known: %s" % (name, ", ".join(ops)))
    defaults = cfg.get("defaults") or {}
    for n in ([name] if name else ops):
        op = expand(ops[n], ops)
        if not name:
            print("%-20s %-6s %s  - %s" % (n, str(op.get("method", "")).upper(), op.get("path", ""), op.get("about", "")))
            continue
        d = merge(defaults, op.get("defaults") or {})
        payload = expand(op.get("payload"), cfg.get("templates") or {})
        used = [r for r in dict.fromkeys(refs([op.get("path"), payload, op.get("headers"), op.get("attach")]))
                if r not in builtins and r not in ("response", "") and r not in (op.get("required") or [])]
        print("%s: %s" % (n, op.get("about", "")))
        print("  %s %s" % (str(op.get("method", "")).upper(), op.get("path", "")))
        print("  required: %s" % (", ".join(dict.fromkeys(list(op.get("required") or []) + refs(op.get("path")))) or "-"))
        if op.get("require_any"):
            print("  at least one of: " + ", ".join(op["require_any"]))
        print("  optional: %s" % (", ".join(used) or "-"))
        relevant = set(used) | set(op.get("required") or [])
        shown = ["%s=%s" % (k, to_text(v)) for k, v in d.items() if k in relevant and not empty(v)]
        print("  defaults: %s" % ("; ".join(shown) or "-"))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(prog="jira.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", help="operation name, or 'ops' to list operations")
    ap.add_argument("pairs", nargs="*", metavar="key=value", help="values; use key=@file to read a file")
    ap.add_argument("--config", help="config file (default: $JIRA_AGENT_CONFIG, ./jira-agent.config.yml, next to this script)")
    ap.add_argument("--dry-run", action="store_true", help="print the request instead of sending it")
    a = ap.parse_args()

    cfg = load_config(a.config)
    cli = {}
    if a.op != "ops":
        for p in a.pairs:
            k, sep, v = p.partition("=")
            if not sep or not k.strip():
                die("expected key=value, got: " + p)
            if v.startswith("@"):
                try:
                    with open(v[1:], encoding="utf-8") as fh:
                        v = fh.read().rstrip("\n")
                except OSError as e:
                    die("cannot read %s: %s" % (v[1:], e))
            cli[k.strip()] = v

    project = cli.get("project") or to_text((cfg.get("defaults") or {}).get("project"))
    over = (cfg.get("projects") or {}).get(project)
    if isinstance(over, dict):
        cfg = merge(cfg, over)
    conn = cfg.get("connection") or {}
    API["version"] = str(conn.get("api_version") or "2")
    base = to_text(conn.get("base_url")).rstrip("/")
    builtins = git_vars(cfg)
    builtins.update(api_version=API["version"], base_url=base)

    if a.op == "ops":
        list_ops(cfg, builtins, a.pairs[0] if a.pairs else None)
        return
    ops = cfg.get("operations") or {}
    if a.op not in ops:
        die("unknown operation '%s'. Known: %s" % (a.op, ", ".join(ops)))
    op = expand(ops[a.op], ops)
    templates = cfg.get("templates") or {}
    look = make_look(cli, merge(cfg.get("defaults") or {}, op.get("defaults") or {}), builtins)

    need = list(dict.fromkeys(list(op.get("required") or []) + refs(op.get("path"))))
    missing = [n for n in need if empty(look(n))]
    if missing:
        die("operation '%s' needs: %s. Pass as key=value (run 'jira.py ops %s' for details)."
            % (a.op, ", ".join(missing), a.op))
    any_of = op.get("require_any") or []
    if any_of and all(empty(look(n)) for n in any_of):
        die("operation '%s' needs at least one of: %s" % (a.op, ", ".join(any_of)))

    method = str(op.get("method") or "GET").upper()
    prefix = to_text(render_str(to_text(op.get("prefix", conn.get("api_prefix"))), look))
    url = base + prefix + to_text(render_str(to_text(op.get("path")), look))
    extra = {k: to_text(v) for k, v in render(op.get("headers") or {}, look).items()}
    attach = to_text(render_str(to_text(op["attach"]), look)) if op.get("attach") else ""
    body = render(expand(op["payload"], templates), look) if "payload" in op else None

    if a.dry_run:
        print(method, url if base else url.replace(prefix, "(JIRA_BASE_URL not set)" + prefix, 1))
        for k, v in extra.items():
            print("%s: %s" % (k, v))
        print("file: " + attach if attach else json.dumps(body, indent=2, ensure_ascii=False) if body is not None else "(no body)")
        return

    names = conn.get("token_env") or ["JIRA_PAT", "JIRA_TOKEN"]
    token = next((os.environ[n] for n in ([names] if isinstance(names, str) else names) if os.environ.get(n)), "")
    if not base or not token:
        die("set JIRA_BASE_URL and JIRA_PAT (or JIRA_TOKEN)")
    user = to_text(conn.get("user"))
    headers = {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (user, token)).encode()).decode()
               if user else "Bearer " + token, "Accept": "application/json"}
    data = None
    if attach:
        data, headers["Content-Type"] = multipart(attach)
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
    headers.update(extra)

    status, resp = send(method, url, headers, data, float(conn.get("timeout") or 30))
    builtins["response"] = resp
    if op.get("result"):
        print(to_text(render_str(to_text(op["result"]), look)))
    elif resp:
        print(json.dumps(resp, indent=2, ensure_ascii=False))
    else:
        print("OK (HTTP %d)" % status)


if __name__ == "__main__":
    main()
