"""Update the README's annual public-contribution summary using GitHub GraphQL."""

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone, timedelta
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
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def timestamp(now):
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def completed_weeks(now):
    now = now.astimezone(timezone.utc)
    monday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    return [(monday - timedelta(weeks=12-i), monday - timedelta(weeks=11-i, seconds=1))
            for i in range(12)]


def fetch_payload(username, token, now):
    if not token:
        raise ValueError("Set GH_TOKEN or GITHUB_TOKEN to read GitHub contributions.")
    selections = "\n".join(
        f"{field}(maxRepositories: 100) {{ repository {{ id isPrivate }} contributions(first: 1) {{ totalCount }} }}"
        for field in FIELDS.values()
    )
    weeks = "\n".join(
        f'week{i}: contributionsCollection(from: "{timestamp(start)}", to: "{timestamp(end)}") '
        '{ commitContributionsByRepository(maxRepositories: 100) '
        '{ repository { id isPrivate } contributions(first: 1) { totalCount } } }'
        for i, (start, end) in enumerate(completed_weeks(now))
    )
    query = """query($login: String!, $from: DateTime!, $to: DateTime!, $opened: String!, $merged: String!) {
      user(login: $login) { contributionsCollection(from: $from, to: $to) {
        %s
      } %s }
      opened: search(query: $opened, type: ISSUE, first: 1) { issueCount }
      merged: search(query: $merged, type: ISSUE, first: 1) { issueCount }
    }""" % (selections, weeks)
    start = timestamp(now.replace(month=1, day=1, hour=0, minute=0, second=0))
    search = f"is:pr is:public author:{username} created:{start}..{timestamp(now)}"
    body = json.dumps({"query": query, "variables": {
        "login": username,
        "from": timestamp(now.replace(month=1, day=1, hour=0, minute=0, second=0)),
        "to": timestamp(now),
        "opened": search,
        "merged": search + " is:merged",
    }}).encode("utf-8")
    request = Request("https://api.github.com/graphql", data=body, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "Lightning1507-profile-activity",
    })
    with urlopen(request, timeout=45) as result:
        return json.load(result)


def public_counts(entries):
    if not isinstance(entries, list) or len(entries) >= 100:
        raise ValueError("Repository limit reached or invalid contribution list.")
    total, repositories = 0, set()
    for entry in entries:
        private = entry["repository"]["isPrivate"]
        count = entry["contributions"]["totalCount"]
        if type(private) is not bool or type(count) is not int or count < 0:
            raise ValueError("Invalid repository visibility or contribution count.")
        if not private:
            repo_id = entry["repository"]["id"]
            if not isinstance(repo_id, str) or not repo_id:
                raise ValueError("Missing public repository identifier.")
            total += count
            if count:
                repositories.add(repo_id)
    return total, repositories


def build_snapshot(payload, username, now):
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("GitHub returned an API error; previous statistics retained.")
    try:
        collection = payload["data"]["user"]["contributionsCollection"]
        metrics, repositories = {}, set()
        for metric, field in FIELDS.items():
            total, contributed = public_counts(collection[field])
            repositories.update(contributed)
            metrics[metric] = total
        weeks = []
        for i, (start, end) in enumerate(completed_weeks(now)):
            count, _ = public_counts(payload["data"]["user"][f"week{i}"]["commitContributionsByRepository"])
            weeks.append({"start": timestamp(start), "end": timestamp(end), "commits": count})
        extras = {
            "public_repositories": len(repositories),
            "opened_pull_requests": payload["data"]["opened"]["issueCount"],
            "merged_pull_requests": payload["data"]["merged"]["issueCount"],
        }
    except (KeyError, TypeError) as error:
        raise ValueError("Missing GitHub contribution data; previous statistics retained.") from error
    snapshot = {
        "username": username,
        "scope": SCOPE,
        "period_start": timestamp(now.replace(month=1, day=1, hour=0, minute=0, second=0)),
        "period_end": timestamp(now),
        "updated_at": timestamp(now),
        "metrics": metrics,
        "weeks": weeks,
        "extras": extras,
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
        extras = snapshot["extras"]
        if set(extras) != {"public_repositories", "opened_pull_requests", "merged_pull_requests"} or any(
                type(n) is not int or n < 0 for n in extras.values()):
            raise ValueError("Invalid extra metrics.")
        if extras["merged_pull_requests"] > extras["opened_pull_requests"]:
            raise ValueError("Merged PRs must be a subset of PRs opened this year.")
        expected = completed_weeks(end.replace(tzinfo=timezone.utc))
        if not isinstance(snapshot["weeks"], list) or len(snapshot["weeks"]) != 12:
            raise ValueError("Expected 12 completed weeks.")
        for week, (week_start, week_end) in zip(snapshot["weeks"], expected):
            if (week["start"] != timestamp(week_start) or week["end"] != timestamp(week_end)
                    or type(week["commits"]) is not int or week["commits"] < 0):
                raise ValueError("Invalid weekly contribution period or count.")
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid saved contribution snapshot.") from error


def render_activity(snapshot):
    validate_snapshot(snapshot)
    year = snapshot["period_start"][:4]
    updated = datetime.strptime(snapshot["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
    # Explicit month names make rendering identical on local and CI locales.
    month = MONTHS[updated.month - 1]
    date = f"{updated.day} {month}, {updated:%H:%M} UTC"
    counts = snapshot["metrics"]
    return (
        f"**This year · {year}**\n\n"
        f"**{counts['commits']:,}** commits · **{counts['pull_requests']:,}** pull requests · "
        f"**{counts['reviewed_prs']:,}** reviewed PRs · **{counts['issues']:,}** issues\n\n"
        '<picture>\n'
        '  <source media="(prefers-color-scheme: dark) and (max-width: 600px)" srcset="assets/activity-dark-mobile.svg" />\n'
        '  <source media="(max-width: 600px)" srcset="assets/activity-light-mobile.svg" />\n'
        '  <source media="(prefers-color-scheme: dark)" srcset="assets/activity-dark.svg" />\n'
        '  <img src="assets/activity-light.svg" alt="Public commit contributions in the last 12 completed UTC weeks, plus annual merged PR and repository counts. Full numbers are available in the linked activity data." width="100%" />\n'
        '</picture>\n\n'
        f"<sub>{year} · Public repositories · Updated {date} · "
        "[About these counts](docs/activity.md) · [Data](data/activity.json)</sub>"
    )


def render_panel(snapshot, theme="light", mobile=False):
    validate_snapshot(snapshot)
    if theme not in ("light", "dark"):
        raise ValueError("Unknown panel theme.")
    dark = theme == "dark"
    bg, fg, muted, border, grid, bar, accent = (
        ("#0d1117", "#e6edf3", "#8b949e", "#30363d", "#21262d", "#2964a6", "#388bfd")
        if dark else ("#ffffff", "#1f2328", "#59636e", "#d1d9e0", "#eaeef2", "#80b7f4", "#0969da")
    )
    width, height = (400, 420) if mobile else (800, 380)
    left, right, top, bottom = (37, 379, 106, 253) if mobile else (45, 774, 90, 244)
    plot_height, step = bottom-top, (right-left)/12
    peak = max(week["commits"] for week in snapshot["weeks"])
    raw_step = max(peak, 4) / 4
    magnitude = 10 ** math.floor(math.log10(raw_step))
    tick_step = next(n*magnitude for n in (1, 2, 5, 10) if n*magnitude >= raw_step)
    ceiling = tick_step * 4
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
             '<title id="title">Public GitHub activity: 12 completed weeks</title>']
    details = "; ".join(f"{week['start'][:10]}: {week['commits']} commits" for week in snapshot["weeks"])
    extra = snapshot["extras"]
    merged, opened = extra["merged_pull_requests"], extra["opened_pull_requests"]
    parts += [f'<desc id="desc">{details}. PRs opened this year now merged: {merged} of {opened}. Public repositories contributed to this year: {extra["public_repositories"]}.</desc>',
              f'<rect x=".5" y=".5" width="{width-1}" height="{height-1}" rx="8" fill="{bg}" stroke="{border}"/>',
              '<g font-family="Segoe UI,Arial,sans-serif">']

    def text(x, y, value, size=13, color=fg, weight="400", anchor="start"):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{value}</text>')

    def short_date(iso):
        date = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        return f"{date.day} {MONTHS[date.month-1]}"

    text(22 if mobile else 25, 33, "Commit contributions", 19, weight="600")
    period = f"{short_date(snapshot['weeks'][0]['start'])} – {short_date(snapshot['weeks'][-1]['end'])}"
    text(22 if mobile else 25, 57, "12 completed weeks · " + period, 13, muted)
    for i in range(5):
        y = bottom - plot_height*i/4
        parts.append(f'<path d="M{left} {y}H{right}" stroke="{grid}"/>')
        text(left-8, y+4, str(tick_step*i), 12, muted, anchor="end")
    for i, week in enumerate(snapshot["weeks"]):
        bar_width = step * .54
        x = left + step*i + (step-bar_width)/2
        bar_height = plot_height * week["commits"] / ceiling
        parts.append(f'<rect x="{x:.2f}" y="{bottom-bar_height:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" rx="1.5" fill="{accent if i==11 else bar}"/>')
        if not week["commits"]:
            parts.append(f'<path d="M{x:.2f} {bottom}h{bar_width:.2f}" stroke="{muted}" stroke-width="1.5"/>')
        if not mobile:
            text(round(x+bar_width/2, 2), round(bottom-bar_height-8, 2), str(week["commits"]), 12, anchor="middle")
        if i in (0, 4, 8, 11):
            text(round(x+bar_width/2, 2), bottom+25, short_date(week["start"]), 12, muted, anchor="middle")
    divider = 296 if mobile else 287
    parts.append(f'<path d="M22 {divider}H{width-22}" stroke="{border}"/>')
    second = 220 if mobile else 435
    text(22, divider+26, "This year's PRs now merged", 12 if mobile else 13, muted)
    text(second, divider+26, "Repos contributed to", 12 if mobile else 13, muted)
    text(22, divider+54, f"{merged:,} / {opened:,}", 22, weight="600")
    text(second, divider+54, f"{extra['public_repositories']:,}", 22, weight="600")
    meter_width = 168 if mobile else 235
    parts.append(f'<rect x="22" y="{divider+66}" width="{meter_width}" height="4" rx="2" fill="{grid}"/>')
    parts.append(f'<rect x="22" y="{divider+66}" width="{meter_width*merged/opened if opened else 0:.2f}" height="4" rx="2" fill="{accent}"/>')
    text(22, height-16, f"Public repositories · Extra metrics: {snapshot['period_start'][:4]} year to date", 11, muted)
    parts.append('</g></svg>\n')
    return "\n".join(parts)


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
    summary = render_activity(snapshot)
    panels = {f"activity-{theme}{'-mobile' if mobile else ''}.svg": render_panel(snapshot, theme, mobile)
              for theme in ("light", "dark") for mobile in (False, True)}
    readme_path = output_dir / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    start, end = "<!-- activity:start -->", "<!-- activity:end -->"
    if readme.count(start) != 1 or readme.count(end) != 1 or readme.index(start) > readme.index(end):
        raise ValueError("README must contain exactly one ordered activity marker pair.")
    prefix, rest = readme.split(start, 1)
    _, suffix = rest.split(end, 1)
    updated_readme = prefix + start + "\n" + summary + "\n" + end + suffix
    # Whitelist the persisted fields so raw API payloads can never be published.
    public = {key: snapshot[key] for key in
              ("username", "scope", "period_start", "period_end", "updated_at", "metrics", "weeks", "extras")}
    for name, content in panels.items():
        atomic_write(output_dir / "assets" / name, content)
    atomic_write(readme_path, updated_readme)
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
