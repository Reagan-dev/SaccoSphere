# Consent Type Taxonomy — Review Note

Status: **for product/legal review, not yet decided.** Nothing in this
document has been implemented. No `consent_type` values were added,
renamed, or removed as part of producing it.

## Why this needs sign-off before any code change

`UserConsent.consent_type` is evidence of what a specific user agreed to,
at a specific version of a specific policy, at a specific time. Two kinds
of change to it are not safe to make unilaterally as an engineering
decision:

- **Renaming or removing a value** invalidates every already-collected
  consent record under that value — the record would no longer map to
  anything meaningful, which is a problem under Kenya's Data Protection
  Act (2019) for records that are themselves the compliance evidence.
- **Splitting a broad category** (e.g. `MARKETING` → SMS marketing +
  email marketing) raises a question this document does not answer:
  *what happens to consent already given under the broad category?*
  Two defensible-sounding options — "treat existing `MARKETING` consent
  as covering both new sub-categories" vs. "require every existing
  marketing-consenting user to re-consent under the new categories" —
  have different legal weight, and picking one is a compliance/legal
  call, not an engineering one. This document does not recommend either.

Nothing below proposes specific new category names or an answer to that
question. It only establishes that the *mechanism* for adding a new
value later is safe, additive, and already in place.

## Current `consent_type` values

Source of truth: `UserConsent.ConsentType` in `accounts/models.py`.

| Value | Label | Introduced |
|---|---|---|
| `TERMS` | Terms | `accounts/migrations/0001_initial.py` |
| `PRIVACY` | Privacy | `accounts/migrations/0001_initial.py` |
| `DATA_PROCESSING` | Data processing | `accounts/migrations/0001_initial.py` |
| `MARKETING` | Marketing | `accounts/migrations/0001_initial.py` |

All four have existed since the original `UserConsent` migration; none
have been renamed since.

## Confirmed: the field is already extensible (item 1)

`consent_type` is a plain `models.CharField(max_length=30, choices=...)`
backed by `UserConsent.ConsentType`, a Django `TextChoices` class — this
was already the case; no refactor was needed or made.

Verified directly, not assumed:

- **No database-level constraint restricts the value.** `UserConsent.Meta`
  has one `UniqueConstraint` (on `user`, `consent_type`, `version`) and no
  `CheckConstraint`. `choices=` is Django-level validation only (forms,
  serializers, admin dropdowns) — the column itself accepts any string up
  to 30 characters. Adding a new choice is a metadata-only migration; it
  does not alter the column type or touch existing rows.
- **No second, independently-maintained list of the four values exists in
  application logic.** Every real call site reads `UserConsent.ConsentType.
  choices` or `.values` dynamically:
  - `accounts/serializers.py` (`ConsentGiveSerializer.consent_type`,
    validation error message)
  - `accounts/views.py` (`ConsentListView` loops `.values`)
  - `accounts/management/commands/list_outdated_consents.py` and
    `list_expiring_consents.py` (loop `.values`)
  - `accounts/admin.py` (`list_filter` uses Django's own introspection)
  - `drf-yasg`'s generated OpenAPI schema (derived from the serializer
    field, not hand-maintained)

  The only literal, single-value reference outside the model/tests is
  `saccomanagement/bulk_sms_views.py`'s use of `UserConsent.ConsentType.
  MARKETING` to gate the bulk-SMS-campaign audience — that is a specific
  business rule intentionally scoped to one type, not a duplicated
  taxonomy, and needs no change regardless of what else is added.
- **`config/settings/base.py`'s `CONSENT_POLICY_VERSIONS` and
  `CONSENT_EXPIRY_DURATIONS` are plain dicts keyed by `consent_type`
  string**, not a second enum. They are already a one-line-per-type
  addition — see the checklist below for why a new value needs an entry
  in at least the first one.

**Conclusion: no restructuring is required.** The mechanism was already
built for additive extension; what's missing is the taxonomy decision
itself, which is out of scope here.

## What actually needs to change when a value is added (checklist)

None of this is done. This is the checklist for whoever implements a
decided taxonomy change later.

1. Add the new member to `UserConsent.ConsentType` in `accounts/models.py`.
2. Run `manage.py makemigrations accounts` — generates a metadata-only
   `AlterField` migration (see template below). No data migration is
   needed for the new value itself (existing rows are unaffected), but
   see point 6 if the change also involves reinterpreting an existing
   value.
3. Add an entry to `settings.CONSENT_POLICY_VERSIONS` for the new type.
   **This is easy to forget and fails silently**: `get_consent_status`
   and `has_active_consent` both do `settings.CONSENT_POLICY_VERSIONS.get
   (consent_type)`; with no entry, a user who gives consent for the new
   type will have it recorded but `get_consent_status` will report
   `'outdated'` forever (never `'active'`), and `has_active_consent` will
   always return `False` — because `current_version` comes back `None`
   and can never equal a real stored version.
4. Optionally add an entry to `settings.CONSENT_EXPIRY_DURATIONS` if the
   new type should expire — otherwise it defaults to never expiring,
   consistent with every current type.
5. Update `accounts/tests/test_consent_views.py::ConsentListViewTestCase
   ::test_list_consents_returns_all_types` — it hard-codes
   `self.assertEqual(len(response.data), 4)  # All 4 consent types`. This
   is the one place in the test suite that assumes a fixed count; it
   would need updating to match the new total. (Confirmed via a
   project-wide grep for hard-coded consent-type counts — this is the
   only one.)
6. If this is a **split** of an existing category rather than a wholly
   new one: decide (with legal) what happens to existing `UserConsent`
   rows recorded under the old value. That decision may itself require a
   data migration (e.g. backfilling a new column, or leaving old rows as
   historical-only evidence and prompting fresh consent) — this is
   exactly the sign-off this document is deferring, and is likely more
   consequential than the schema change itself.

## Proposed migration/PR template (illustrative only — not applied)

This uses a placeholder name, `EXAMPLE_NEW_TYPE`, purely to show the
shape of the change. It is not a proposed real value.

**1. Model change** (`accounts/models.py`):

```python
class ConsentType(models.TextChoices):
    TERMS = 'TERMS', 'Terms'
    PRIVACY = 'PRIVACY', 'Privacy'
    DATA_PROCESSING = 'DATA_PROCESSING', 'Data processing'
    MARKETING = 'MARKETING', 'Marketing'
    EXAMPLE_NEW_TYPE = 'EXAMPLE_NEW_TYPE', 'Example new type'  # + new line
```

**2. Generated migration** (`accounts/migrations/00NN_alter_userconsent_consent_type.py`,
shape based on this project's existing `AlterField`-style migrations,
e.g. `0019_alter_userconsent_ip_address_and_more.py`):

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '00XX_previous_migration'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userconsent',
            name='consent_type',
            field=models.CharField(
                choices=[
                    ('TERMS', 'Terms'),
                    ('PRIVACY', 'Privacy'),
                    ('DATA_PROCESSING', 'Data processing'),
                    ('MARKETING', 'Marketing'),
                    ('EXAMPLE_NEW_TYPE', 'Example new type'),
                ],
                help_text='Type of consent being recorded.',
                max_length=30,
            ),
        ),
    ]
```

This is metadata-only (a `CharField(max_length=30)` already accepts the
new string) — no `RunPython` data step is needed for the addition itself.

**3. Settings** (`config/settings/base.py`):

```python
CONSENT_POLICY_VERSIONS = {
    'TERMS': 'v1.0',
    'PRIVACY': 'v1.0',
    'DATA_PROCESSING': 'v1.0',
    'MARKETING': 'v1.0',
    'EXAMPLE_NEW_TYPE': 'v1.0',  # + new line
}
```

**4. Test update** (`accounts/tests/test_consent_views.py`):

```python
self.assertEqual(len(response.data), 5)  # All 5 consent types
```

No changes are needed to `ConsentGiveView`, `ConsentWithdrawView`,
`ConsentListView`, `ConsentHistoryView`, `has_active_consent`,
`get_consent_status`, or `require_consent` — all of them already operate
generically on whatever `consent_type` string they're given.

## Confirmed by test: a type with no prior history behaves safely (item 2)

`accounts/tests/test_consent_service.py::NewConsentTypeExtensibilityTestCase`
calls `get_consent_status` and `has_active_consent` with a string that is
*not* a current `ConsentType` value (`'SMS_MARKETING'`, used only as an
example string — not a proposal) and confirms:

- `get_consent_status(user, 'SMS_MARKETING')` returns `'never_given'`
- `has_active_consent(user, 'SMS_MARKETING')` returns `False`

Neither function validates `consent_type` against the model's registered
choices before querying — they filter generically — so this is a
faithful simulation of "day one" for a genuinely new type, without this
task adding one. This was already the expected behavior per the existing
`never_given` handling (Prompt 4); this test confirms it holds for a type
with zero registered history, not just an existing type the user hasn't
consented to yet.

The give/withdraw/list endpoints were not separately re-tested here
because they call these same service functions and validate `consent_type`
via `UserConsent.ConsentType.values` (see `ConsentGiveSerializer.
validate_consent_type`) — today, correctly, that means the give endpoint
rejects a not-yet-registered type with a 400. Once a type is actually
added to the enum (step 1 of the checklist above), it becomes a normal
registered choice and the existing give/withdraw/list flow handles it
with no further code change, per the "no changes needed" list above.
