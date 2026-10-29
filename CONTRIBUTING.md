# Contributing

Thank you for helping improve FHStats Tracker.

## Scope

Changes here should target the public Windows desktop application. Hosted service code, personal runtime data and private owner tools belong outside this repository. Keep English and Spanish UI behavior equivalent.

## Before opening a pull request

1. Explain the user-visible problem and provide reproduction steps.
2. Make the smallest practical change and add a meaningful regression test when appropriate.
3. Run the desktop tests and isolated startup check shown in the README.
4. Check your diff for credentials, personal settings, screenshots, recordings, game captures and unrelated generated files.
5. Include validation results and known limitations in the pull request description.

Do not add private automation modules, credentials or server operations. Do not attach actual user timing databases to a public issue. Use synthetic fixtures and redact personal paths and tokens.

Describe capture sources accurately. A memory observation, a UDP packet, a legacy OCR result and a server-approved event result are different kinds of evidence.
