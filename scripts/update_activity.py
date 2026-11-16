"""Update the README's annual public-contribution summary using GitHub GraphQL."""

import argparse
import hashlib
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


def recent_months(now):
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    current = now.year * 12 + now.month - 1
    def start(index):
        year, month = divmod(index, 12)
        return datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return [(start(index), now if index == current else start(index+1)-timedelta(seconds=1))
            for index in range(current-5, current+1)]


def fetch_payload(username, token, now):
    if not token:
        raise ValueError("Set GH_TOKEN or GITHUB_TOKEN to read GitHub contributions.")
    selections = "\n".join(
        f"{field}(maxRepositories: 100) {{ repository {{ id isPrivate }} contributions(first: 1) {{ totalCount }} }}"
        for field in FIELDS.values()
    )
    chart_fields = " ".join(
        f"{field}(maxRepositories: 100) {{ repository {{ id isPrivate }} contributions(first: 1) {{ totalCount }} }}"
        for metric, field in FIELDS.items() if metric != "issues"
    )
    months = "\n".join(
        f'month{i}: contributionsCollection(from: "{timestamp(start)}", to: "{timestamp(end)}") '
        '{ ' + chart_fields + ' }'
        for i, (start, end) in enumerate(recent_months(now))
    )
    query = """query($login: String!, $from: DateTime!, $to: DateTime!, $opened: String!, $merged: String!) {
      user(login: $login) { contributionsCollection(from: $from, to: $to) {
        %s
      } %s }
      opened: search(query: $opened, type: ISSUE, first: 1) { issueCount }
      merged: search(query: $merged, type: ISSUE, first: 1) { issueCount }
    }""" % (selections, months)
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
        months = []
        for i, (start, end) in enumerate(recent_months(now)):
            monthly = {metric: public_counts(payload["data"]["user"][f"month{i}"][field])[0]
                       for metric, field in FIELDS.items() if metric != "issues"}
            months.append({"start": timestamp(start), "end": timestamp(end), "metrics": monthly, "partial": i == 5})
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
        "months": months,
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
        expected = recent_months(end.replace(tzinfo=timezone.utc))
        if not isinstance(snapshot["months"], list) or len(snapshot["months"]) != 6:
            raise ValueError("Expected 6 calendar months.")
        for i, (month, (month_start, month_end)) in enumerate(zip(snapshot["months"], expected)):
            if (month["start"] != timestamp(month_start) or month["end"] != timestamp(month_end)
                    or type(month["partial"]) is not bool or month["partial"] != (i == 5)
                    or set(month["metrics"]) != {"commits", "pull_requests", "reviewed_prs"}
                    or any(type(n) is not int or n < 0 for n in month["metrics"].values())):
                raise ValueError("Invalid monthly contribution period or counts.")
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
    def panel_url(theme, mobile=False):
        digest = hashlib.sha256(render_panel(snapshot, theme, mobile).encode("utf-8")).hexdigest()[:12]
        return f"assets/activity-{theme}{'-mobile' if mobile else ''}.svg?v={digest}"
    return (
        '<picture>\n'
        f'  <source media="(prefers-color-scheme: dark) and (max-width: 600px)" srcset="{panel_url("dark", True)}" />\n'
        f'  <source media="(max-width: 600px)" srcset="{panel_url("light", True)}" />\n'
        f'  <source media="(prefers-color-scheme: dark)" srcset="{panel_url("dark")}" />\n'
        f'  <img src="{panel_url("light")}" alt="GitHub activity: annual totals and six monthly columns stacked by commits, opened PRs and reviewed PRs. Current month is in progress. Expand Activity details below for all numbers." width="100%" />\n'
        '</picture>\n\n'
        '<details>\n<summary>Activity details and data</summary>\n\n'
        f"**This year · {year}**\n\n"
        "| Commits | PRs | Reviewed PRs | Issues |\n"
        "| :---: | :---: | :---: | :---: |\n"
        f"| **{counts['commits']:,}** | **{counts['pull_requests']:,}** | "
        f"**{counts['reviewed_prs']:,}** | **{counts['issues']:,}** |\n\n"
        "| Month (UTC) | Commits | PRs | Reviewed PRs |\n"
        "| :--- | ---: | ---: | ---: |\n"
        + "\n".join(f"| {m['start'][:7]}{' (in progress)' if m['partial'] else ''} | {m['metrics']['commits']} | {m['metrics']['pull_requests']} | {m['metrics']['reviewed_prs']} |" for m in snapshot["months"])
        + '\n\n</details>\n\n'
        + f"<sub>{year} · Public repositories · Updated {date} · "
        "[About these counts](docs/activity.md) · [Data](data/activity.json)</sub>"
    )


def render_panel(snapshot, theme="light", mobile=False):
    validate_snapshot(snapshot)
    if theme not in ("light", "dark"):
        raise ValueError("Unknown panel theme.")
    dark = theme == "dark"
    bg, fg, muted, border, grid = (
        ("#0d1117", "#e6edf3", "#8b949e", "#30363d", "#21262d") if dark else
        ("#ffffff", "#1f2328", "#59636e", "#d1d9e0", "#eaeef2")
    )
    colors = ("#388bfd", "#3fb950", "#a371f7") if dark else ("#0969da", "#1a7f37", "#8250df")
    series = ("commits", "pull_requests", "reviewed_prs")
    width, height = (400, 700) if mobile else (800, 550)
    left, right, top, bottom = (38, 378, 302, 480) if mobile else (49, 774, 232, 388)
    totals = [sum(month["metrics"].values()) for month in snapshot["months"]]
    raw_step = max(max(totals), 4) / 4
    magnitude = 10 ** math.floor(math.log10(raw_step))
    tick_step = next(n*magnitude for n in (1, 2, 5, 10) if n*magnitude >= raw_step)
    ceiling = tick_step * 4
    extra, metrics = snapshot["extras"], snapshot["metrics"]
    merged, opened = extra["merged_pull_requests"], extra["opened_pull_requests"]
    details = "; ".join(f"{m['start'][:7]}: {m['metrics']['commits']} commits, {m['metrics']['pull_requests']} opened PRs, {m['metrics']['reviewed_prs']} reviewed PRs" for m in snapshot["months"])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
             '<title id="title">GitHub activity: annual totals and 6 months of public contributions</title>',
             f'<desc id="desc">{details}. Current month in progress. Annual merged PRs: {merged} of {opened}. Public repos contributed to this year: {extra["public_repositories"]}.</desc>',
             f'<rect x=".5" y=".5" width="{width-1}" height="{height-1}" rx="8" fill="{bg}" stroke="{border}"/>',
             '<g font-family="Segoe UI,Arial,sans-serif">']

    def text(x, y, value, size=13, color=fg, weight="400", anchor="start"):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{value}</text>')

    def divider(y):
        parts.append(f'<path d="M22 {y}H{width-22}" stroke="{border}"/>')

    year = snapshot["period_start"][:4]
    text(22 if mobile else 26, 35, "GitHub activity", 20 if mobile else 21, weight="600")
    text(width-22, 35, f"{year} · Public only", 11 if mobile else 13, muted, anchor="end")
    labels = ("Commits", "Pull requests", "Reviewed PRs", "Issues opened")
    for i, metric in enumerate(("commits", "pull_requests", "reviewed_prs", "issues")):
        x = 22 + (i % 2)*190 if mobile else 26 + i*193
        y = 86 + (i//2)*61 if mobile else 93
        text(x, y, f"{metrics[metric]:,}", 30 if mobile else 32, weight="600")
        text(x, y+22, labels[i], 13, muted)
    divider(190 if mobile else 142)
    text(22 if mobile else 26, 219 if mobile else 174, "Contributions · last 6 months", 15 if mobile else 16, weight="600")
    first = datetime.strptime(snapshot["months"][0]["start"], "%Y-%m-%dT%H:%M:%SZ")
    last = datetime.strptime(snapshot["period_end"], "%Y-%m-%dT%H:%M:%SZ")
    period = f"{MONTHS[first.month-1]} {first.year} – {MONTHS[last.month-1]} {last.year}"
    if mobile:
        text(22, 242, period, 12, muted)
    else:
        text(width-26, 174, period, 12, muted, anchor="end")
    for x, label, color in zip((22, 130, 242) if mobile else (26, 144, 286),
                               ("Commits", "PRs opened", "Reviewed PRs"), colors):
        y = 265 if mobile else 202
        parts.append(f'<rect x="{x}" y="{y-9}" width="8" height="8" rx="1" fill="{color}"/>')
        text(x+14, y, label, 12, muted)
    for i in range(5):
        y = bottom - (bottom-top)*i/4
        parts.append(f'<path d="M{left} {y}H{right}" stroke="{grid}"/>')
        text(left-8, y+4, str(tick_step*i), 11, muted, anchor="end")
    step = (right-left)/6
    for i, month in enumerate(snapshot["months"]):
        bar_width = step * (.48 if mobile else .46)
        x, y = left+step*i+(step-bar_width)/2, bottom
        for metric, color in zip(series, colors):
            bar_height = (bottom-top)*month["metrics"][metric]/ceiling
            y -= bar_height
            parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" fill="{color}"/>')
        if totals[i] == 0:
            parts.append(f'<path d="M{x:.2f} {bottom}h{bar_width:.2f}" stroke="{muted}"/>')
        if not mobile:
            text(round(x+bar_width/2, 2), round(y-8, 2), str(totals[i]), 12, anchor="middle")
        month_number = int(month["start"][5:7])
        text(round(x+bar_width/2, 2), bottom+25, MONTHS[month_number-1]+("*" if month["partial"] else ""), 12, muted, anchor="middle")
    text(width-22, 530 if mobile else 436, f"*{MONTHS[last.month-1]} is in progress", 11, muted, anchor="end")
    line = 550 if mobile else 450
    divider(line)
    second = 219 if mobile else 435
    text(22, line+25, "This year's PRs now merged", 12 if mobile else 13, muted)
    text(second, line+25, "Repos contributed to", 12 if mobile else 13, muted)
    text(22, line+53, f"{merged:,} / {opened:,}", 22, weight="600")
    text(second, line+53, f"{extra['public_repositories']:,}", 22, weight="600")
    meter_width = 168 if mobile else 235
    parts.append(f'<rect x="22" y="{line+66}" width="{meter_width}" height="4" rx="2" fill="{grid}"/>')
    parts.append(f'<rect x="22" y="{line+66}" width="{meter_width*merged/opened if opened else 0:.2f}" height="4" rx="2" fill="{colors[0]}"/>')
    divider(641 if mobile else 528)
    date = f"{last.day} {MONTHS[last.month-1]} {last.year}"
    text(22, 662 if mobile else 543, f"Totals: Jan 1 – {date} · Public repositories", 10 if mobile else 10, muted)
    if mobile:
        text(22, 680, "Updated daily · Times in UTC", 10, muted)
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
              ("username", "scope", "period_start", "period_end", "updated_at", "metrics", "months", "extras")}
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
