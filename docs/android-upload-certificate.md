# Don’tRipIt Android upload certificate

Generated: 2026-08-14

This file contains **public certificate identity only**. It does not contain a private key, keystore, password or base64 secret.

- Package: `com.dontripit.app`
- Upload-key alias: `dontripit-upload`
- Algorithm: RSA 2048
- Certificate SHA-256: `99:A1:DD:B3:25:FB:1E:7C:A7:03:C4:FD:34:AF:C7:60:0B:E5:66:D2:B3:FF:80:B3:D8:3A:A9:06:47:5D:0A:3F`
- Keystore file SHA-256: `cbf6f8d7c967c0e52da517649819610a8d3e8d23c69a91c7118aa9e791d1648b`

The private upload keystore is intentionally stored outside Git.

For production TWA association through Google Play, the final `assetlinks.json` must use the **Play App Signing certificate SHA-256** after Play App Signing is enabled. The upload certificate above can additionally be included for local/upload-key verification, but it does not replace the Play signing fingerprint.
