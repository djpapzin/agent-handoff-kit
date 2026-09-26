# Organizer requirements — checked 26 September 2026

Codex researched official organizer pages and the 24 September organizer email.
These are requirements and verification states, not a claim that a submission
is complete. Account/access checks were completed in the follow-ups below. No organizer message was sent.

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
| Video/slides/cover | Event-specific maximum 3-minute MP4 video, with at least 90 seconds of the solution in action and narration; PDF slide presentation; PNG/JPG cover, use 16:9 to meet rulebook. Nothing produced yet. |
| Submission fields | Descriptive title; short description 50–255 characters; title 5–50 characters; Problem & Solution Statement and separate IBM Bob Usage Statement each <=500 words and 500–4000 form characters; technology/category tags; repo and application links. Authenticated step 1 of 3 inspected; later media/link steps not yet inspected because the project is not built and no draft was filled. |
| Judging | Presentation, business value, application of technology, originality. No numeric weights verified. |
| Data | No client, company-confidential, personal, or social-media data. Public web data only with suitable commercial-use terms and source list. Project decision: generated synthetic fixtures only; no web dataset, personal data or OddsEdge source/data imported. Organizer research is documentation, not application data. |
| Existing code | Event-specific reuse permission was not found. Fresh implementation, disclose pre-existing idea and Codex setup baseline. Avoid reuse rather than assume permission. No unresolved reuse dependency for this fresh scope. |
| Deadline | Target 27 September 2026 at 17:00 SAST (15:00 UTC). Rulebook's manual route requires valid reason AND prior organizer/mentor approval, within six hours; not an automatic grace period. |

## Initial account blockers (historical; resolved below)

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
summary PNGs, MP4 video under three minutes with at least 90 seconds of working demo and PDF slides. Is a separate
exported Bob report required in addition to the guide's screenshots? Is there
an event-specific reuse policy to disclose a pre-existing idea and setup
brief when all implementation is newly written during the event? We are
using 27 September 15:00 UTC as the final submission cutoff; please flag any
separate deadline.


## Authenticated follow-up — 26 September, approximately 07:20 SAST

GitHub sign-in succeeded in Safari using the existing lablab account. My Teams
showed no ongoing team. Created the approved solo fallback as **Agent Handoff
Kit**, UTC+2, membership closed, no invitations sent. Dashboard verifies the
user as sole member and reports no project submission yet.

- Team: https://lablab.ai/ai-hackathons/ibm-bob-2-hackathon/agent-handoff-kit
- Submission form: https://lablab.ai/ai-hackathons/ibm-bob-2-hackathon/agent-handoff-kit/submission

The authenticated event page fully loaded and materially supersedes the general
five-minute video guidance: **maximum three minutes, at least 90 seconds of
on-screen solution demonstration, MP4 with narration**, explicitly showing Bob
usage. Both Problem & Solution and IBM Bob Usage statements must be <=500 words.
The actual form additionally enforces title 5–50 characters, short description
50–255 characters and each long/usage statement 500–4000 characters. Meet both
word and character limits. Categories and technologies are required. Step 1
was inspected only; no draft content or final submission was sent.

Source: https://lablab.ai/ai-hackathons/ibm-bob-2-hackathon — What to submit,
Important Requirements and Deliverable Details. This source also says submissions
must be original and MIT-compliant. Record licensing suitability before publication;
no licensing change or blanket reuse permission is inferred. The event explicitly
says to build a new project, consistent with our fresh implementation decision.
Deadline is directly displayed as **27 September, 17:00 SAST** (11:00 AM ET).

Team-specific Discord channel creation requires at least two members with
connected Discord, so it is unavailable to this solo team and does not justify
adding someone merely to create a channel. General event Discord access is a
separate verification item.

This follow-up supersedes the earlier signed-out/team blocker and the generic
five-minute limit above. Public repo and interactive prototype remain required;
creation of the team is not project submission or publication of the code.

Discord invite resolved to the existing-account login page in Safari, offering
password, mobile QR or passkey. No authenticated Discord session is available;
user must complete this authentication. No new Discord account or server
membership was created, and no message was sent.

## Discord verification — 26 September

After the user completed sign-in, Safari showed the authenticated DjPapzin /
djpapzin account in the LABLAB.AI server. The official IBM Bob 2.0 hackathon
updates channel loaded readable organizer announcements, including the
25 September kickoff and Q&A notices. Event matchmaking and participant-chat
channel links were also visible.

- [Official event updates](https://discord.com/channels/877056448956346408/1549403437807050955)
- [Event matchmaking](https://discord.com/channels/877056448956346408/1549403440189411429)
- [Event participant chat](https://discord.com/channels/877056448956346408/1549403442206875779)

This resolves the remaining Discord membership/channel-access blocker above.
No Discord message, invitation, reaction, or subscription was sent. A dedicated
team channel remains unavailable to a solo team under the two-member condition;
general event-channel access is verified. Account/access checks are complete.
The project still has no working prototype, public repository, or submission.
