# Interactive web demo

Current public demo: https://handoff.djpapzin.com/ . The named tunnel starts
automatically on the Oracle VM; no laptop needs to remain awake. Older hosting
attempts below are a historical log, not current deployment instructions.

Codex authored this web wrapper around the verified Bob-assisted core.
No new Bob development session or consumption is claimed for this packaging.

## Local run

```sh
python3 -m handoff_kit.web --port 8080
```

Open http://127.0.0.1:8080. Click Run the recovery demo; wait about 2–3 seconds.
The page reports actual completion evidence after the whole scenario finishes;
it does not simulate live progress. A previous result is explicitly labeled if
a later run fails. The browser can download the result as JSON.

## Hosting contract

Run on Linux with Python 3.9+ and permission to spawn/kill child processes.
Use a normal web/container service with an HTTPS reverse proxy, local temporary
writable storage and a request timeout greater than 20 seconds. A static-only
host cannot run this demo. The application binds to PORT supplied by the host:

```sh
python3 -m handoff_kit.web --host 0.0.0.0
```

No pip dependencies, secrets, data import or persistent disk required.
Health endpoint: GET /healthz. Keep one instance initially; two concurrent demo
runs are allowed per instance. Extra requests receive HTTP 429 and can retry.
The runner terminates its process group after 20 seconds, including children.
Only a fixed empty POST /api/run executes the scenario. User commands, paths,
URLs and input data are not accepted. Standard HTTP headers are bounded by the
Python server, and per-connection read timeout is 25 seconds. Use the host's
normal ingress controls; this is a low-traffic hackathon prototype.

Optional Docker packaging is supplied (Python 3.12 base, non-root user):

```sh
docker build -t agent-handoff-kit .
docker run --rm -p 8080:8080 agent-handoff-kit
```

The Docker image has not been built/tested in this task; local Python execution
is verified. The user selected Render on 26 September 2026. The isolated source
was published at https://github.com/djpapzin/agent-handoff-kit, default branch
`hackathon/agent-handoff-kit`, with the MIT license.

Render sign-in succeeded using the existing account. The new-service form is
prepared with Docker, Frankfurt, the default branch, no secrets or disk, and
health check `/healthz`. Creation on Free was rejected: "You have reached your
free instance usage limit for this month. Please select a paid compute plan to
continue." The next offered plan is $7/month (0.5 CPU, 512 MB). Approval for that
recurring charge is pending. No service or public application URL was created,
and no paid plan or final submission was committed.

## Verification — 26 September 2026

`python3 -m unittest discover -s tests -v`: **109 tests passed in 9.268s**.
The HTTP tests run real repeated scenarios and check isolation, status/receipt/
generation/history, input rejection, cross-origin rejection, capacity rejection,
error redaction/capacity release and timeout process-group termination.
Full output: verification-web-tests.txt. Local socket tests required the host's
sandbox approval; the initial sandbox-only attempt could not bind test servers.

Browser verification on the local page produced run `51655eb7cf8a`: DONE,
generation 2, one receipt, nine durable events, unchanged replay, 2.27 seconds
including lease wait and process startup. Verified empty/loading/success states,
button re-enabling, rendered receipt/history and the evidence download action.
Browser error/warning log was empty. Layout inspected at 1440px and 390px;
390px viewport had document scroll width 390px (no horizontal page overflow).
Viewport restored after testing. UI screenshots are verification artifacts,
not Bob session evidence.

## Railway follow-up — 26 September 2026

The user switched hosting to Railway. Existing GitHub sign-in succeeded. The
workspace is on Free; selecting New was blocked by "Free plan resource provision
limit exceeded. Please upgrade to provision more resources!" No new project or
service was created. The account plans page offers Hobby at $5 minimum monthly
usage, with $5 monthly credits and charges for extra resource usage. Upgrade and
a maximum monthly budget were requested from the user; neither is yet approved.
The existing Dockerfile uses PORT and binds 0.0.0.0; /healthz is ready for Railway.
No runtime code changed and no new runtime verification is claimed.

The user subsequently specified zero-budget hosting. No paid Railway or Render
plan is authorized; the pending upgrade request is superseded. Free alternatives
are being evaluated.

## PythonAnywhere WSGI hosting — prepared, not deployed

The user selected PythonAnywhere Free on 26 September. The dependency-free
`handoff_kit.wsgi.application` entry point serves the same assets and calls the
same real-process scenario synchronously. All child work ends within the request;
no background tasks, Docker, additional packages or paid features are needed by
the adapter. Host-specific subprocess permission and CPU limits remain unverified.

Deployment steps once the website is accessible:

1. In a PythonAnywhere Bash console, clone the public project repository:
   `git clone https://github.com/djpapzin/agent-handoff-kit.git`
2. Add a free Web app using Manual configuration and Python 3.9 or newer.
3. Set its WSGI configuration to the following, replacing YOUR_USERNAME:

   ```python
   import sys
   sys.path.insert(0, '/home/YOUR_USERNAME/agent-handoff-kit')
   from handoff_kit.wsgi import application
   ```

4. Reload the web app, check `/healthz`, then run the browser demo and verify
   DONE, generation 2, one receipt, nine history events and unchanged replay.
5. Do not mark hosted verification complete until that public run succeeds.

Local validation: 4 adapter tests include WSGI protocol validation, asset routes,
input rejection, error redaction and a real process-kill recovery through WSGI.
The full suite passed: 113 tests in 11.639 seconds.
See verification-wsgi-tests.txt for the full suite result. PythonAnywhere's login
page and homepage returned server error pages during setup. The homepage later
recovered, but following its Log in link still returned HTTP 500. No account, hosted app, paid service or public demo URL was created.

PythonAnywhere notes that subprocesses outliving a web request may be killed:
https://help.pythonanywhere.com/pages/AsyncInWebApps/ . This adapter waits for the
scenario to finish before returning and retains its 20-second process-group limit.

## EU access workaround

A direct unauthenticated HTTP check confirmed the US login returned 502. The
separate EU site, login and Beginner registration pages loaded successfully.
The Beginner plan is explicitly EUR0/month. The free signup form is ready at
https://eu.pythonanywhere.com/registration/register/beginner/ . User account
creation (password and terms) is required before deployment can continue.
US and EU accounts are separate. The adapter remains ready and hosted execution
remains unverified.

## US account access — 27 September

The user signed into the existing US account djpapzin. Its only free web app,
djpapzin.pythonanywhere.com, is expired and configured for TruthGuard. Explicit
approval to back up its configuration and reuse the address for Agent Handoff Kit
is pending. Existing project files must be preserved. No hosted configuration
change has been made.

The public repository was cloned into /home/djpapzin/agent-handoff-kit without
changing the existing site. In the idle existing Bash console, Python 3.10 ran
all 4 WSGI adapter tests successfully in 3.111 seconds, including actual worker
kill and recovery. This verifies the console environment only; serving through
the hosted web worker remains pending approval and verification.

## Hosting direction change

The user approved reusing the expired PythonAnywhere site. Its original WSGI
configuration was backed up remotely to
/home/djpapzin/truthguard-wsgi-backup-20260927.py with mode 0600 and byte equality
verified before replacement. The active WSGI file now imports handoff_kit.wsgi
from the isolated project folder. The site was NOT reactivated or reloaded.
The user then asked about hosting on their existing Oracle VM; deployment is
paused at this boundary pending the VM SSH alias or connection details.
Do not publish or copy the original WSGI backup into this repository.

## Oracle deployment — verified 27 September 2026

Public demo: https://neighbor-breakdown-refer-pollution.trycloudflare.com/

Runtime source (application commit dd06c71) is installed separately under
/opt/agent-handoff-kit on the existing Oracle VM. Python 3.10.12 runs the HTTP
wrapper at 127.0.0.1:18880. The agent-handoff-kit and agent-handoff-tunnel systemd
services are enabled and active. Unit definitions are in deploy/. Dynamic users,
private temporary storage, read-only system files, inaccessible home directories,
resource caps and no-new-privileges isolate the demo. No existing service,
firewall rule, DNS record or shared tunnel was changed. No paid resource was added.
Disk was 90% used with about 21GB available before deployment; the lightweight
runtime avoids Docker image builds and dependencies. No production data is used.

Public browser run 342495a090cf passed in 2.77 seconds: worker A killed, generation
2 DONE, one receipt, nine durable events and unchanged replay. Local health probe
returned status ok. The first health probe raced service startup; the subsequent
probe and public run both succeeded. Local suite remains 113 passing tests; no
new full-suite run is claimed for the VM deployment.

The Quick Tunnel is temporary, without an uptime guarantee. Its address changes
when the tunnel process restarts; retrieve a replacement with:
`sudo journalctl -u agent-handoff-tunnel --no-pager | grep trycloudflare.com`.
Do not use this URL as a permanent submission link without accounting for that
limitation. A stable named tunnel/domain remains a future improvement.

Rollback only this deployment:
`sudo systemctl disable --now agent-handoff-tunnel agent-handoff-kit`.
Keep application files for inspection. PythonAnywhere remains expired and was
not reloaded; its original WSGI backup remains remote with mode 0600.

Effective deployment instructions: /home/ubuntu/AGENTS.md host routing and
/opt/agent-handoff-kit/AGENTS.md project scope (standalone installed runtime,
no VM git checkout). Installed project policy SHA256:
c1a7ff716e9e7ae82b94b0798cb207671e0fb23c785cf140445472b557bc08a2.


## Stable hostname — 27 September 2026

Public demo: https://handoff.djpapzin.com/

Dedicated Cloudflare tunnel `agent-handoff-kit` routes this hostname to `http://127.0.0.1:18880`. The separate `agent-handoff-stable-tunnel.service` is enabled on the existing Oracle VM. It loads a root-protected credential through systemd LoadCredential; no credential is stored in the repository. The temporary Mac transfer file was deleted after installation. Existing unrelated tunnels and routes were preserved. The earlier Quick Tunnel remains available for old links.

Runtime policy source: `/opt/agent-handoff-kit/AGENTS.md`; effective hash `04830f2dd6922d3a434b7a3905ad715f1999fc9d79f33e783268f27016792cdc`. Deployment root `/opt/agent-handoff-kit` is an installed runtime, not a Git checkout.
