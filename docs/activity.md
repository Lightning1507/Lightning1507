# GitHub activity

The panel's large totals show **current-calendar-year contributions to public repositories**,
from January 1 at 00:00 UTC through the displayed update time.
It uses GitHub’s GraphQL `contributionsCollection`, grouped by repository,
and excludes private repositories before adding up the counts.

| Metric | Meaning |
| --- | --- |
| Commit contributions | Commits recognized by GitHub’s contribution rules, rather than every commit on every branch. |
| Pull requests | Pull request contributions opened during the period. |
| Issues | Issue contributions opened during the period. |
| Reviewed PRs | Review contributions recognized by GitHub, which returns the most recent review for each reviewed PR rather than every review submission. |

## Activity panel

The stacked chart covers **six calendar months including the current month**, in UTC.
Each column has three directly named series: commit contributions, opened PRs, and
reviewed PRs. The current month ends at the update time and is marked with an asterisk
and **in progress**. Earlier months cover their full calendar period. Month boundaries
do not overlap and follow real month lengths, including leap years. A baseline mark
represents a real zero, not unavailable data. The chart period is separate from the
annual totals and can cross a year boundary.

Monthly counts use the same public-repository filtering and contribution rules as
the annual counts. Reviewed PRs are distinct within each period: reviewing the same
PR in two different months can contribute to both monthly columns but only once
in the annual reviewed-PR count. Monthly reviewed-PR counts therefore need not sum
to the annual number. Stacked height counts those recorded activities; it does not
measure effort, productivity or code quality.

The two extra metrics use the current calendar year:

- **This year's PRs now merged:** public PRs authored by Lightning1507, opened
  since January 1 through the update time, which have been merged by that update.
  The denominator is the same set of opened PRs, including those still open or closed
  without merging. It does not mix in PRs opened in earlier years. Counts come from
  public GitHub issue/PR searches and may lag behind recent changes while search indexes update.
- **Repos contributed to:** the union of public repositories with recognized commit,
  PR, issue or PR-review contributions during the year. A repo is counted once across
  those categories. It includes collaboration on other people's repositories and
  is not a count of repositories owned by the user.

The entire panel has light/dark and desktop/mobile SVG variants selected using a native
`picture` element. Mobile uses a two-by-two grid for the annual totals and omits
numbers above bars. The **Activity details and data** disclosure below the image
contains annual and monthly values as real Markdown tables. SVG descriptions and
the linked [aggregate data](../data/activity.json) also provide the values. The
visible panel is a single graphic, with no separate totals table above it.

GitHub may take time to recognize new contributions. These counts describe activity;
they are not measures of skill or project quality. The navigation links below the panel
open public GitHub searches across all dates, so their totals can differ from this annual summary.

## Updates

The **Update public activity** workflow runs daily at 02:17 UTC and can also be run
from the repository’s Actions tab. It uses the built-in `GITHUB_TOKEN`;
no personal access token or external statistics image service is required.
The token has `contents: write` so the workflow can commit `README.md`,
`data/activity.json` and four `assets/activity-*.svg` variants. Only the text between the README's
`activity:start` and `activity:end` HTML comments is replaced; the rest of the profile stays intact.
The updater rejects missing, duplicated or reversed markers before changing either file.

The updater makes one GraphQL request for annual counts, monthly counts, and PR searches.
It requests counts for up to 100 repositories per metric and time period. If that limit
is reached, it fails rather than silently presenting potentially incomplete totals.
It also rejects API errors, missing fields and invalid counts. If fetching or validation
fails, the previous summary is retained and the workflow reports a failure.
The timestamp below the counts shows how fresh the last successful update is.

Only aggregate counts and time periods are written to the repository.
Private repository names, contributions and source code are never included in the summary.

## Local use

Python 3.11 or newer is sufficient; there are no third-party dependencies.
Supply a GitHub API token through the environment without committing it:

```sh
python scripts/update_activity.py --username Lightning1507
python -m unittest discover -s tests -v
```

The updater reads `GH_TOKEN` or `GITHUB_TOKEN`. To regenerate the README summary from an
existing saved aggregate without an API request:

```sh
python scripts/update_activity.py --from-json data/activity.json
```

## References

- [GitHub GraphQL user and contributions fields](https://docs.github.com/en/graphql/reference/users)
- [Profile contributions reference](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference)
- [Automatic token authentication](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication)
- [Searching issues and pull requests](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)
- [Theme-aware README images](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/quickstart-for-writing-on-github)
