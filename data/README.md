# Company data directory

Upload your policy document and transactions through **Settings → Company data**.
Runtime no longer reads hardcoded files from this folder.

Optional legacy bootstrap: if `company/active.json` is missing and this folder
contains `policy.*` + `transactions.json`, the app imports them once on first boot.

