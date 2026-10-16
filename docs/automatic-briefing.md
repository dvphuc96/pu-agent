# Automatic partner briefing

The primary flow is **Trò chuyện → “Hôm nay có email gì mới?” → conversational company summaries, source links, attached full reports and weekly appointments in the same chat → approve an exact action**.

The inbox question routes directly to an application workflow rather than relying
on the model to decide whether to chain another tool. It synchronizes Gmail INBOX
for the Vietnam calendar day (midnight to midnight), classifies business requests
against older correspondence and internal knowledge, and researches every new
partner selected. Existing draft workspaces do not make a company an old partner.
Personal email with a company website can be considered; ambiguous senders without
company evidence are not guessed into organizations.

## Validate incoming partners before research

An email domain or company mention is only a candidate identity. A new potential
partner must pass **all** of these checks before website/search/report work:

1. A valid human sender and a non-transactional subject. Subjects such as
   “Welcome to MCP Market”, “You're one step away from selling skills on MCP
   Market”, newsletters/digests, promotions, webinars, invoices and OTPs are
   excluded. Reply/forward prefixes do not bypass this filter. A newsletter body
   with both opt-out and subscription/browser-view markers is also excluded;
   an unsubscribe link alone is not sufficient evidence of bulk mail.
2. A concrete, direct business request in the **body**: partnership, project,
   quotation, requested demo or a business meeting. A subject such as “Đề nghị
   hợp tác triển khai AI” is a positive signal only. Neutral subjects remain
   eligible for content assessment, so a real request titled “Xin chào” is not
   discarded just because it lacks business keywords.
3. A model decision with an allowed intent, an explicit direct-request flag,
   the actual email ID and a contiguous verbatim body quote. Local validation
   rejects missing/invented IDs, quotes copied only from subjects, fabricated
   company names, and company introductions without a request. The company name
   must appear in that same email; no company name is inferred from a domain.
4. Classification against supplied older correspondence and internal knowledge.
   No earlier relationship found means **potentially new in the checked data**,
   not proof there has never been a business relationship. Existing workspaces
   and old newsletters do not establish a prior business relationship.

Unclear evidence stays **chưa đủ dữ kiện** and triggers no research. Chat keeps per-email status, reason and accepted request quotes in a collapsed disclosure. The ordinary answer groups promotional mail into one count. All decisions, including
those beyond the 50-item display limit, persist in chat metadata with policy
version `partner-input-v5`. Unrelated same-domain messages do not become report
sources. Incomplete classification does not yield partial automatic research.

The background worker uses the same subject/body/evidence checks. It may also
prepare existing-partner meetings and does not label those requests new partners.
Semantic judgments still depend on the model; local checks establish traceability,
not a guarantee that every business intent will be interpreted correctly.

## Reports in chat

Each company is introduced in a natural Vietnamese summary with three sourced highlights and up to eight unique public source links (fewer when fewer were actually read). The full immutable report opens from its attachment inside chat and can be imported after review. No source-selection checkbox UI is used. This is followed by the
user's available 45-minute appointments from now through Sunday of the current
week. Coverage is explicit: local-only calendars do not imply Google or partner
availability. Progress appears while the workflow runs. Report IDs persist in chat
history, and an unchanged inbox request reuses a valid report. Source-backed
reports can be reviewed and approved inside chat without navigating away.

Gmail failure is labeled as a cached snapshot. Generation uses a 12,000-token
report budget and bounded evidence, with one 20,000-token structured repair when
necessary. Transport-escaped JSON is decoded without corrupting Vietnamese text. Claims must reference original evidence, not only an agent synthesis. Two malformed/empty outputs fail rather than publishing a successful empty report. The chat summary is derived from the evaluated report, without a second model relay. The complete body remains in the saved report attachment.

Sources, manual research and operational tools remain available as secondary views.
The background **Hôm nay** worker below is optional; it is not required for the chat
request. The chat workflow still runs when background automation is paused.

## Run

Connect a model and Gmail once in **Kết nối tài khoản**. Gmail read access and
send access are separate. In **Hôm nay**, optionally set an internal recipient
and domains to ignore (for example your own company). No recipient is inferred
from an incoming email.

```sh
pu dashboard
```

The dashboard starts a worker automatically. For operation without a browser:

```sh
pu worker
```

Keep one of these processes running. This is an application worker, not an OS
scheduled service: a stopped process or sleeping computer cannot poll Gmail.
Both commands may run together; a renewable SQLite lease selects one owner.
After restart, persisted jobs and workflow checkpoints are reused. A missing
model connection is shown as a setup step and does not silently kill the worker.

## Automatic processing

1. Every 15 minutes, list up to 500 inbox messages from the last seven days,
   follow Gmail pagination and download bodies only for unseen messages.
   A limit warning is visible if more mail exists.
2. Group previously unseen messages by the explicitly stated company website, falling back to the sender domain. Skip personal email
   providers, obvious automated senders and configured excluded domains.
   Domain matching identifies a candidate organization, not a verified legal entity.
3. Classify the email need with a bounded model call and validate its quoted
   request against the original body. Ambiguous input does not trigger research.
   General marketing and system notifications do not trigger public research. Invalid classification
   fails visibly; it is not silently treated as irrelevant mail.
4. Read the website, up to three real same-domain about/product/service links,
   and up to four search results with their full pages when accessible. Public
   reads cross the tool/security harness and retain per-page provenance.
   Snippet fallbacks are marked as such. Dates are preserved when the page exposes
   publication metadata; missing dates are not presented as recent news.
5. Retrieve internal context and check calendar intervals. All personal busy
   events block availability, while the partner report shows only matching
   meetings. Suggested appointments have an actual start/end and requested
   duration. They do not establish the partner's availability.
6. Run the existing scoped research workflow with collected evidence and an
   explicit target. Produce a Vietnamese brief describing the company, expressed
   need, business/products, dated updates, preparation questions and gaps.
   Retain immutable report versions and source mappings.
7. Apply structural checks plus mandatory AI comparison of content to source
   excerpts for automatic reports. Missing or weak factual-support review keeps
   a draft. This is a fallible AI check, not a guarantee of factual accuracy.
8. For an eligible report, prepare a pending knowledge-save proposal. Also
   prepare an email proposal if an internal recipient and Gmail send access are
   configured. Neither action executes until the user approves it.

After 07:00 Asia/Ho_Chi_Minh, the first daily scan also refreshes known partners
with related meetings in the next two calendar days, even without new email.
The daily fingerprint prevents duplicate refreshes.

Incoming email and website contents remain untrusted evidence. Agents have read
tools only. The worker can propose actions but receives no approval capability.
Approval binds the exact report hash, destination and payload; the workspace
shows recipient, subject, introduction and full saved report before execution.
Expired unsigned proposals can be refreshed when opening an automatic report.
Rejected, failed, executing or executed actions are not automatically reissued.

## Recovery and limits

- New email IDs, job fingerprints and active-job checks prevent duplicate work.
- A failed model/network workflow retries at most three times, with delays of
  two and four minutes. Completed stages are reused on resume.
- A report saved before interruption is reused rather than regenerated.
- Weak evidence is a completed draft requiring attention, not an infinite retry.
- Model context prioritizes up to 30 enabled recent sources within 110,000
  characters. It is not a complete archive of all historical correspondence.
- Gmail automation uses stored plain text. Attachment extraction remains an
  explicit import path; it does not automatically download every attachment.
- Website blocks, JavaScript-only pages, missing search access, unavailable
  calendars and ambiguous identities are limitations to expose in the report.
- This remains a local single-user application. The existing operational
  dashboard, authentication/deployment model and monitoring are separate concerns.

## Verification

`evals/deterministic/test_inbox_chat.py` covers chat routing, multiple partner reports, prior business context, exact Vietnam day boundaries, current-week calendar scope, report reuse, persisted references, unapproved actions and token-limit repair.

`evals/deterministic/test_automatic_briefing.py` covers the actual report workflow
with controlled model/network adapters, discovery deduplication, skipped mail,
lease ownership, mandatory semantic checks, pending-only actions, restart
recovery, bounded retries, official-page collection, per-result attribution,
partner calendar filtering and morning refreshes.

`test_gmail.py` covers incremental paginated sync. `test_notebook_runtime.cjs`
exercises reading, source navigation, the daily page, distinct report library,
inline approval previews, asynchronous enqueueing and version preservation.
These tests do not prove live Google OAuth, real model output quality or visual
layout in a browser.


## Clean local restart

The offline reset helper `pu.ops.reset.reset_local_data` requires all dashboard
and worker processes using that home to be stopped first. It deletes the local
SQLite state and generated memory, knowledge, cached inbox/calendar data, reports,
chat history, approvals, jobs, logs and usage files. Account tokens, OAuth client
configuration, model configuration are retained; learned SOUL/persona rules are cleared and return to the default on the next chat. It does not
contact Google or delete remote email/calendar events and does not train model
weights. A reset marker invalidates cached UI state on the next refresh. Normal
background syncing waits until the next polling interval so the fresh database
can be checked before re-import. The user may ask the inbox question immediately.

Previous correspondence is context, not a reason to discard a direct meeting or
business request. Existing partners with actionable requests also receive reports.
Mere prior outreach about an opportunity does not prove an established partnership.
Calendar check times are formatted in Vietnam time instead of raw UTC timestamps.

Input evidence can select an application-provided `request_passages` ID instead
of asking the model to retype a quote. The application extracts that original
paragraph. Legacy quotes tolerate line breaks, whitespace and canonically
equivalent Unicode composition; words, accents, IDs and company checks remain
strict. A failed chat validation retains the attempted evidence in local metadata
so the next diagnosis can distinguish format differences from invented content.


## Company identity boundary

Policy `company-boundary-v1` anchors the request to the sender's self-introduction
and candidate website, without treating either as a verified legal entity. A
conflicting signature is retained as a warning and deterministically blocks
distribution even if an AI judge gives a high score. Similar company names never
merge profiles: third-party articles need an explicit link or reference to the
target domain. Unrelated pages are excluded before any report stage sees them.

Workspace report stages receive an empty tool registry and no capabilities after
collection; they analyze only the selected evidence. Agent syntheses cannot add
new factual sources to this bounded report. The public reader lists relevant
primary sources; complete provenance stays in JSON and a collapsed audit area.
Old automatic reports without the boundary policy cannot be newly distributed.
The full report dialog uses a near full-height, up to 1320px-wide reading layout,
with a full-screen mobile layout. No computer-use verification is performed.


Inbox classification now creates one candidate per email, not one per sender
domain. Evidence IDs and failures are confined to that email. Only independently
validated messages with the same company name and candidate website are merged
for research. An explicitly labeled company website can differ from the sender
domain; that difference produces an unverified-affiliation warning, rather than
changing the research target to the sender's organization. The worker also routes
by candidate company site and splits legacy jobs that bundled different clients.
When a valid selected paragraph is merely context, the validator may select a
direct-request paragraph in the same email; fabricated IDs or quotes still fail.
