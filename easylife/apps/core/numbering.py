from django.db import transaction

from .models import DocumentSequence


def next_number(key, prefix, padding=6):
    """Return the next document number for `key`, e.g. next_number("adjustment", "ADJ-") -> "ADJ-000001".

    Takes a row lock, so concurrent callers never get the same number. Call inside the same
    transaction that creates the document so a rollback does not burn a number."""
    with transaction.atomic():
        seq, _ = DocumentSequence.objects.select_for_update().get_or_create(key=key)
        value = seq.next_value
        seq.next_value = value + 1
        seq.save(update_fields=["next_value"])
    return f"{prefix}{value:0{padding}d}"
