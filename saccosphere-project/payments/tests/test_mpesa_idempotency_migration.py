"""MpesaIdempotencyRecord.checkout_request_id -> external_reference_id
rename + kind backfill (migrations 0011/0012).

checkout_request_id used to be the sole unique key, reused to hold
Safaricom's ConversationID for B2C too - correct only because the
ws_CO_... (STK) and AG_... (B2C) id namespaces happen not to collide.
This exercises the migrations themselves - not just the resulting model
- against representative pre-existing data shaped exactly like what the
old schema actually held, to prove the rename preserves data and the
kind backfill classifies both prefixes (plus an unrecognized one)
correctly.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from payments.models import MpesaIdempotencyRecord


MIGRATE_FROM = ('payments', '0010_alter_callback_raw_payload')
MIGRATE_TO = ('payments', '0012_mpesaidempotencyrecord_kind')


class MpesaIdempotencyRecordMigrationTest(TransactionTestCase):
    """Runs the real migration executor: seeds rows under the pre-
    migration schema, migrates forward, and inspects the result under
    the post-migration schema."""

    def tearDown(self):
        # Bring every app's schema back to its latest state for the
        # rest of the suite - the executor above left it at MIGRATE_TO.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_rename_and_kind_backfill_apply_cleanly(self):
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATE_FROM])
        executor.loader.build_graph()

        OldRecord = executor.loader.project_state(
            [MIGRATE_FROM],
        ).apps.get_model('payments', 'MpesaIdempotencyRecord')

        # Representative existing data: real STK and B2C id shapes, plus
        # one that matches neither prefix (defensive edge case).
        OldRecord.objects.using(connection.alias).create(
            checkout_request_id='ws_CO_191220191020363925',
        )
        OldRecord.objects.using(connection.alias).create(
            checkout_request_id='AG_20191219_0000774e2f7ab7b158c7',
        )
        OldRecord.objects.using(connection.alias).create(
            checkout_request_id='unrecognized-legacy-id',
        )

        # The migration under test - must not raise.
        executor.migrate([MIGRATE_TO])
        executor.loader.build_graph()

        NewRecord = executor.loader.project_state(
            [MIGRATE_TO],
        ).apps.get_model('payments', 'MpesaIdempotencyRecord')

        rows = {
            row.external_reference_id: row.kind
            for row in NewRecord.objects.using(connection.alias).all()
        }

        # Data preserved by the rename - same three ids, now under the
        # new column name.
        self.assertEqual(
            set(rows),
            {
                'ws_CO_191220191020363925',
                'AG_20191219_0000774e2f7ab7b158c7',
                'unrecognized-legacy-id',
            },
        )
        # Backfilled by prefix.
        self.assertEqual(rows['ws_CO_191220191020363925'], 'STK')
        self.assertEqual(rows['AG_20191219_0000774e2f7ab7b158c7'], 'B2C')
        # Unrecognized prefix falls back to STK rather than being left
        # NULL (see the migration's own docstring).
        self.assertEqual(rows['unrecognized-legacy-id'], 'STK')


class KindDiscriminatorTest(TransactionTestCase):
    """Post-migration behaviour of the model itself: new records get
    the correct kind, and uniqueness is scoped to (kind,
    external_reference_id) - the deliberate choice documented on
    MpesaIdempotencyRecord, not global uniqueness on the id alone."""

    def test_new_records_get_the_kind_the_caller_declares(self):
        stk = MpesaIdempotencyRecord.objects.create(
            kind=MpesaIdempotencyRecord.Kind.STK,
            external_reference_id='ws_CO_KIND_TEST_001',
        )
        b2c = MpesaIdempotencyRecord.objects.create(
            kind=MpesaIdempotencyRecord.Kind.B2C,
            external_reference_id='AG_KIND_TEST_001',
        )

        self.assertEqual(stk.kind, MpesaIdempotencyRecord.Kind.STK)
        self.assertEqual(b2c.kind, MpesaIdempotencyRecord.Kind.B2C)

    def test_same_id_different_kind_is_allowed(self):
        """Uniqueness is scoped per kind, not global on the id - a
        deliberate choice (see the model docstring): the two id
        namespaces don't collide today, but nothing here should keep
        depending on that being true forever. An identical string under
        two different kinds is therefore two distinct idempotency
        records, not a collision."""
        shared_id = 'COLLIDING_ID_001'
        MpesaIdempotencyRecord.objects.create(
            kind=MpesaIdempotencyRecord.Kind.STK,
            external_reference_id=shared_id,
        )
        MpesaIdempotencyRecord.objects.create(
            kind=MpesaIdempotencyRecord.Kind.B2C,
            external_reference_id=shared_id,
        )

        self.assertEqual(
            MpesaIdempotencyRecord.objects.filter(
                external_reference_id=shared_id,
            ).count(),
            2,
        )

    def test_same_kind_same_id_is_rejected(self):
        """Within one kind, the id must still be unique - this is the
        actual idempotency guarantee the callback pipeline relies on."""
        from django.db import IntegrityError

        MpesaIdempotencyRecord.objects.create(
            kind=MpesaIdempotencyRecord.Kind.STK,
            external_reference_id='ws_CO_DUPLICATE_001',
        )

        with self.assertRaises(IntegrityError):
            MpesaIdempotencyRecord.objects.create(
                kind=MpesaIdempotencyRecord.Kind.STK,
                external_reference_id='ws_CO_DUPLICATE_001',
            )

    def test_get_or_create_is_the_call_sites_own_idempotency_guard(self):
        """Mirrors exactly how payments.tasks uses this model: the
        second get_or_create for the same (kind, id) finds the existing
        row instead of raising - this is what makes a callback/
        reconciliation re-delivery a clean no-op."""
        _first, created_first = MpesaIdempotencyRecord.objects.get_or_create(
            kind=MpesaIdempotencyRecord.Kind.B2C,
            external_reference_id='AG_GETORCREATE_001',
        )
        _second, created_second = (
            MpesaIdempotencyRecord.objects.get_or_create(
                kind=MpesaIdempotencyRecord.Kind.B2C,
                external_reference_id='AG_GETORCREATE_001',
            )
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(
            MpesaIdempotencyRecord.objects.filter(
                external_reference_id='AG_GETORCREATE_001',
            ).count(),
            1,
        )
