# GitHub activity

The profile card shows **current-calendar-year contributions to public repositories**,
from January 1 at 00:00 UTC through the displayed update time.
It uses GitHub’s GraphQL `contributionsCollection`, grouped by repository,
and excludes private repositories before adding up the counts.

| Metric | Meaning |
| --- | --- |
| Commit contributions | Commits recognized by GitHub’s contribution rules, rather than every commit on every branch. |
| Pull requests | Pull request contributions opened during the period. |
| Issues | Issue contributions opened during the period. |
| Reviewed PRs | Review contributions recognized by GitHub, which returns the most recent review for each reviewed PR rather than every review submission. |

GitHub may take time to recognize new contributions. These counts describe activity;
they are not measures of skill or project quality. The navigation links below the card
open public GitHub searches across all dates, so their totals can differ from this annual card.

## Updates

The **Update public activity** workflow runs daily at 02:17 UTC and can also be run
from the repository’s Actions tab. It uses the built-in `GITHUB_TOKEN`;
no personal access token or external statistics image service is required.
The token has `contents: write` so the workflow can commit the two generated files:
`assets/activity.svg` and `data/activity.json`.

The updater requests counts for up to 100 repositories per metric. If that limit
is reached, it fails rather than silently presenting potentially incomplete totals.
It also rejects API errors, missing fields and invalid counts. If fetching or validation
fails, the previous card is retained and the workflow reports a failure.
The timestamp on the image shows how fresh the last successful update is.

Only aggregate counts and the time period are written to the repository.
Private repository names, contributions and source code are never included in the card.

## Local use

Python 3.11 or newer is sufficient; there are no third-party dependencies.
Supply a GitHub API token through the environment without committing it:

```sh
python scripts/update_activity.py --username Lightning1507
python -m unittest discover -s tests -v
```

The updater reads `GH_TOKEN` or `GITHUB_TOKEN`. To regenerate the SVG from an
existing saved aggregate without an API request:

```sh
python scripts/update_activity.py --from-json data/activity.json
```

## References

- [GitHub GraphQL user and contributions fields](https://docs.github.com/en/graphql/reference/users)
- [Profile contributions reference](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference)
- [Automatic token authentication](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication)
