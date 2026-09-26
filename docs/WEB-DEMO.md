# Interactive web demo

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
is verified. Hosting provider/account selection is pending. No public URL,
account, paid service, repository publication or final submission was created.

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
