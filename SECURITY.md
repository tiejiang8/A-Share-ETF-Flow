# Security and local data

Do not include credentials, cookies, local environment files, raw response
archives, account identifiers or assistant history in an issue or contribution.
Use synthetic or redacted reproductions.

For a security issue, use GitHub private vulnerability reporting if it is enabled
for this repository. If it is unavailable, contact the maintainer privately before
disclosing exploit details. No dedicated security email is currently specified.

The core collector and offline summary command do not require brokerage access.
The optional WeChat publishing script is a separate integration and requires an
explicit operator action and separately configured credentials. Running tests or
the offline example does not invoke it.

Runtime directories and local helper state are excluded both by `.gitignore` and
by the release exporter's source allowlist. The export manifest identifies every
included file and its SHA-256 digest.
