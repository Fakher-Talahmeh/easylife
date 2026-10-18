from django.db import transaction

from apps.core import audit

from .models import ShopProfile, SocialLink


@transaction.atomic
def update_profile(*, actor, data, request=None):
    """Saves the shop profile; `social_links`, when sent, REPLACES the whole list (the admin edits it as one block)."""
    data = dict(data)
    links = data.pop("social_links", None)
    profile = ShopProfile.load()
    for key, value in data.items():
        setattr(profile, key, value)
    profile.save()
    if links is not None:
        profile.social_links.all().delete()
        SocialLink.objects.bulk_create(
            [SocialLink(profile=profile, **{**link, "sort_order": link.get("sort_order", i)}) for i, link in enumerate(links)])
    audit.log("store_profile.updated", actor=actor, request=request, obj=profile,
              fields=sorted(data) + (["social_links"] if links is not None else []))
    return profile
