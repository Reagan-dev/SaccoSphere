# User Consent Management — Final QA Summary

Scope: the full multi-step consent hardening effort — schema (`UserConsent`,
`DataConsentLog`), serializers, endpoints (give/withdraw/list/history/export),
enforcement (`has_active_consent`/`get_consent_status`/`require_consent`),
audit-log reliability (`saccomanagement/odpc_logging.py`), export, admin
permissions, version-staleness detection, and expiration.

This pass ran the real test suite repeatedly against real code — every number
below comes from an actual `manage.py test` / `coverage` run, not estimation.

## Two real bugs found and fixed during this pass

Both were found by tracing through scenarios the earlier steps hadn't
explicitly tested, then confirmed by direct reproduction before touching any
code — not assumed from reading alone.

### 1. Re-giving consent after withdrawal (same version) silently failed

`UserConsent`'s original unique constraint was `(user, consent_type,
version)` with no condition — it didn't distinguish an active record from a
withdrawn one. Withdrawing, then giving the exact same `(consent_type,
version)` again, hit that constraint against the *withdrawn* row, fell into
`ConsentGiveView`'s `except IntegrityError` recovery path, and that path
re-fetched *any* matching row (not filtering on `withdrawn_at`) — so it
returned the stale withdrawn record with **HTTP 200**, looking like success,
while `get_consent_status` still reported `'withdrawn'`. Confirmed by direct
reproduction in a shell before any fix was applied.

**Fix:** the constraint is now conditional — `UniqueConstraint(fields=[...],
condition=Q(withdrawn_at__isnull=True), name='unique_active_user_consent_per_version')`
(`accounts/migrations/0022_userconsent_unique_active_constraint.py`), and the
view's exception-recovery re-fetch is now scoped the same way. Re-giving
after withdrawal now correctly inserts a new active row, preserving the
withdrawn one as history. Tests:
`WithdrawThenGiveAgainTestCase.test_give_after_withdraw_creates_new_active_record`,
`.test_history_preserves_both_the_withdrawal_and_the_regive`.

### 2. A new throttle test polluted shared cache state across the whole suite

Adding a test that deliberately exceeds `ConsentGiveUserThrottle`'s rate
(to prove `throttle_failure()` actually fires, not just that it's wired up)
initially only cleared the cache in `setUp`, not after. `ConsentGiveIPThrottle`
is keyed by client IP, which is constant across every test in a process — the
11 requests that test made left that shared IP counter elevated for every
later test in the same run, causing spurious 429s in unrelated tests
(reproduced directly: passed in isolation, failed inside the full suite).
**Fix:** `self.addCleanup(cache.clear)` in that test's `setUp`. Re-confirmed
clean across three consecutive full-suite runs afterward.

## Explicit scenario verification (as requested)

| Scenario | Status | Test |
|---|---|---|
| Two concurrent requests giving the same `(user, consent_type, version)` resolve idempotently, not 500 | ✅ Newly added — the existing idempotency test only covered the *sequential* pre-check-finds-a-match path; this specifically forces the `except IntegrityError` recovery branch via a genuine DB collision. Uses `APITransactionTestCase`, not `TestCase` — see note below. | `ConsentGiveRaceConditionTestCase.test_race_condition_at_database_level_recovers_without_500` |
| Withdraw-then-give-again produces sane, auditable history | ✅ Newly added (this is bug #1 above) | `WithdrawThenGiveAgainTestCase` (2 tests) |
| Version bump flips active → outdated, no migration needed | ✅ Already covered from the enforcement-consistency step | `test_stale_version_reports_outdated_not_active_and_blocks_enforcement`, `GetConsentStatusTestCase.test_returns_outdated_for_old_version` |
| Deleting a user preserves `DataConsentLog` with snapshot intact | ✅ Already covered | `DataConsentLogModelTestCase.test_user_deletion_preserves_log_with_snapshot` (+ accessed_by / both-users variants) |
| IP capture handles X-Forwarded-For behind a proxy, in a test-client scenario | ✅ Newly added — previously only the isolated utility function was tested, not the real endpoint | `ConsentGiveViewTestCase.test_ip_captured_from_x_forwarded_for_behind_proxy`, `.test_ip_captured_from_remote_addr_without_proxy` |
| Export, list, history all provably scoped to the requesting user, cross-user attempts rejected | ✅ Already covered for all three | Export: `test_export_excludes_other_users_consents`, `.test_user_id_query_param_is_ignored_cannot_target_another_user`, `.test_export_only_includes_logs_about_this_user_not_logs_they_accessed`. List: `CrossUserAccessTestCase.test_user_list_only_sees_own_consents`. History: `ConsentHistoryViewTestCase.test_user_cannot_view_another_users_history` |
| Admin's active permission configuration (Option A) is enforced | ✅ Already covered, 8 tests including a non-superuser-carve-out check | `test_consent_admin.py` |
| `create_data_consent_log`'s failure path still passes after all later changes | ✅ Re-run and confirmed passing, unchanged, after every subsequent step | `saccomanagement/tests/test_odpc_logging.py` (8 tests) |

### A note on the concurrency test's test-case class

`ConsentGiveRaceConditionTestCase` uses `APITransactionTestCase`, not the
`TestCase` every other test in this suite uses. This wasn't a style choice —
`ConsentGiveView.post` has no `@transaction.atomic` wrapper and
`settings.DATABASES` has no `ATOMIC_REQUESTS`, so in real traffic each
`.create()` is its own autocommit statement and an `IntegrityError` there
doesn't poison anything else. Plain `TestCase` wraps every test body in one
atomic block for fast rollback-based isolation; under that wrapping, the
exact same scenario raises `TransactionManagementError` on the recovery
query, because the `IntegrityError` poisons the *test's own* transaction —
which would have been a test artifact misreported as a bug. Verified this
distinction directly (a raw shell reproduction outside any test wrapper
succeeds) before choosing the test's base class.

## Coverage

Measured with `coverage.py` (added as a dev-only tool for this pass, not a
new project dependency — not added to `requirements.txt`), scoped via
`.coveragerc_consent` to exactly: `accounts/models.py`, `serializers.py`,
`views.py`, `urls.py`, `throttles.py`, `admin.py`, `utils.py`,
`services/consent.py`, both new management commands, `saccomanagement/models.py`,
`saccomanagement/odpc_logging.py`, `config/pagination.py`, `config/utils.py` —
run across the full `accounts` + `saccomanagement` suites (505 tests).

**The file-level percentages below are not a meaningful "consent feature"
number for the shared files** (`models.py`, `serializers.py`, `views.py`,
`throttles.py`, `admin.py`) — each also contains large amounts of unrelated
KYC/OTP/OAuth/erasure code exercised by other test files not focused on here.
Per the brief, coverage in that legacy code was not chased. What follows is
the *consent-specific* subset, checked line-by-line against each file's
actual class/function boundaries rather than read off the aggregate percentage:

| File | Consent-specific surface | Coverage |
|---|---|---|
| `accounts/models.py` | `UserConsent` (lines 1495–1651) | **100%** — zero missing lines/branches |
| `accounts/serializers.py` | `ConsentSerializer`, `DataConsentLogSerializer` | **100%** |
| `accounts/serializers.py` | `ConsentGiveSerializer` | 2 lines flagged missing, both confirmed dead code (see below) |
| `accounts/views.py` | `ConsentGiveView` | 1 line missing: the final defensive `raise` after a DB-level `IntegrityError` whose recovery re-fetch finds nothing — contradictory-by-construction, not reachable through legitimate use |
| `accounts/views.py` | `ConsentWithdrawView`, `ConsentListView`, `ConsentHistoryView`, `ConsentExportView` | **100%** |
| `accounts/urls.py` | all consent routes | **100%** |
| `accounts/admin.py` | `UserConsentReadOnlyAdmin` (active, Option A), `ConsentEditReasonForm` | **100%** |
| `accounts/admin.py` | `UserConsentAuditedEditAdmin` (Option B) | **0% — intentional, see below** |
| `accounts/services/consent.py` | whole module | **96%** (1 line, 1 partial branch — see below) |
| `accounts/utils.py` | `get_client_ip` | Fully covered on both real paths; one unreachable-in-practice branch |
| `accounts/throttles.py` | `ConsentGiveUserThrottle` | **100%**, including `throttle_failure()` itself |
| `accounts/throttles.py` | other 5 consent throttle classes | `throttle_failure()` body untested for 5 of 6 — see below |
| `accounts/management/commands/list_outdated_consents.py` | whole file | **100%** after this pass's additions |
| `accounts/management/commands/list_expiring_consents.py` | whole file | **100%** after this pass's additions |
| `saccomanagement/models.py` | `DataConsentLog` | **100%** |
| `saccomanagement/odpc_logging.py` | `create_data_consent_log`, `ConsentLogWriteError` | **100%** (retry, exhaustion, metrics all exercised) |
| `saccomanagement/odpc_logging.py` | `DataAccessMixin.retrieve`/`.list` | **Untested — see known gap below** |
| `config/pagination.py` | `ConsentExportConsentsPagination`, `ConsentExportAuditLogPagination` | **100%** |
| `config/utils.py` | `emit_metric` | **100%** |

## Known gaps, and why each is a legitimate "not chasing this" rather than an oversight

1. **`ConsentGiveSerializer.validate_consent_type`'s raise (line 642) and
   `validate_version`'s "empty" raise (line 650) are dead code.** `consent_type`
   is a `ChoiceField`, which rejects an invalid value at the field level
   before the custom `validate_consent_type` method ever runs. `version` is a
   `CharField` with DRF's default `trim_whitespace=True` — a whitespace-only
   value is normalized to `''` before validation, and `allow_blank=False`
   (also the default) rejects it at the field level too. Both custom checks
   are unreachable through the real API. Confirmed, not assumed: added
   `test_whitespace_only_version_rejected` specifically to probe this — it
   passes (correct 400), but coverage confirms it's the *field*-level check
   catching it, not line 650. This is harmless defensive redundancy, not a
   bug; not worth removing or "fixing" in a QA pass.
2. **`ConsentGiveView`'s final `raise` (after a recovery re-fetch that finds
   nothing) is unreachable in normal operation.** It would require the unique
   constraint to fire while the exact row that should have caused the
   conflict is simultaneously absent from a follow-up query — a contradiction
   given how the constraint and the query are defined together. Left as
   defensive code, not exercised.
3. **`throttle_failure()` is proven to work for one of six consent throttle
   classes** (`ConsentGiveUserThrottle`, `test_exceeding_give_rate_returns_429`).
   The other five (`ConsentGiveIPThrottle`, both `ConsentWithdraw*Throttle`,
   both `ConsentExport*Throttle`) have structurally identical logic (log a
   warning, raise `Throttled`) and weren't independently re-tested — doing so
   six times would be repeating verification of the same code shape, not
   covering new risk. If any of the five is ever modified, it should get its
   own test at that point rather than now.
4. **`UserConsentAuditedEditAdmin` (Option B, the audited-manual-edit admin
   config) has zero coverage — by design.** `ACTIVE_CONSENT_ADMIN_POLICY = 'A'`
   means this class is never registered with `admin.site`, so it's genuinely
   dead code today, written and ready but deliberately inactive pending the
   compliance/legal sign-off documented in the admin-permissions step. It
   would need its own tests *if and when* the policy switches to `'B'`, not
   before.
5. **`DataAccessMixin.retrieve()`/`.list()` (the actual DRF-mixin overrides,
   as opposed to the lower-level `_log_object_access`/`_get_member_user`
   helpers they call) are untested.** Every existing odpc_logging test calls
   `_log_object_access` directly against a minimal stub object+request,
   which exercises the real audit-log write path (the point of that step)
   but not the mixin's own `retrieve`/`list` wiring into a real DRF view.
   Testing that end-to-end would mean standing up one of the real admin
   views that mix this in (e.g. `AdminKYCQueueView`) with its own KYC/role
   fixtures — exactly the "unrelated legacy code" this brief said not to
   chase coverage into. Flagging this as a genuine, understood gap rather
   than silently working around it: if `DataAccessMixin` is ever refactored,
   this is the first place a regression could hide undetected.
6. **`accounts/services/consent.py:137` and a partial branch at `144->148`**
   (inside `require_consent`'s argument-extraction fallback logic) — the
   decorator tries several ways to locate `user` from `*args`/`**kwargs`
   before giving up; one specific fallback order (an object with `.request`
   but not `.user`, passed positionally, with no `user`/`request` kwarg) isn't
   hit by any current test, all of which are more direct about how they pass
   `user`. Narrow, defensive branch; not a live code path any real call site
   uses today.
7. **`list_expiring_consents.py`'s `if latest is None: continue`** is
   unreachable given the query that produces the candidate list (a user
   only becomes a candidate because a matching row already exists), and
   **`if latest.expires_at is None: continue`** is reachable only via a
   narrow edge case (a user's newest consent record for a type has no
   expiry while an older one did, e.g. because `CONSENT_EXPIRY_DURATIONS`
   was reconfigured between the two) — not tested, judged low-value for the
   effort of choreographing that specific settings-change sequence.

None of the above were "discovered and left broken" — each was traced to its
exact line, and the reasoning above is why leaving it untested is a
deliberate call, not an oversight.

## Full regression status

Every change in this pass (and every prior consent-hardening step, re-verified
here) was checked against the full `accounts` + `saccomanagement` (+ `ledger`
for the parts that touch `LedgerEntry`/statements) suite, not just the new
tests in isolation. The suite carries a pre-existing, unrelated baseline of
25 failures + 7 errors (KYC-upload/data-erasure `throttle_classes` bugs
documented in the earlier audit, phone-format-validation test assertions,
SACCO-search/superadmin test issues) that predate this consent work entirely
and are outside its scope — confirmed by name, every run, that the set never
changed as a result of anything done here. The two bugs this pass *did* find
and fix (above) were both introduced by earlier consent-hardening steps, not
by pre-existing legacy code, and are now covered by regression tests.
