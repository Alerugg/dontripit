# Don’tRipIt · Google Play Data Safety draft

Audit baseline: 2026-08-14
Package: `com.dontripit.app`

> This is a code-audited submission draft, not a claim that the Play Console form has already been submitted. Re-run this audit immediately before closed/open/production release if data flows, SDKs, infrastructure or account features change.

## Executive answers

- Does the app collect or share required user-data types? **Yes — collects.**
- Does the app share user data with third parties for their own purposes? **No based on the current code/product model.** Infrastructure/email providers are used as service providers acting on Don’tRipIt’s behalf; verify current provider contracts before final submission.
- Is user data encrypted in transit? **Yes for the intended production app/API over HTTPS.** Reconfirm all production endpoints before submission.
- Can users request data deletion? **Yes.** In-app account deletion exists and `/delete-account` is the external web resource prepared for Play.
- Does the app allow account creation? **Yes.**
- Ads / advertising SDKs? **No in the audited build.**
- Analytics SDKs (Firebase Analytics, GA, PostHog, Amplitude, etc.)? **None found in the audited code.**
- Location permissions/inference? **No location feature.** The server receives network IP and uses a pseudonymous HMAC identity for short-lived abuse/rate limiting; it does not infer location from IP in the audited code.

## Data types to declare

### Personal info · Name

**Collected:** Yes  
**Shared:** No  
**Ephemeral:** No  
**Required or optional:** Optional at app level: public catalogue use does not require an account; users who choose to create an account must provide a name.  
**Purpose:** Account management; App functionality.  
**Code basis:** registration stores `User.name`.

### Personal info · Email address

**Collected:** Yes  
**Shared:** No under the service-provider exception; verify Resend/infrastructure treatment before final submission.  
**Ephemeral:** No  
**Required or optional:** Optional at app level; required only if the user chooses to create an account.  
**Purpose:** Account management; App functionality; password recovery.  
**Advertising/marketing:** No in the current release. Newsletter/marketing is not active.  
**Code basis:** registration stores `User.email`; password recovery sends to the registered address.

### Personal info · User IDs

**Collected:** Yes  
**Shared:** No  
**Ephemeral:** No  
**Required or optional:** Optional at app level because an account is optional for public catalogue browsing.  
**Purpose:** Account management; App functionality.  
**Code basis:** Don’tRipIt generates an internal account ID and associates sessions/library rows with it.

### App activity · Other actions

**Collected:** Yes  
**Shared:** No  
**Ephemeral:** No for saved collection/wishlist selections.  
**Required or optional:** Optional. Users choose whether to save cards to collection/wishlist.  
**Purpose:** App functionality.  
**What this covers:** card/print selections saved to collection, quantities, wishlist selections and wishlist priority/target values if those fields are exposed/used.  
**Code basis:** `UserCollectionItem` and `UserWishlistItem` persist the user’s selected exact physical prints.

### App activity · In-app search history

**Form response:** Include because search text is transmitted off-device to service a search request.  
**Collected:** Yes, **processed ephemerally** in the audited product flow.  
**Shared:** No  
**Required or optional:** Optional; users choose whether to search.  
**Purpose:** App functionality.  
**Persistence:** No user-linked search-history table or analytics/search-history persistence was found. API request metrics store endpoint/status/latency, not the search term.  
**Important:** If infrastructure/request logs start retaining query strings or search terms, this must be reclassified as non-ephemeral.

### Device or other IDs · security/rate-limit identity

**Conservative declaration:** Yes  
**Shared:** No under the service-provider model  
**Ephemeral:** No — a pseudonymous HMAC identity is persisted in a short-lived rate-limit bucket.  
**Required or optional:** Required automatically for requests protected by the rate limiter.  
**Purpose:** Fraud prevention, security and compliance.  
**Code basis:** client IP is used to construct a rate-limit identity, transformed with HMAC-SHA256, and the bucket expires after the one-minute window plus a short cleanup period. The audited code does **not** infer geographic location from the IP.

## Data types currently NOT selected

The audited mobile product does not currently justify selecting the following:

- Approximate location — no location inference or location feature in audited code.
- Precise location.
- Address.
- Phone number.
- Race/ethnicity.
- Political/religious beliefs.
- Sexual orientation.
- Health/fitness data.
- Contacts.
- Messages.
- Photos/videos.
- Audio files/voice recordings.
- Files/documents.
- Calendar.
- Installed apps.
- Web browsing history.
- User payment information.
- Credit score.
- Other financial information.
- Purchase history **in the current user-facing build**.
- Advertising data.

## Fields present in backend schema but not currently exposed by the app UI

The collection API/schema supports optional `condition`, `notes`, `purchase_price`, `purchase_currency` and `acquired_at`, and wishlist supports `target_price` / `target_currency`. The audited current `LibraryPage` UI only sends `print_id` and quantity for collection changes and does not provide purchase/notes forms.

**Release rule:** if purchase price/date, free-text notes, condition entry or other stored fields become user-facing before the Play release, re-audit this form. Purchase/transaction fields could change the appropriate Google Play data type; free-text notes may require `Other user-generated content`.

## App interactions / diagnostics

Don’tRipIt stores server-side API metrics containing endpoint path, HTTP status and latency. These rows do not contain a user ID, IP, search term or session token in the audited implementation. No mobile analytics or crash-reporting SDK was found.

**Draft position:** do not select user-linked `App interactions`, `Crash logs` or `Diagnostics` solely for these anonymous operational metrics. Before submitting the Play form, verify what the production hosting provider logs automatically and whether any retained log data is reasonably linkable to an identifiable user/device.

## Sharing assessment

Google Play does not require transfers to service providers processing data on the developer’s behalf to be disclosed as “sharing.” The current privacy policy identifies infrastructure and email-delivery providers. Before final submission confirm that:

1. they are acting as service providers under the applicable agreements;
2. no provider uses Don’tRipIt account/library data for its own advertising/profile purposes;
3. no new analytics/advertising SDK has been added.

If any answer changes, update the Data Safety form.

## Deletion answers

- Account creation: **Yes**
- In-app deletion path: **Yes** — Dashboard → Account → “Eliminar mi cuenta”
- External deletion resource to enter in Play Console: **`https://dontripit.com/delete-account`**
- External route status today: implemented on `mobile-pwa`; **do not enter it in Play Console as a live production URL until that route is actually deployed to `dontripit.com`.**
- Backend behavior: hard-delete `users` row after authenticated password + `ELIMINAR` confirmation; sessions, password-reset tokens, collection and wishlist use `ON DELETE CASCADE`.

## Encryption / security answers

Proposed Data Safety answers, subject to final live verification:

- Data encrypted in transit: **Yes**
- User can request deletion: **Yes**
- Independent security review badge: **No / not claimed**

Do not claim a third-party security certification until one actually exists.

## Re-audit triggers

Re-open this document before Play submission if any of these are introduced:

- analytics or crash SDK;
- advertising/affiliate tracking;
- push notifications;
- camera/photo/card scanning;
- location;
- social login;
- payments/subscriptions;
- newsletter/marketing consent;
- user profile/public social features;
- purchase history / acquisition-cost UI;
- free-text collection notes UI;
- retained search history;
- telemetry tied to user/session/device identifiers.
