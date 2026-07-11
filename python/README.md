# django-drf-foundation

Pydantic-inside-DRF wire schemas, deny-by-default permissions, and generated-TS-types
plumbing for a Django + Django REST Framework API. Extracted from a working production
app's shared foundation layer.

**The load-bearing idea:** DRF stays the HTTP layer (routing, auth, throttling); Pydantic
owns the request/response *shapes*, defined once, and a frontend generates its TypeScript
types from the same source. This package is that shared layer.

## Install

Not published to PyPI — install directly from git, pinned to a tag. This package lives in
the `python/` subdirectory of the `django-react-foundation` repo (its sibling `react-vite-foundation`
frontend package lives at that repo's root — see the top-level README for why):

```bash
uv add "django-drf-foundation @ git+https://github.com/trevor-e/django-react-foundation.git@v0.1.0#subdirectory=python"
```

## Setup

1. Add `drf_foundation` to `INSTALLED_APPS` (it ships a management command; it has no
   models/migrations, so nothing else is required):

   ```python
   INSTALLED_APPS = [
       ...,
       "rest_framework",
       "drf_foundation",
   ]
   ```

2. Wire the exception handler and deny-by-default permissions:

   ```python
   REST_FRAMEWORK = {
       "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
       "EXCEPTION_HANDLER": "drf_foundation.schemas.api_exception_handler",
   }
   ```

3. (Optional) Point the schema export somewhere other than the default
   `<BASE_DIR>/../frontend/src/types/api-schema.json`:

   ```python
   WIRE_SCHEMA_OUTPUT = BASE_DIR.parent / "frontend/src/types/api-schema.json"
   WIRE_SCHEMA_TITLE = "My API wire schema"  # optional, defaults to "API wire schema"
   ```

4. (Optional) Enable the shared ops-key auth tier for headless tooling:

   ```python
   TASK_TRIGGER_KEY = os.environ.get("TASK_TRIGGER_KEY", "")  # blank = disabled
   ```

## Usage

Declare wire models per app in `<app>/schemas.py`:

```python
# widgets/schemas.py
from drf_foundation.schemas import Schema

class Widget(Schema):
    id: int
    name: str
    price: float
```

Use the envelope helpers in views:

```python
from drf_foundation.schemas import ok, err, parse
from widgets.schemas import Widget

@api_view(["GET"])
def get_widgets(request):
    widgets = [Widget(id=1, name="Left widget", price=9.99)]
    return ok(widgets)  # {"status": "success", "data": [...]}
```

Open a route to anonymous access explicitly (never leave it implicit):

```python
from drf_foundation.permissions import public_endpoint

@api_view(["GET"])
@public_endpoint
def health_check(request): ...
```

Grep `public_endpoint` at any time for the complete, auditable public-route allowlist.

Export the combined JSON Schema for the frontend:

```bash
python manage.py export_api_schema           # writes the schema file
python manage.py export_api_schema --check   # CI drift guard: fails if stale, doesn't write
```

Pipe the output through `json-schema-to-typescript` (or any JSON-Schema-to-TS tool) on the
frontend side to generate typed API response/request models. Auto-discovery means any new
`<app>/schemas.py` — in any installed app — flows into the export with no registration step.

## Testing

```bash
uv sync
uv run pytest
```

## What this package deliberately does NOT cover

- **Auth/registration** (JWT issuance, user model, email verification) — those tie to a
  concrete `User` model and migration history, so they're a much heavier lift to share as a
  drop-in package. Copy the pattern from a reference project instead of importing it.
- **Settings boilerplate** beyond what's listed above (`CONN_MAX_AGE`, `ruff`/type-checker
  config, etc.) — those live in the blueprint doc as copy-paste recipes, not in this package,
  since a settings module isn't meaningfully "importable" the way a schema/permission layer is.
