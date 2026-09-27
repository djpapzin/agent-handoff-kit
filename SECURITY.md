# Security boundaries

This experimental prototype uses synthetic fixtures and local SQLite. The runtime
requires no model API keys. The public demo accepts no user task data, paths,
commands or URLs. Do not use it for sensitive workloads or expose private projects.

The HTTP wrapper serves three allowlisted assets and a health endpoint; an empty
POST to /api/run executes the fixed scenario. It limits concurrent scenarios and
timeouts, but is intended for low traffic behind an HTTPS proxy. It is not a
production multi-tenant execution service.

Hosting credentials belong outside this repository and outside the web root.
The supplied stable tunnel unit references a server-side systemd credential file;
it contains no token. Never put credentials in JavaScript, screenshots, fixtures,
Git history or release attachments. .gitignore alone cannot remove prior leaks.

The 27 September 2026 pre-release check found no matching credential patterns in
98 local Git-history blobs or current text files. Public .env, .git/config and
server credential paths returned 404. This is a bounded check, not a security
audit or assurance that every secret format and media frame has been examined.

Report suspected vulnerabilities using GitHub's private vulnerability reporting
if available; otherwise open an issue asking for a private contact without posting
exploit details, tokens or personal data. Rotate any exposed credential promptly.
