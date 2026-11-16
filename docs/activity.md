# GitHub activity

The text summary shows **current-calendar-year contributions to public repositories**,
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

The bar chart covers the **12 most recent completed UTC weeks**, Monday 00:00:00
through Sunday 23:59:59. The current partial week is excluded so it is not compared
with full weeks. Weekly commit counts follow GitHub's contribution rules and are
filtered to public repositories. A mark at the baseline represents a real zero.
The chart period is separate from the annual text totals and can cross a year boundary.

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

The chart has light/dark and desktop/mobile SVG variants selected using a native
`picture` element. The smaller layout reduces date labels and omits numbers above
individual bars. The annual totals remain actual Markdown text; SVG descriptions
and the linked [aggregate data](../data/activity.json) provide the chart values.

GitHub may take time to recognize new contributions. These counts describe activity;
they are not measures of skill or project quality. The navigation links below the summary
open public GitHub searches across all dates, so their totals can differ from this annual summary.

## Updates

The **Update public activity** workflow runs daily at 02:17 UTC and can also be run
from the repository’s Actions tab. It uses the built-in `GITHUB_TOKEN`;
no personal access token or external statistics image service is required.
The token has `contents: write` so the workflow can commit `README.md`,
`data/activity.json` and four `assets/activity-*.svg` variants. Only the text between the README's
`activity:start` and `activity:end` HTML comments is replaced; the rest of the profile stays intact.
The updater rejects missing, duplicated or reversed markers before changing either file.

The updater makes one GraphQL request for annual counts, weekly counts, and PR searches.
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
