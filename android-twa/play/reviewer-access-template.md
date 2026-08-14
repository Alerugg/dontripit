# Google Play reviewer access · Don’tRipIt

Use this template only after a dedicated reviewer account has been created in the final release environment.

Do **not** commit the real reviewer email or password to Git.

## Play Console App access

Restricted functionality: **Yes**

Prepared instructions:

1. Open Don’tRipIt. Public catalogue, sets, cards, exact prints and search are available without signing in.
2. Open **Cuenta / Entrar**.
3. Sign in using the reusable reviewer credentials entered in the protected Play Console fields.
4. After login, the reviewer can access Dashboard, Collection and Wishlist.
5. Account deletion is available from **Cuenta → Privacidad y control de tu cuenta → Eliminar mi cuenta**.
6. To exercise deletion, enter the reviewer account password and type **ELIMINAR**.
7. External account-deletion information is available at `https://dontripit.com/delete-account` once the mobile web release is deliberately made public.

## Credentials — enter only in Play Console

- Username/email: `[PLAY_CONSOLE_PROTECTED_FIELD]`
- Password: `[PLAY_CONSOLE_PROTECTED_FIELD]`

The credentials must remain active, reusable and not depend on OTP/2FA, geography, invitation expiry or a one-time link during Google review.

## Reviewer-account rules

- dedicated account only; do not use an owner’s personal account;
- no personal/private collection data;
- seed only enough harmless test data to demonstrate collection/wishlist if useful;
- keep the account valid throughout all active Play reviews;
- if the account is deleted during a review test, recreate it and immediately update Play Console credentials before the next review.
