---
feature: Themed multipart transactional email with preview drift checks and a provider seam
code: [python/src/drf_foundation/emails.py, python/src/drf_foundation/email_provider.py, python/src/drf_foundation/email_previews.py, python/src/drf_foundation/management/commands/render_email_previews.py]
---
# transactional-email

## Rules
- [transactional-email.multipart] Every rendered message has a plain-text primary body and an HTML alternative with the same meaning. {pre-kanspec}
- [transactional-email.safe-html] Email templates use inline, table-oriented markup, keep autoescaping enabled, and load no remote assets. {pre-kanspec}
- [transactional-email.theme] A consumer supplies brand and palette through `EmailTheme`; built-in message kinds share the same shell and remain usable without project-specific templates. {pre-kanspec}
- [transactional-email.provider-seam] Application code sends through the package delivery seam so switching between Django and Resend does not fork rendering or multipart behavior. {pre-kanspec}
- [transactional-email.previews] Each built-in or registered project email kind has deterministic fixture-backed preview HTML, and `render_email_previews --check` fails CI on drift without rewriting. {pre-kanspec}
