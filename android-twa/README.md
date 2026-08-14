# Don’tRipIt Android TWA

This directory is the reproducible configuration source for the Android wrapper of Don’tRipIt.

## Identity

- Package ID: `com.dontripit.app`
- App name: `Don’tRipIt`
- Production origin: `https://dontripit.com`
- Generator: Bubblewrap `1.25.0`
- Initial Android version: `0.1.0` (`versionCode` 1)

## Safety model

The generated Gradle project, APK/AAB outputs and every keystore are ignored. No private signing key is committed to GitHub.

CI may build an **unsigned** Android App Bundle to prove that the TWA project is reproducible. Release signing is a separate gate and will use a protected upload key / Play App Signing.

## Digital Asset Links

A Trusted Web Activity removes browser chrome only after Android/Chrome verifies the relationship between the app and `dontripit.com`.

`assetlinks.template.json` intentionally contains placeholders. Do not publish it until the actual SHA-256 fingerprints are known.

For a Google Play release we should ultimately include the Play App Signing SHA-256 fingerprint. A local/upload fingerprint can also be included for local verification.

The final file will be served from:

`https://dontripit.com/.well-known/assetlinks.json`

## Production rule

Nothing in this directory changes `catalog-v2` or the production deployment by itself. The PWA/TWA work remains isolated on `mobile-pwa` until the mobile release is accepted.
