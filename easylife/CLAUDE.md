# EasyLife — project rules

Backend: Django 5 + DRF. Layers: `models.py` (fields/constraints only) -> `services.py` (all business logic, in
`transaction.atomic`) -> `api/` (thin serializers/views). Design docs live in `docs/`.

## API documentation is mandatory
`docs/API_DOCUMENTATION.md` is the reference for every real endpoint (purpose, permission, request, response, errors).

- **Any change to an endpoint (new route, new HTTP method, changed request/response/permission/error) must update
  `docs/API_DOCUMENTATION.md` in the same change.**
- Format: a `#### METHOD /path` heading per endpoint (path relative to `/api/v1`, params as `{uuid}`), then the template in
  section 14 of that file. Copy real responses by running the endpoint; do not invent them.
- Add new error codes to its error catalogue (section 12) and new permissions to its permission table (section 2).
- `apps/core/tests/test_api_docs.py` fails when a route is undocumented or a documented route no longer exists.

## Auth model (do not reintroduce email/passwords)
Login is WhatsApp OTP only, for customers, staff and admins. Identity = normalized phone number.
Staff/admins are created or promoted by an admin; unknown numbers always become customers.
Admin endpoints must declare `required_permission` (default deny) and have a permission test.

## Secrets
Never put real keys in tracked files (`.env.example` holds placeholders only; `.env` is gitignored).

## Checks
`python -m pytest apps` must pass before finishing.
