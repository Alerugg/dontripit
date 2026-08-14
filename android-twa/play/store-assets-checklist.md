# Don’tRipIt · Google Play store assets checklist

Verified against current Google Play Console Help on 2026-08-14.

## App icon

Required for the store listing:

- PNG, 32-bit with alpha;
- 512 × 512 px;
- maximum 1024 KB;
- no fake rankings, price badges, Play categories or misleading promotional text.

Current source asset prepared in the PWA:

`frontend/public/icons/dontripit-512.png`

Before upload, visually confirm it is the desired high-fidelity Play Store icon and not merely a temporary launcher graphic.

## Feature graphic

Required dimensions:

- JPEG or 24-bit PNG without alpha;
- 1024 × 500 px.

Creative rule for Don’tRipIt:

- use the dark/purple Don’tRipIt visual system;
- communicate catalogue / exact-print / collection utility;
- avoid fake prices, fake ratings, fake awards and fake catalogue results;
- avoid third-party publisher logos as if they were Don’tRipIt branding;
- do not imply official affiliation with Pokémon, Magic, One Piece, Yu-Gi-Oh!, Riot, Cardmarket or other rights holders.

**Status:** final asset must be created/approved from the accepted release visual system before store submission.

## Screenshots

Google Play currently requires a minimum of two screenshots across supported device types to publish a store listing.

Technical constraints:

- JPEG or 24-bit PNG without alpha;
- minimum dimension 320 px;
- maximum dimension 3840 px;
- maximum dimension must not be more than twice the minimum dimension.

For Don’tRipIt, capture real UI from the accepted release build. Recommended phone sequence:

1. Home / search entry;
2. search results with exact versions;
3. card or exact-print detail;
4. collection/dashboard;
5. wishlist or set browsing.

Do not compose screenshots that show functionality or data unavailable in the uploaded AAB/web release.

## Capture gate

Final screenshots should be captured only when all of these are true:

- the accepted mobile UI is fixed;
- the same web/PWA build intended for the TWA is available;
- no fake/demo prices or placeholder content are visible;
- account/private data shown is a dedicated test account with no personal information;
- status/navigation bars do not expose unrelated notifications or private device data.

## Why final graphics are not committed yet

The app binary and metadata can be prepared reproducibly before Play Console exists. Store screenshots and the feature graphic are visual release assets and must represent the final accepted UI. Creating them from an intermediate preview would make the package less deployment-ready, not more.

When the Play account is available, capture/approve these assets immediately before the first Internal Testing listing is completed.
