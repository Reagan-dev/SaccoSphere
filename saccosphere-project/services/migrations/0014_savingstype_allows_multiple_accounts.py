"""Allow specific savings types to hold several accounts per member.

Two related changes:

* ``SavingsType.allows_multiple_accounts`` (bool, default ``False``) - a
  per-product opt-out of the "one account per member per type" rule, for
  products such as multiple fixed-deposit pots. Default ``False`` keeps
  every existing type behaving exactly as before.

* Drop ``Saving``'s ``unique_together = ('membership', 'savings_type')``.
  The replacement rule is conditional on the type flag, which crosses the
  ``savings_type`` relation; a Postgres partial unique index cannot
  reference a joined column, so the rule is enforced in
  ``Saving.clean()`` (``full_clean`` runs from the admin - the only
  savings-account write path today - and from any future serializer).

Online-safe assessment
----------------------
* ``AddField`` of a boolean with a constant default is metadata-only on
  PostgreSQL 11+ (no table rewrite, no row-by-row backfill).
* ``AlterUniqueTogether`` -> ``set()`` drops one unique index. Fast,
  brief ``ACCESS EXCLUSIVE`` lock on ``services_saving`` only, no
  rewrite.

Online-safe, no maintenance window at current (pre-production) volume.

Reversible
----------
Reverse re-adds the ``allows_multiple_accounts`` column drop and
re-creates the ``(membership, savings_type)`` unique index. Re-creating
the index takes a brief lock and requires the existing rows to already
satisfy it; they do, because nothing has been allowed to create a
duplicate while the constraint was only app-level unless a type had
``allows_multiple_accounts=True`` - flip those back to ``False`` before
rolling back if any exist.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0013_dividenddeclaration_unique_per_sacco_type_year'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='saving',
            unique_together=set(),
        ),
        migrations.AddField(
            model_name='savingstype',
            name='allows_multiple_accounts',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'When true, a member may hold more than one savings '
                    'account of this type (e.g. several fixed-deposit '
                    'pots). When false (the default) a member is limited '
                    'to one account of this type.'
                ),
            ),
        ),
    ]
