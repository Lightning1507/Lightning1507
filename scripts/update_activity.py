"""Generate an annual public-contributions card using the GitHub GraphQL API."""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.request import Request, urlopen

FIELDS = {
    "commits": "commitContributionsByRepository",
    "pull_requests": "pullRequestContributionsByRepository",
    "issues": "issueContributionsByRepository",
    "reviewed_prs": "pullRequestReviewContributionsByRepository",
}
ROOT = Path(__file__).resolve().parents[1]
SCOPE = "public repositories only"


def timestamp(now):
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_payload(username, token, now):
    if not token:
        raise ValueError("Set GH_TOKEN or GITHUB_TOKEN to read GitHub contributions.")
    selections = "\n".join(
        f"{field}(maxRepositories: 100) {{ repository {{ isPrivate }} contributions(first: 1) {{ totalCount }} }}"
        for field in FIELDS.values()
    )
    query = """query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) { contributionsCollection(from: $from, to: $to) {
        %s
      } }
    }""" % selections
    body = json.dumps({"query": query, "variables": {
        "login": username,
        "from": timestamp(now.replace(month=1, day=1, hour=0, minute=0, second=0)),
        "to": timestamp(now),
    }}).encode("utf-8")
    request = Request("https://api.github.com/graphql", data=body, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "Lightning1507-profile-activity",
    })
    with urlopen(request, timeout=45) as result:
        return json.load(result)


def build_snapshot(payload, username, now):
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("GitHub returned an API error; previous statistics retained.")
    try:
        collection = payload["data"]["user"]["contributionsCollection"]
        metrics = {}
        for metric, field in FIELDS.items():
            entries = collection[field]
            if not isinstance(entries, list) or len(entries) >= 100:
                raise ValueError("Repository limit reached or invalid contribution list.")
            total = 0
            for entry in entries:
                private = entry["repository"]["isPrivate"]
                count = entry["contributions"]["totalCount"]
                if type(private) is not bool or type(count) is not int or count < 0:
                    raise ValueError("Invalid repository visibility or contribution count.")
                if not private:
                    total += count
            metrics[metric] = total
    except (KeyError, TypeError) as error:
        raise ValueError("Missing GitHub contribution data; previous statistics retained.") from error
    snapshot = {
        "username": username,
        "scope": SCOPE,
        "period_start": timestamp(now.replace(month=1, day=1, hour=0, minute=0, second=0)),
        "period_end": timestamp(now),
        "updated_at": timestamp(now),
        "metrics": metrics,
    }
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("scope") != SCOPE:
        raise ValueError("Only public contribution aggregates are supported.")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", snapshot.get("username", "")):
        raise ValueError("Invalid GitHub username.")
    try:
        dates = [datetime.strptime(snapshot[key], "%Y-%m-%dT%H:%M:%SZ")
                 for key in ("period_start", "period_end", "updated_at")]
        start, end, updated = dates
        if start != datetime(end.year, 1, 1) or updated != end or end < start:
            raise ValueError("Invalid annual contribution period.")
        metrics = snapshot["metrics"]
        if set(metrics) != set(FIELDS) or any(type(n) is not int or n < 0 for n in metrics.values()):
            raise ValueError("Invalid aggregate counts.")
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid saved contribution snapshot.") from error


def render_svg(snapshot):
    validate_snapshot(snapshot)
    year = snapshot["period_start"][:4]
    updated = snapshot["updated_at"].replace("T", " ").replace("Z", " UTC")
    labels = {"commits": "Commit contributions", "pull_requests": "Pull requests",
              "issues": "Issues", "reviewed_prs": "Reviewed PRs"}
    cards = []
    for index, (metric, label) in enumerate(labels.items()):
        x = 32 + (index % 2) * 378
        y = 90 + (index // 2) * 110
        count = format(snapshot["metrics"][metric], ",")
        cards.append(f'''<g transform="translate({x} {y})">
          <rect width="358" height="96" rx="12" fill="#122335" stroke="#243c50"/>
          <rect x="0" y="20" width="3" height="56" rx="1.5" fill="{'#f4c65b' if index == 0 else '#70d8e4'}"/>
          <text x="22" y="43" fill="#f0f6fc" font-size="38" font-weight="700">{count}</text>
          <text x="22" y="75" fill="#b5cedd" font-size="21">{label}</text>
        </g>''')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="366" viewBox="0 0 800 366" role="img" aria-labelledby="title desc">
      <title id="title">{escape(snapshot['username'])} — public GitHub activity, {year}</title>
      <desc id="desc">{escape('; '.join(f'{labels[k]}: {v}' for k, v in snapshot['metrics'].items()))}. January 1 through {updated}.</desc>
      <rect width="800" height="366" rx="16" fill="#0d1826"/>
      <g font-family="Segoe UI,Arial,sans-serif">
        <text x="32" y="43" fill="#f0f6fc" font-size="25" font-weight="650">A year of building</text>
        <text x="768" y="43" text-anchor="end" fill="#f4c65b" font-size="25" font-weight="650">{year}</text>
        <text x="32" y="71" fill="#b5cedd" font-size="17">January 1 to date · Public repositories only</text>
        {''.join(cards)}
        <text x="32" y="340" fill="#a5bdcf" font-size="16">Updated {updated}</text>
      </g>
    </svg>
'''


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_outputs(snapshot, output_dir):
    svg = render_svg(snapshot)
    # Whitelist the persisted fields so raw API payloads can never be published.
    public = {key: snapshot[key] for key in
              ("username", "scope", "period_start", "period_end", "updated_at", "metrics")}
    atomic_write(output_dir / "assets" / "activity.svg", svg)
    atomic_write(output_dir / "data" / "activity.json", json.dumps(public, indent=2) + "\n")


def refresh(username, token, output_dir=ROOT, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    snapshot = build_snapshot(fetch_payload(username, token, now), username, now)
    write_outputs(snapshot, output_dir)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="Lightning1507")
    parser.add_argument("--from-json", type=Path)
    args = parser.parse_args()
    try:
        if args.from_json:
            snapshot = json.loads(args.from_json.read_text(encoding="utf-8"))
            write_outputs(snapshot, ROOT)
        else:
            snapshot = refresh(args.username, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
        print(json.dumps(snapshot["metrics"]))
        return 0
    except Exception as error:
        # Do not print raw API responses, request headers, or credentials.
        print(f"Activity update failed ({type(error).__name__}). Previous data retained.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
