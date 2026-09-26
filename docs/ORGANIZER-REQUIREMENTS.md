# Organizer requirements — checked 26 September 2026

Codex researched official organizer pages and the 24 September organizer email.
These are requirements and verification states, not a claim that a submission
or all account checks are complete. No organizer message was sent.

## Sources

- [IBM event-specific guide](https://lablab-ibm-bob-2-hackathon-guide.s3.us.cloud-object-storage.appdomain.cloud/index.html): linked by the organizer's 24 September email, read in the browser. Sections: The hackathon expectation; data sets; Bob account/Bobcoins; Upload Bob task session summary.
- [Submission guidelines](https://lablab.ai/delivering-your-hackathon-solution): read fully in the browser; accessible there despite web-fetch errors.
- [Rulebook](https://lablab.ai/hackathon-rules): read fully in the browser, including application components and manual submission conditions.
- [General FAQ](https://lablab.ai/guide): team membership also required for solo entrants.
- [Live event dashboard](https://lablab.ai/ai-hackathons/ibm-bob-2-hackathon/live): browser says live/submissions open; dynamic countdown on 26 September around 03:04 SAST was about 1 day 13h 55m, consistent with 27 September 17:00 SAST. Server-fetched zero countdown is not reliable.
- [24 September timing/access record](https://github.com/djpapzin/oddsedge/issues/1197#issuecomment-5819060484): previously verified official window 25 September 15:00 UTC to 27 September 15:00 UTC (17:00 SAST). Use this cutoff; do not assume an extension.

## Resolved requirements and project decisions

| Requirement | Evidence / action |
|---|---|
| Developer-workflow improvement | Event guide asks for a working prototype that improves a specific developer workflow, with measurable productivity/error/rework impact. Demonstrate interrupted task recovery on synthetic developer work; measure recovery time/manual steps and receipt count when implemented. Do not invent metrics. |
| Bob IDE is mandatory | Event guide requires Bob IDE as a core component; other frameworks allowed, Bob Shell and watsonx optional. Bob already authored architecture revisions; implementation/testing must retain meaningful Bob contribution evidence. |
| Genuine task evidence | Every participant must include relevant Bob task consumption-summary screenshots in final repo bob_sessions/. PNG preferred by guide; organizer email specifies PNG. Preserve task IDs, prompts, changes, and attribution. Actual architecture screenshots saved locally. |
| Budget | 40 Bobcoins per participant; guide says no top-ups. Resumed architecture task displays 3.17 cumulative Bobcoins, not a current account-total reading. |
| Public code | Rulebook requires public GitHub repository. Submission guide specifically asks for Bob-assisted files and relevant report/session evidence. No public repo exists yet; local commits are not uploaded. Follow event guide's consumption screenshots and preserve available task exports if used later. |
| Interactive prototype | Rulebook requires application URL and lists Streamlit/Replit/Vercel; submission guide includes Replit for online code execution. Local CLI alone is insufficient under published guidance. Keep CLI core and plan a bounded online synthetic execution harness; no dashboard needed. Confirm chosen hosting mechanism before packaging. No exception granting local-only CLI was found. |
| Video/slides/cover | Maximum 5-minute MP4 video; PDF slide presentation; PNG/JPG cover, use 16:9 to meet rulebook. Nothing produced yet. |
| Submission fields | Descriptive title; short description up to 255 characters; long description at least 100 words; technology/category tags; repo and application links. Actual authenticated submission form still needs final inspection. |
| Judging | Presentation, business value, application of technology, originality. No numeric weights verified. |
| Data | No client, company-confidential, personal, or social-media data. Public web data only with suitable commercial-use terms and source list. Project decision: generated synthetic fixtures only; no web dataset, personal data or OddsEdge source/data imported. Organizer research is documentation, not application data. |
| Existing code | Event-specific reuse permission was not found. Fresh implementation, disclose pre-existing idea and Codex setup baseline. Avoid reuse rather than assume permission. No unresolved reuse dependency for this fresh scope. |
| Deadline | Target 27 September 2026 at 17:00 SAST (15:00 UTC). Rulebook's manual route requires valid reason AND prior organizer/mentor approval, within six hours; not an automatic grace period. |

## Account checks still blocked

Prior approval email confirms the user's event application was approved. Bob
Enterprise team ibm-hackathon-lablab is verified; the event guide explicitly
says this does NOT constitute hackathon team membership.

Lablab was signed out in the available browsers. Safari is left on the existing
account sign-in page. That page explicitly says sign-in agrees to terms; the
user was asked to complete it. The Mac later locked again. No event team
membership or authenticated submission-form state can be verified yet.
Discord membership/event-channel visibility also remains unverified. Do not
claim enrollment failed or resubmit registration merely because of signed-out UI.

After user sign-in: inspect the existing event account/team and submission form;
verify solo/team entry and Discord event-channel access. Do not change an
existing team, contact teammates, publish, or submit on assumption.

## Organizer question draft (not sent)

Please confirm whether the IBM Bob 2.0 event accepts a Python/SQLite CLI
prototype exposed through an interactive online execution environment (e.g.
Replit) with synthetic fixtures, plus a public GitHub repository, Bob task
summary PNGs, MP4 video under five minutes and PDF slides. Is a separate
exported Bob report required in addition to the guide's screenshots? Is there
an event-specific reuse policy to disclose a pre-existing idea and setup
brief when all implementation is newly written during the event? We are
using 27 September 15:00 UTC as the final submission cutoff; please flag any
separate deadline.
