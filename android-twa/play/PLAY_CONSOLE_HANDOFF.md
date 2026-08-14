# Don’tRipIt · Play Console deployment handoff

Prepared: 2026-08-14
Branch: `mobile-pwa`
Production branch intentionally untouched: `catalog-v2`

This is the single operational handoff for the first Google Play deployment. Do not reconstruct release values from memory; use this file and the audited documents linked below.

## Release identity

- App name: `Don’tRipIt`
- Android package: `com.dontripit.app`
- Version name: `0.1.0`
- Version code: `1`
- TWA host: `dontripit.com`
- Start URL: `/?source=android`
- Minimum SDK: `23`
- Compile SDK gate: `36`
- Target SDK gate: `36`
- Notifications: disabled in initial release
- Native camera/location/contacts/files permissions: none intentionally requested in initial release

## Binary prepared outside Git

Prepared Play-upload AAB filename:

`dontripit-0.1.0-1-play-upload-signed.aab`

Expected SHA-256:

`f462851b7356db63c368a30f1f59ae7fc65def55b782f7706b36cc17d93d28f8`

The AAB is signed with the Don’tRipIt **upload key**. The private keystore is intentionally not stored in Git.

Upload-key public identity:

- Alias: `dontripit-upload`
- Certificate SHA-256: `99:A1:DD:B3:25:FB:1E:7C:A7:03:C4:FD:34:AF:C7:60:0B:E5:66:D2:B3:FF:80:B3:D8:3A:A9:06:47:5D:0A:3F`
- Public certificate record: `docs/android-upload-certificate.md`

## Required Play Console values already prepared

Machine-readable values: `android-twa/play/console-values.json`

Store copy: `docs/google-play-store-listing.md`

Data Safety audit: `docs/google-play-data-safety-draft.md`

App Content / reviewer access: `docs/google-play-app-content-checklist.md`

Release notes: `android-twa/play/release-notes-es-ES.txt`

Fastlane-compatible localized metadata is mirrored under:

`android-twa/fastlane/metadata/android/es-ES/`

## Public URLs

- Website: `https://dontripit.com`
- Privacy policy: `https://dontripit.com/privacy`
- Account deletion: `https://dontripit.com/delete-account`
- Support email: `info@dontripit.com`

Important: `/delete-account` is implemented and tested on `mobile-pwa` but intentionally remains outside production until the mobile release is approved for integration. Do not submit that URL to Play as live until it has deliberately been released on `dontripit.com`.

## Account deletion

In-app route already exists through Dashboard / Cuenta. It requires:

1. authenticated session;
2. current password;
3. exact confirmation `ELIMINAR`.

Backend hard-deletes the account. Sessions, reset tokens, collection and wishlist are associated to the user with cascade deletion.

External deletion resource is `/delete-account` and provides the required web path for users who cannot access the app/account flow.

## Play App Signing — action immediately after first upload

The upload certificate above is **not** the Play App Signing certificate.

After Play App Signing is enabled and the first AAB is accepted:

1. open **App integrity / Play App Signing** in Play Console;
2. copy the **SHA-256 certificate fingerprint of the App signing key certificate**;
3. do not confuse it with the Upload key certificate;
4. run `android-twa/scripts/render-assetlinks.mjs` with the real Play App Signing fingerprint;
5. publish the resulting file as `https://dontripit.com/.well-known/assetlinks.json` only during the deliberate mobile production integration;
6. verify Digital Asset Links before treating the TWA as fully trusted/fullscreen.

Until the Play signing fingerprint exists, `assetlinks.json` must not contain a guessed fingerprint.

## Reviewer access

The public catalogue is usable without login. Dashboard, collection, wishlist and in-app deletion require login.

Before review, create one dedicated reviewer account in the final release environment. Do **not** commit its email/password. Put reusable credentials only in Play Console’s protected App access fields.

Prepared reviewer instructions live in `docs/google-play-app-content-checklist.md`.

## Owner decision still required

### Target audience

Do not auto-select this in Play Console.

Decision required from the owner at submission time:

- `18+ only`, or
- include older teens and complete the additional privacy/Families analysis that choice may trigger.

The current technical release does not need this decision to build, but Play Console requires a truthful audience declaration before review.

## Store assets

Technical requirements/checklist: `android-twa/play/store-assets-checklist.md`.

Already available:

- 512px Don’tRipIt app icon source.

Final-release capture required:

- feature graphic 1024×500;
- at least two real phone screenshots from the accepted mobile UI;
- any additional screenshots Play requests for selected device/form-factor support.

Do not use fake catalogue data, fake ratings, rankings, prices or misleading publisher logos in store graphics.

## First deployment sequence when the Play account is paid

1. Create/verify the developer account.
2. Create Don’tRipIt in Play Console with package `com.dontripit.app`.
3. Enable/confirm Play App Signing.
4. Paste the prepared store listing and policy values.
5. Complete Data Safety from the audited draft, re-checking any code/infrastructure changes since 2026-08-14.
6. Complete App Content and target-audience decision.
7. Add the reviewer account credentials.
8. Upload the signed AAB whose SHA-256 matches this handoff.
9. Copy the Play App Signing SHA-256 fingerprint and finalize Digital Asset Links.
10. Add final store graphics/screenshots.
11. Create an Internal Testing release first.
12. Install/test the Play-delivered build on a real Android device.
13. If the personal developer account is subject to Google’s new-account production testing requirement, complete the required Closed Testing period before requesting production access.
14. Only after these checks, merge/deploy the required PWA/TWA web files to `dontripit.com`.

## Automation

Run locally or in CI:

`node android-twa/scripts/play-preflight.mjs`

A dedicated GitHub workflow also runs this readiness contract on `mobile-pwa`.

The existing signed-release workflow expects protected GitHub Actions secrets for the upload keystore. Those secrets are intentionally not present in source control.

## Hard stop conditions

Do not submit/publish if any of these are true:

- package differs from `com.dontripit.app`;
- version code `1` has already been consumed by a different AAB;
- AAB SHA-256 does not match the approved build without a documented rebuild/version bump;
- target SDK gate is below 36;
- privacy or deletion URLs are not publicly reachable;
- Play App Signing fingerprint has not been used in final Digital Asset Links;
- reviewer login is invalid;
- Data Safety no longer matches the code/data flows;
- store screenshots do not represent the actual shipped UI.
