# GDP Agent Platform guidebook

`index.html` is the operating guide and runbook for the platform: access model, roles and Snowflake grants, setup order, every feature, configuration (environment variables, Jira, GitHub, Airflow, Teams), the deployment and secrets runbooks, acceptance checks, the table reference and known gaps.

## Open it

Open `docs/guidebook/index.html` in a browser. It is a single self-contained page (fonts load from Google Fonts when online).

## Keep it current

- This file is the source of truth. A pull request that changes a feature, a privilege, a grant, a table or a setting also updates the matching chapter here.
- Never put real secret values, account identifiers or personal data in the guide. Use placeholders such as `<client id>`.
- Write in plain prose without em or en dashes.
- After merging, republish the shared copy of the page so readers without repository access see the same content.
