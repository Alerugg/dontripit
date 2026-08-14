# Don’tRipIt · Android / Google Play release checklist

Status baseline: 2026-08-14

This checklist is intentionally separate from production. `mobile-pwa` remains the Android/PWA release branch until the mobile release is accepted.

## Fixed Android identity

- App name: `Don’tRipIt`
- Package ID: `com.dontripit.app`
- Initial version: `0.1.0`
- Initial version code: `1`
- TWA origin: `https://dontripit.com`
- Minimum SDK: 23
- Compile SDK: 36
- Target SDK: 36
- Notifications: disabled for the initial wrapper

The package name becomes effectively permanent once an artifact is uploaded to Play Console. Do not change `com.dontripit.app` casually after that point.

## Current Google Play compatibility gate

Google Play requires new mobile apps and updates submitted from 2026-08-31 to target Android 16 / API 36 or later. Don’tRipIt is generated and CI-gated at API 36 now, rather than waiting for that deadline.

Official reference:
https://support.google.com/googleplay/android-developer/answer/11926878

## Signing model

Use Play App Signing with a separate upload key.

1. Don’tRipIt signs the `.aab` with the upload key.
2. Google Play verifies the upload certificate.
3. Google Play signs distributed APKs with the app signing key.
4. Keep the upload keystore backed up and outside Git.
5. Never commit a keystore, base64 keystore, password or private key.

Protected GitHub secrets expected by `.github/workflows/android-release.yml`:

- `ANDROID_UPLOAD_KEYSTORE_B64`
- `ANDROID_UPLOAD_KEYSTORE_PASSWORD`
- `ANDROID_UPLOAD_KEY_PASSWORD`
- `ANDROID_UPLOAD_KEY_ALIAS`

Official reference:
https://developer.android.com/studio/publish/app-signing

## Digital Asset Links / full TWA verification

The final production file is:

`https://dontripit.com/.well-known/assetlinks.json`

Do not publish the template placeholders.

For Google Play distribution, obtain the SHA-256 fingerprint of the **Play App Signing certificate** after Play App Signing is configured. The upload certificate fingerprint may also be included for local/upload-key builds.

Generate a validated file with:

```bash
PLAY_APP_SIGNING_SHA256='AA:BB:...' \
UPLOAD_SHA256='11:22:...' \
REQUIRE_PLAY_FINGERPRINT=1 \
node android-twa/scripts/render-assetlinks.mjs /tmp/assetlinks.json
```

Only publish after both package identity and the real Play app-signing fingerprint are confirmed.

## Recommended release sequence

1. Keep production unchanged while PWA/TWA QA continues.
2. Create the app in Play Console with package `com.dontripit.app`.
3. Configure Play App Signing.
4. Create/store the upload key securely.
5. Add the four protected GitHub secrets.
6. Run `Android Signed Release` manually from `mobile-pwa`.
7. Verify the signed `.aab`, APK, SHA-256 files and upload-certificate fingerprint artifact.
8. Upload the signed `.aab` to **Internal testing** first.
9. Obtain the Play App Signing SHA-256 certificate fingerprint from Play Console.
10. Render the final `assetlinks.json` with the Play fingerprint.
11. Only when mobile QA is accepted: deploy the PWA manifest/service worker and final `/.well-known/assetlinks.json` to `dontripit.com`.
12. Verify TWA domain association on a real Android device: Don’tRipIt must open without browser chrome.
13. Run account, collection, wishlist, search, exact-print, set, price and logout smoke tests from the installed app.
14. Move to closed/production testing as required by the developer-account type.

## Testing-track rules to check in Play Console

Internal testing is the first target and supports up to 100 testers. It does not require production access.

If the Google Play developer account is a **personal account created after 2023-11-13**, Google currently requires a closed test with at least 12 opted-in testers continuously for at least 14 days before applying for production access.

Official references:
https://support.google.com/googleplay/android-developer/answer/9845334
https://support.google.com/googleplay/android-developer/answer/14151465

## Play app creation fields

When the app is created in Play Console, confirm deliberately:

- Default language
- Store name: `Don’tRipIt`
- Type: app, not game
- Free/paid decision
- Public support/contact email
- Developer Program Policies declaration
- US export-laws declaration
- Play App Signing terms

Official reference:
https://support.google.com/googleplay/android-developer/answer/9859152

## Production blockers that are intentionally not faked

The following values do not exist yet and must never be invented:

- Real upload keystore/private key
- Upload-key passwords
- Play App Signing SHA-256 fingerprint
- Play Console application record / application ID
- Production `assetlinks.json`
- A Play-internal-testing release ID

The unsigned and ephemeral-smoke-signed CI gates prove the build/signing machinery. They do **not** substitute for the real Play signing identity.
