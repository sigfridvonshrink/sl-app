# Unexpected-sender warnings — functional spec

Status: draft. Target state (post-redesign), not the current implementation.
An appendix maps the current code to this target so the spec doubles as a build plan.

Fork-only feature. Not part of upstream simple-login/app. Designed to be additive,
opt-in, and byte-identical to upstream when disabled, so it can be offered upstream
as a self-contained PR.

---

## 1. Problem

An alias can receive mail from anyone who learns it (breaches, resale, scraping).
The recipient has no quick signal distinguishing a sender they expect on that alias
from a stranger. This feature adds a low-effort visual warning, on forwarded mail and
in the dashboard, that fades as a sender becomes familiar.

It answers a question SimpleLogin's existing anti-phishing (DMARC/SPF/DKIM) cannot:
not "is this mail authentic?" but "is this sender *expected* on this alias?". The two
are orthogonal and both can fire.

## 2. Goals / non-goals

Goals:
- Per-alias soft allow-list of trusted sender domains.
- Escalating, self-decaying warning marker on forwarded mail from non-trusted senders.
- Dashboard surfaces, per alias, which senders are trusted and lets the user manage them.
- Entirely opt-in; off by default; zero behavioural or visual change when disabled.

Non-goals:
- Not a block. Mail is always delivered; the marker is advisory only. (Blocking is the
  separate, existing per-contact block toggle.)
- Not authentication. Does not verify SPF/DKIM/DMARC; does not replace SimpleLogin's
  anti-phishing handling. (See §13 for why integration was considered and dropped.)
- No new per-sender storage. Only trusted domains are persisted (§4).

## 3. Terminology

One user-facing term: **trusted domain** / **allow-list**. ("Whitelist" is retired from
UI copy; the model field stays `sender_allow_list` for code continuity.) A domain is
**trusted** when it is on an alias's allow-list; otherwise, if a contact for it exists,
it is **flagged**.

The unit is always a **domain** (registered/eTLD+1), never an individual contact.
Trusting a domain affects every sender at that domain.

## 4. Data model

Persisted, single source of truth:
- `Alias.sender_allow_list` — JSON list of registered domains, nullable. `NULL`/empty =
  feature inert for that alias (allow-all, no markers). Domains stored normalized via
  `get_registered_domain` (eTLD+1).

User-level flags (bits on `User.flags`, no migration needed):
- `FLAG_SENDER_WARNINGS` (master switch) — **NEW**. Off by default. Gates everything,
  UI and email.
- `FLAG_AUTO_WHITELIST_ON_FIRST_CONTACT` — auto-trust the first sender to a new alias.
- `FLAG_MARKER_IN_SUBJECT` — place the marker in Subject (else in the From display name).

User-level decay config (one additive nullable JSON column — the only schema change):
- `User.sender_warning_decay` — `JSON`, nullable, default `NULL`. `NULL` = built-in default
  ladder (§6). Holds the marker tiers and the optional auto-trust terminus:
  ```json
  { "tiers": [ {"marker": "⚠️⚠️", "max_days": 1, "max_count": 2},
               {"marker": "⚠️",   "max_days": 8, "max_count": 5} ],
    "floor_marker": "〰️",
    "auto_trust": null }   // or {"min_days": 365, "min_count": 12}
  ```
  `auto_trust: null` = off (the default). `floor_marker` is shown below the last tier.
  Marker glyphs, thresholds, and the floor are all editable from the Advanced settings grid
  (§9.1); casual users never touch it. Marker strings are user input that lands in outgoing
  mail headers — see the validation rules in §9.1 (CRLF/control-char rejection, length cap,
  non-empty). Fixed tier count (2 tiers + floor); rows are not addable/removable.

Derived, never stored:
- A domain's **flagged** state = "a contact exists for it and it is not on the list."
- A domain's **orphan** state = "on the list but no current contact." (Possible via manual
  trust, backfill, or trusting then deleting the contact.)
- An *untrusted* domain with no contact is structurally impossible — it has no source and
  vanishes when its last contact is deleted. No cleanup needed.

## 5. Activation model

```
FLAG_SENDER_WARNINGS  (user master, default OFF)
  └─ when ON ─ per-alias sender_allow_list (0..N trusted domains)
       ├─ FLAG_AUTO_WHITELIST_ON_FIRST_CONTACT  (sub-option)
       └─ FLAG_MARKER_IN_SUBJECT                (sub-option)
```

- Master OFF → upstream behaviour exactly. Lists may exist in the DB but are dormant and
  invisible. (Option A: lists are preserved across disable/enable, not wiped.)
- Master ON, alias has 0 trusted → armed but inert: allow-all, no markers, no contact tags;
  the domain panel is shown so the user can start trusting.
- Master ON, alias has ≥1 trusted → trusted senders pass clean; all others get a marker.

The inversion to remember: an empty list does **not** mean "everyone flagged" — it means
"warnings off for this alias." The first trusted domain flips the alias from allow-all to
flag-all-others. The UI must state this (§9).

## 6. Decay tiers (single source of truth)

A non-trusted sender's marker decays with the contact's age and message count. One function
owns this; the email path, page render, and toggle response all call it. No duplication in
JS or templates. Thresholds come from `User.sender_warning_decay` or the built-in default
ladder below.

Default ladder:

| Condition (whichever first)        | Marker |
|------------------------------------|--------|
| age < 1 day  OR ≤ 2 messages       | ⚠️⚠️   |
| age < 8 days OR ≤ 5 messages       | ⚠️     |
| otherwise                          | 〰️ (permanent floor — never sunsets to none) |
| trusted (incl. auto-trusted, §8.1) | ✅ (dashboard only; no marker on mail) |
| blocked, or no list, or master off | (none) |

Polarity: each marker tier holds while age < its `max_days` **OR** count ≤ its `max_count`
(conservative — stays loud if recent *or* sparse). The auto-trust terminus (§8.1) is the
opposite: it fires only when age ≥ `min_days` **AND** count ≥ `min_count` (reluctant —
earned on both axes). 〰️ is the floor whenever no tier and no auto-trust applies.

Configurable glyphs: the marker strings themselves (both tiers + floor) are editable in the
Advanced grid, defaulting to ⚠️⚠️ / ⚠️ / 〰️. They must be non-empty (an empty marker would
re-introduce the rejected sunset — "no marker ⇒ trusted" must hold) and pass header-safety
validation (§9.1). The static legend reflects the user's current glyphs.

Legend vs dials: the marker *meanings* are a small static legend shown to everyone
(§9.2 "How to use"). The *thresholds and glyphs* are the editable grid, hidden under
Advanced (§9.1). Don't bury the legend; bury the dials.

## 7. Source-of-truth principle

Server computes, client paints. Matches upstream's existing pattern (toggle endpoints
return an authoritative scalar; JS only reflects it). No business logic in templates or JS:
- Decay tiers computed once in Python.
- No `arrow`/time math in templates; no module passed to Jinja.
- Toggle endpoints return ready-to-render data (tags, classified domain lists, counts).
- JS only renders that data, animates, toasts. Page load and post-toggle use the same
  server-computed values — no drift.

## 8. Email path (when master ON)

On the forward phase, for the recipient mailbox:
1. Compute `whitelist_mismatch = not alias.is_sender_allowed(sender)`. (False when the
   alias list is empty → inert.)
2. If mismatch, compute the decay tag (§6) from the contact.
3. Inject the tag:
   - `FLAG_MARKER_IN_SUBJECT` on → into the Subject, after the 3rd (or earliest available)
     word boundary.
   - else → into the From **display name**, after the first word boundary. (SimpleLogin
     already rewrites From and re-signs on the SL→mailbox leg, so this does not affect
     sender-domain DMARC alignment.)
4. On the reply phase, a Subject marker previously inserted is stripped so it does not
   echo back out. The strip matches against the user's *current* marker glyphs; if a user
   edits a glyph, in-flight subjects stamped with the old glyph won't be stripped (stale
   marker echoes once) — acceptable, affects only mail already sent with the prior glyph.

Master OFF → none of the above runs; forward path is the upstream path unchanged.

### 8.1 Auto-trust terminus (lazy promotion, no cron)

The decay ladder's final step. When `sender_warning_decay.auto_trust` is set (`{min_days,
min_count}`) and the alias is *armed* (list non-empty), the forward phase, before computing
a marker for a non-trusted sender, checks the contact. If `age_days ≥ min_days` **AND**
`count ≥ min_count`, it adds the sender's registered domain to the alias's allow-list,
commits, and treats the sender as trusted (no marker). Time *promotes a sender into the
trusted set* — it never erases the signal while leaving the sender untrusted, so the rule
"no marker ⇒ trusted" stays honest.

Properties:
- The bottom row of the Advanced decay grid (§9.1), not a separate lever. Default **off**
  (`auto_trust: null`) — feature-complete but no surprise auto-trusting until a power user
  sets it.
- AND polarity (both axes), unlike the OR marker tiers — trust is earned, reluctant.
- Lazy: happens on the next forwarded mail from that sender, not via cron. Promotion occurs
  exactly when a marker would otherwise have been applied.
- Only on armed aliases (empty list = inert; nothing to promote).
- Recorded, visible, reversible: the domain turns green in the panel; the user can untrust
  it. No silent/implicit trust state.

Marker matrix:

| Alias trusted | Sender         | Marker        |
|---------------|----------------|---------------|
| 0             | any            | none          |
| ≥1            | trusted        | none          |
| ≥1            | non-trusted new| ⚠️⚠️          |
| ≥1            | non-trusted mid| ⚠️            |
| ≥1            | non-trusted old| 〰️           |

## 9. UI

### 9.1 Settings card ("Unexpected sender warnings")

Principle: **one switch by default; everything else has working defaults behind Advanced.**
Casual users flip the master toggle and leave the rest.

**Primary (always visible):**
- **Master toggle** (top): enable/disable the whole feature. Default off. When off, the
  card shows only the toggle + a one-line explainer; nothing else renders.
- On enable, sensible defaults apply with no further action: auto-trust-first **ON**, marker
  placement **From**, decay = default ladder, auto-trust terminus **off**.
- A small static **legend** (⚠️⚠️ new · ⚠️ familiar · 〰️ established) and a link to
  docs/examples (cross-client screenshots).

**Advanced (collapsed `Advanced settings ▸`):** all defaulted; touch only to tune.
- **Marker placement** (radio): From field / Subject line. One help line on the trade-off.
- **Auto-trust first sender** (checkbox): ideal for an alias handed to one contact; skip for
  publicly posted aliases.
- **Decay grid** — editable glyph + thresholds, one row per marker plus the auto-trust
  terminus (marker cells are text inputs, pre-filled with the defaults):
  ```
  Marker      Show while younger than    …or fewer messages than
  [ ⚠️⚠️ ]    [ 1 ]  days               [ 2 ]
  [ ⚠️ ]      [ 8 ]  days               [ 5 ]
  [ 〰️ ]      (everything older)         —
  ── promote to trusted ───────────────────────────────────────
  ✅ auto     after at least [    ] days  AND  [    ] messages   (blank = off)
                                                  [ Reset to defaults ]
  ```
  - Marker rows: "younger than … OR fewer than …" (OR — tier lingers while recent or sparse).
  - Auto-trust row: "at least … AND …" (AND — earned on both axes; §8.1). Blank/unset = off.
  - **Reset to defaults** restores the default glyphs, ladder, and clears auto-trust.
  - **Threshold guards:** positive ints, bounded (days ≤ 3650, counts ≤ 100000); tier-1 <
    tier-2 on both columns; auto-trust ≥ last tier. Reject invalid input (a broken ladder =
    broken markers).
  - **Marker guards (header-safety — markers are inserted into outgoing From/Subject):**
    reject CR/LF and control chars (CRLF header-injection vector); length cap (≤ 8 chars);
    must be UTF-8 `Header`-encodable; non-empty (empty = rejected sunset). Validate on save
    **and** defensively at insertion time.
- **Seed allow-lists from existing contacts** (button, confirm dialog): for each alias with
  no list yet, adds the most-frequent contact domain; editable per alias afterwards.

### 9.2 Contact-manager page
Two roles, cleanly separated:

**Contact rows = status (read-only).** Each contact shows its tag (✅ / ⚠️⚠️ / ⚠️ / 〰️ /
none) — the mirror: exactly what the recipient mailbox would see for that sender. The tag
is a control only insofar as clicking it surfaces the domain panel (§9.3). Rendered
server-side on load; repainted from the toggle response. No per-contact trust switch
(removed — it confused contact with domain).

Tags appear only when master ON **and** the alias has ≥1 trusted domain. At 0 trusted,
rows are clean (nothing is actually flagged).

**Domain panel = control (write).** Replaces today's read-only domain pulldown, in the
same place. Shown whenever master ON. Contents = `sender_allow_list ∪ {each contact's
registered_domain}`, rendered as wrapping chips in two alphabetically-sorted groups
separated by a labelled divider:

```
Allowed sender domains                         3 trusted · 5 flagged
── Trusted ─────────────────────────────────────────────────────
 ✓ acme.com   ✓ bank.com·2   ✓ work.org·4      ✓ old.com ✕ (stale)
── Flagged (others get ⚠️) ──────────────────────────────────────
 ○ ads.io   ○ deals.co   ○ news.net   ○ promo.biz   ○ x-mail.io
```

- **Glyph + colour** (colour is reinforcement, never the sole signal; aria-labels on each):
  - `✓` green = trusted, active (has contacts). `·N` shows contact count when N>1.
  - `✓` green dimmed + `✕` "(stale)" = trusted orphan (no contact); `✕` prunes it.
  - `○` gray = flagged (known sender, not trusted; gets a marker).
- **Click a chip** toggles its trust. The panel repaints from the server response: the chip
  animates across the divider (~200ms) with a ~1s highlight so the eye tracks it; the
  counts header and all affected contact tags update.
- **Empty-group placeholders:** 0 trusted → "None yet — click a flagged domain to trust it.";
  0 flagged → "All known senders trusted."
- **Header** states the mode: "0 trusted → warnings off for this alias" vs "N trusted ·
  others get ⚠️" — this is where the §5 inversion is made explicit.
- **Click-to-surface:** clicking a contact-row tag expands the panel, scrolls to it, and
  highlights that contact's domain chip (wherever it sits), so the user can trust it in one
  more click.

Sort: flat alphabetical within each group; orphans pinned to the end of Trusted. No bulk
filter unless a list exceeds ~15 domains (deferred).

### 9.4 Visual reuse & styling (match the existing codebase)

No new CSS framework, component library, or custom widget. The stack is Bootstrap 4 +
Feather icons (`fe fe-*`) + bootbox (confirms) + toastr (toasts), already loaded on this
page. Reuse only those.

- **Chips = Bootstrap badges.** SimpleLogin uses `badge badge-*` throughout (the de-facto
  tag). Render each domain chip as `span.badge.badge-pill`:
  - trusted → `badge-pill badge-success` (green) with a leading `fe fe-check`.
  - flagged → `badge-pill badge-light` (neutral/gray); no icon, or `fe fe-circle`.
  - orphan → `badge-pill badge-success` at reduced opacity + a trailing prune control.
  - count `·N` → a muted inline number, or a nested `badge-light` if a single style reads
    better; keep it subtle.
  Chips wrap naturally (inline-block badges); add small `mr-1 mb-1` spacing like existing
  badge groups. Make each chip a real control: `role="button"`, `tabindex="0"`,
  keyboard-activatable, `aria-pressed`, `data-toggle="tooltip"` title "trust/untrust
  <domain>", and an `aria-label` carrying the state (colour is reinforcement only).
- **Prune (orphan ✕)** = `fe fe-x` (already used in the codebase) or the `&times;` glyph
  (used ~24×). Match whichever the nearest neighbouring template uses.
- **Panel container** = the existing `alert alert-secondary collapse` block already used for
  the read-only domain list and the "How to use" box — keep that shell; swap its body for
  the grouped chips. Group dividers via the existing `hr` / small muted headings already
  seen on dashboard cards.
- **Settings card** reuses the standard `card > card-body > card-title` + `form-check` /
  `custom-switch` pattern already in `setting.html` (e.g. the existing block reuses
  `custom-switch`). The `Experimental`/status pill uses `badge badge-warning` like the
  rest of settings.
- **Toasts / confirms** reuse `toastr.success/error` and `bootbox.confirm` exactly as the
  current block and the delete-contact flow do.
- **Animation** = minimal and native: on repaint, apply a short-lived highlight class
  (CSS background transition) to the moved chip; reuse the existing `highlight-row` class
  pattern (already used on the contact card) rather than introducing a JS animation lib.
  No FLIP/3rd-party animation.
- **Contact-row status tag** stays the emoji (✅/⚠️⚠️/⚠️/〰️) — it is the mirror of the
  marker that lands in the mailbox, so it must match the email glyphs, not a badge.

Net: the panel should look like it shipped with SimpleLogin — Bootstrap badges, Feather
icons, the same alert/card shells, the same toast/confirm helpers. Zero net-new visual
vocabulary.

## 10. API

All gated by master flag server-side; with master off these behave as upstream / are unused.

- `POST /api/contacts/<id>/toggle?ui_tag=1` — existing per-contact block toggle. Returns
  `{block_forward}`; with `?ui_tag=1`, also `{ui_tag}`. Default response shape (no query
  flag) stays upstream-identical.
- `POST /api/aliases/<alias_id>/toggle_allow_domain` — **NEW** (domain-based; replaces the
  current per-contact `toggle_allow_list`). Body `{domain}`. Toggles the domain on the
  alias's list. Returns the full panel state for repaint:
  ```json
  {
    "trusted":  [{"domain": "acme.com", "contacts": 2, "orphan": false}, ...],
    "flagged":  [{"domain": "ads.io",  "contacts": 1}, ...],
    "contact_tags": {"<contact_id>": "⚠️⚠️", ...},
    "counts": {"trusted": 3, "flagged": 5}
  }
  ```
  Ownership-checked; 403 on foreign alias. Idempotent per final state.

## 11. Edge cases

- **Empty/`<>`/invalid sender domain:** contact contributes no chip; grouped under
  "(no domain)" or omitted. Tag = none.
- **Two contacts share a domain:** one chip; trusting it covers both; both rows repaint.
- **Delete last contact of a flagged domain:** chip disappears (no source). Correct.
- **Delete last contact of a trusted domain:** becomes a trusted orphan, stays visible and
  prunable. No silent auto-removal (the current auto-remove-on-delete is dropped — it can't
  fully clean and it fights deliberate pre-trust).
- **Untrust the last trusted domain:** list empties → alias goes inert → contact tags
  vanish → header flips to "warnings off." Panel repaints to that state.
- **Block a contact:** block takes precedence; tag = none for that contact regardless.

## 12. The invariant — master OFF ⇒ byte-identical to upstream

Enforced by exactly two gates, not scattered conditionals:
1. **Template:** the entire fork block (contact tag span, panel, JS) wrapped in
   `{% if current_user.sender_warnings_enabled %}…{% endif %}`. Off → rendered HTML is
   upstream's, character-for-character. Keeps upstream template tests green.
2. **email_handler forward path:** `if not user.sender_warnings_enabled: <upstream path>`;
   the mismatch computation is skipped entirely. (Belt-and-suspenders with
   `is_sender_allowed()` returning True on empty lists.)

One flag, two gates — the whole guarantee, and the headline for an upstream reviewer.

## 13. Considered and dropped: DMARC/SPF/DKIM integration

SimpleLogin already flags/quarantines auth failures globally in the forward phase
(`apply_dmarc_policy_for_forward_phase`): reject/quarantine → quarantined or
`[Possible phishing attempt]`; soft_fail → prefix + warning body. Adding an auth-driven
tier here would duplicate that. Gating auto-trust on a passing first contact was also
dropped — many legitimate senders have no DMARC record (`not_available`), and SL itself
delivers `neutral`/`na`, so a pass-gate would block valid senders. Suppressing this marker
when SL flags was dropped too: the two signals are orthogonal, and a predictable "always
present" marker is the whole low-cognitive-load value. Net: no auth integration; the feature
stands on the expectedness axis alone. A future, optional enhancement could warn on a
*trusted* domain arriving with failing inbound auth, but it is explicitly out of scope here.

Also considered and **rejected: sunsetting the 〰️ marker to nothing** after a long period.
It breaks the core invariant ("no marker ⇒ trusted"): an old-but-untrusted sender would
become visually identical to a trusted one, the dashboard mirror would diverge from the
mail, and an untracked "aged-out" trust state would exist outside the data model. The
legitimate underlying want — long-known senders going clean — is served instead by the
**auto-trust terminus** of the decay ladder (§8.1), which *promotes the sender into the
trusted set* (recorded, visible, reversible) rather than erasing the signal. Time moves a
sender into trust, never into an unmarked limbo. It lives as one row in the Advanced decay
grid (default off), so it adds no primary-surface complexity.

## 14. Upstream-fit summary

- Off by default; two-gate invariant gives byte-identical default output.
- One additive, nullable schema change (`User.sender_warning_decay` JSON); the
  `sender_allow_list` column already exists. Nullable + default-NULL → safe migration, inert
  when unset.
- **Ship whole, not pre-cut.** The full feature — tunable decay, editable markers, auto-trust
  terminus — is the feature. It is not trimmed to court upstream adoption. Rationale: the
  audience that enables sender warnings (privacy-conscious, per-contact-alias users) is the
  same audience that wants to tune; a cut-down version would underserve the real user. The
  Advanced surface is all default-and-forget, so casual users are unaffected either way.
- If offered upstream at all, it is offered as the whole feature (accept or not) — the goal
  is not adoption. The upstream-friendly properties above are kept because they are good
  engineering, not as a negotiating trim.
- Endpoints follow upstream's "authoritative data in response, dumb client" pattern.
- Custom logic isolated in fork-owned files (`whitelist_utils.py`); upstream-file edits are
  import + call.
- Lint/format/test gates per CONTRIBUTING must pass (ruff, flake8, djlint, pytest).

---

## Appendix A — current implementation vs target (build plan)

| Area | Current | Target change |
|------|---------|---------------|
| Master flag | none (activation implicit via lists) | add `FLAG_SENDER_WARNINGS` + accessor; gate template + email path |
| Decay tunability + auto-trust | hardcoded tiers; no auto-trust | add nullable `User.sender_warning_decay` JSON (+ migration); Advanced decay grid (editable glyphs + thresholds) incl. auto-trust terminus row; lazy promotion in forward path (§8.1) |
| Settings layout | flat list under one card | primary (master only) + collapsed Advanced (placement, auto-trust-first, decay grid, seed); good defaults on enable |
| Upgrade continuity | feature activated implicitly (list presence; bit 16 only auto-populated) | migration backfills the new master flag (value 64) for users with the old auto-whitelist flag (flags & 16) or who own a non-empty allow-list, so live behavior is preserved on deploy |
| Contact UI | per-contact "Whitelisted:" switch + tag | remove switch; keep tag read-only + click-to-surface |
| Domain list | read-only pulldown | manageable grouped chip panel (§9.3) |
| Toggle API | `toggle_allow_list` keyed per-contact; returns domains + empties | `toggle_allow_domain` keyed per-domain; returns trusted/flagged/contact_tags/counts |
| Decay logic | duplicated in `get_whitelist_tag` (py), template `data-contact-diff-hours`, and JS | single Python source; JS paints; remove `import arrow` from view + template math |
| Auto-remove on delete | silent, in `perform_contact_deletion_with_whitelist_check` | drop; orphans pruned via panel ✕ |
| Settings | "Inhabitual..." card; placement nested under auto-whitelist | rename; master toggle on top; placement always visible |
| Terminology | "whitelist" / "soft allow list" mixed | unify to "trusted domain / allow-list" |
| Cleanup script | `cleanup_whitelists.py` (fork-only backfill) | keep in fork; exclude from any upstream PR |
