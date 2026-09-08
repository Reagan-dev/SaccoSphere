"""Domain services for SACCO membership workflows."""

from django.db import transaction

from accounts.models import Sacco


# TODO(product): confirm member_number format. This is a placeholder
# (SACCO id prefix + zero-padded sequence) pending a product/legal decision
# on the actual member number scheme SACCOs should use.
MEMBER_NUMBER_FORMAT = '{sacco_id}-{seq:06d}'


def generate_member_number(sacco):
    """
    Return the next unique, SACCO-scoped member number for `sacco`.

    Safe under concurrent import/approval: the SACCO row is locked with
    select_for_update() and its counter incremented inside an atomic
    transaction, so two concurrent callers for the same SACCO cannot be
    handed the same number.

    This function always consumes a new sequence number on every call — it
    does not check whether a membership already has a member_number. Callers
    are responsible for calling this only when a member_number is not yet
    assigned, so that assignment as a whole stays idempotent.
    """
    with transaction.atomic():
        locked_sacco = Sacco.objects.select_for_update().get(pk=sacco.pk)
        locked_sacco.next_member_number_seq += 1
        locked_sacco.save(update_fields=['next_member_number_seq'])
        seq = locked_sacco.next_member_number_seq

    return MEMBER_NUMBER_FORMAT.format(sacco_id=locked_sacco.id, seq=seq)
