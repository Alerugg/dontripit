# Don’tRipIt · Google Play App Content checklist

Prepared: 2026-08-14
Package: `com.dontripit.app`

This document prepares the Play Console declarations but does not submit them.

## Ads

**Proposed answer: No.**

Audited mobile release has no ad network, ad placements, advertising SDK or behavioral advertising flow.

Re-audit if ads/affiliate tracking are introduced.

## App access

**Proposed answer: All or some functionality is restricted.**

Public without login:
- home;
- TCG catalogues;
- sets;
- cards / exact prints;
- public search;
- public market reference where available;
- privacy / terms / cookies / account-deletion information.

Login required:
- dashboard/account;
- collection;
- wishlist;
- in-app account deletion.

### Reviewer access plan

Before sending the app for Google review, create a dedicated Don’tRipIt reviewer account in the real release environment and provide reusable credentials in Play Console App access.

Recommended Play instructions:

> 1. Open Don’tRipIt. The public catalogue is accessible without login.  
> 2. Tap Cuenta / Entrar.  
> 3. Sign in with the review credentials supplied below.  
> 4. Dashboard, Collection and Wishlist are then available.  
> 5. Account deletion is in Cuenta → Privacidad y control de tu cuenta → Eliminar mi cuenta.  
> 6. The account-deletion confirmation requires the account password and the word ELIMINAR.

Do not put reviewer credentials in this repository. Enter them only in the protected Play Console App access fields.

**Blocker:** reviewer account must be created only after the accepted mobile release environment exists. Do not create production test data just to satisfy this draft while `mobile-pwa` is still isolated.

## Privacy policy

Prepared URL: `https://dontripit.com/privacy`

Current production already has a privacy route. The `mobile-pwa` version additionally documents the direct deletion path and associated-data deletion. Verify the exact live policy immediately before Play submission.

## Account deletion

External resource prepared: `https://dontripit.com/delete-account`

Status:
- in-app delete engine: implemented;
- in-app discoverability: implemented;
- external deletion page: implemented on `mobile-pwa`;
- production URL: intentionally **not live yet** while mobile work remains isolated.

Do not enter the URL as a working Play URL until the route is public on `dontripit.com`.

## Target audience and content

**OWNER DECISION REQUIRED — do not auto-submit.**

Don’tRipIt is a collecting/catalogue/portfolio utility, not a children’s app. However TCG subject matter can include popular characters and artwork attractive to teens/children.

Two plausible product positions have different policy consequences:

### Option A — adult collector product

Target group: **18+ only**.

Pros:
- cleanest privacy/account position for an app collecting name/email;
- avoids intentionally targeting minors;
- matches portfolio/market-value positioning.

Trade-off:
- intentionally narrows the stated audience and may reduce discoverability for legitimate teen collectors.

### Option B — older teens + adults

Target groups may include **16–17 and 18+**, or a broader teen range if genuinely designed for them.

Consequences:
- minors become part of the declared audience in some jurisdictions;
- Google Families/target-audience requirements become relevant;
- collection of name/email from minors requires additional privacy/legal analysis and potentially parental-consent handling depending on jurisdiction.

Do not select youth age groups just to maximize reach. Select only groups Don’tRipIt is genuinely designed for and update product/privacy controls accordingly.

## Content rating questionnaire

Complete in Play Console against the exact final build. Do not pre-answer unknown questionnaire wording.

Current product facts to keep consistent:
- Don’tRipIt is a utility/catalogue, not a playable TCG game;
- users do not chat with each other;
- users do not publish public user-generated content;
- no gambling feature;
- no real-money purchases in the app;
- no dating/social matching;
- no ads;
- catalogue art is sourced from TCG/card data and may depict fantasy/comic/game artwork depending on the physical card.

## News app declaration

**Proposed position: Not a News app.**

Don’tRipIt may show official regional TCG news/releases, but news is not the primary product purpose. The core product is card/set/print cataloguing and collection management.

Verify the exact Play Console question before submission.

## Financial features / payments

**Current product:** no in-app purchases, subscriptions, marketplace checkout, financial services or crypto features.

Market references are informational card-price references, not financial investment services and not a guarantee of sale value.

## Permissions

Initial TWA deliberately enables no native notification permission and no camera/location/contact/file permissions.

If card scanning, camera, push notifications or another native capability is added later, re-open both App Content and Data Safety before release.

## Store-contact information

Prepared support email: `info@dontripit.com`

Publisher/controller identity must ultimately match the real Play developer/publisher entity and legal/privacy disclosures. Do not invent a company name, CIF or address.
