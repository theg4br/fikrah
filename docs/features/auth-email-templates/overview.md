# Authentication Email Templates

## Scope

The SMTP email sender renders account verification, password recovery, and new
account welcome emails from `static/emails/login.html`,
`static/emails/password_reset.html`, and `static/emails/welcome.html`. Each
message includes a plain-text part and an HTML alternative for email clients.

The shared `_base.html` layout uses the supplied light-surface icon from
`static/assets/fikrah-icon-light-mode.png`. The SMTP adapter includes its original
PNG bytes once per message as an inline MIME image, referenced by Content-ID
from the HTML. This needs no public asset URL or new environment variable. The
HTML alternative is a `multipart/related` part containing the HTML and image;
the outer message retains its plain-text alternative. A missing asset fails
delivery through the existing retry/error path instead of sending a broken image.
The dark-surface variant, `fikrah-icon-dark-mode.png`, is also packaged for local
client branding. The development site uses both variants according to the
surface, including authentication, navigation, assistant avatars, and favicons.

The login template is used for email verification because FIKRAH requires
verified email before password login. It does not add a passwordless-login flow.

## Rendering and delivery

Templates are loaded from the repository's `static/emails` directory and
rendered with HTML autoescaping. The service inserts the existing HTTPS action
URL and sends the message through the configured SMTP settings. Unsupported
email action purposes are rejected instead of falling through to another
template.

New email/password accounts receive a verification email and a welcome email
after creation. New Google accounts receive a welcome email after their account
and session are committed. Existing accounts do not receive another welcome
email when they sign in.

When using Docker Compose, the API sends these messages to the local Mailpit
service instead of an external SMTP server. Open `http://localhost:8025` to
inspect captured messages. The Compose configuration overrides SMTP host and
security settings inside the API container; a backend run directly on the host
uses the `localhost` SMTP settings in `.env`.
