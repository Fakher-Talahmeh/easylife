"""Catalog write logic: products, variants, prices, images, categories, price lists."""

import io

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.core import audit
from apps.core.exceptions import ServiceError

from .models import (
    Brand, Category, PriceList, Product, ProductImage, ProductPrice, ProductStatus, ProductVariant,
)

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
WEBP_QUALITY = 88


def _open_upright(file):
    """Opens an uploaded picture with its EXIF rotation applied, in a mode WebP can store (keeps transparency)."""
    with Image.open(file) as img:
        img = ImageOps.exif_transpose(img)
        has_alpha = "A" in img.getbands() or "transparency" in img.info
        return img.convert("RGBA" if has_alpha else "RGB")


def to_webp(img, name):
    """Every picture the system stores is a WebP: encodes `img` and returns it as a named file ready for an ImageField."""
    out = io.BytesIO()
    img.save(out, "WEBP", quality=WEBP_QUALITY, method=4)
    return ContentFile(out.getvalue(), name=f"{name}.webp")


def unique_slug(model, name, exclude_pk=None):
    base = slugify(name, allow_unicode=True)[:60] or model.__name__.lower()
    slug, n = base, 2
    while model.objects.filter(slug=slug).exclude(pk=exclude_pk).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def _inventory():
    # Imported lazily: inventory depends on catalog, not the other way round.
    from apps.inventory import services as inventory_services

    return inventory_services


# ---------------------------------------------------------------- categories


@transaction.atomic
def save_category(*, actor, instance=None, data, request=None):
    parent = data.get("parent")
    if instance is not None and parent is not None:
        node = parent
        while node is not None:  # no cycles
            if node.pk == instance.pk:
                raise ServiceError("category_cycle", "A category cannot be its own ancestor.")
            node = node.parent
    if instance is None:
        instance = Category(**data)
        instance.slug = unique_slug(Category, data["name"])
    else:
        for k, v in data.items():
            setattr(instance, k, v)
        if "name" in data:
            instance.slug = unique_slug(Category, instance.name, exclude_pk=instance.pk)
    instance.save()
    audit.log("category.saved", actor=actor, request=request, obj=instance)
    return instance


@transaction.atomic
def delete_category(*, actor, category, request=None):
    if category.children.exists() or category.products.filter(deleted_at__isnull=True).exists():
        raise ServiceError("category_in_use", "Move its products and sub-categories first, or deactivate it.")
    audit.log("category.deleted", actor=actor, request=request, obj=category, name=category.name)
    if category.image:
        category.image.delete(save=False)
    category.delete()


CATEGORY_IMAGE_SIZE = 800  # square, in pixels


def _category_square(file, name):
    """Category pictures are shown in a round frame: store one square (centre crop), at most 800px."""
    img = _open_upright(file)
    side = min(img.size)
    square = ImageOps.fit(img, (min(side, CATEGORY_IMAGE_SIZE),) * 2, method=Image.Resampling.LANCZOS)
    return to_webp(square, name)


@transaction.atomic
def set_category_image(*, actor, category, file, request=None):
    validate_image(file)
    picture = _category_square(file, f"{category.slug[:40]}-{timezone.now():%H%M%S%f}")
    if category.image:
        category.image.delete(save=False)
    category.image = picture
    category.save()
    audit.log("category.image_set", actor=actor, request=request, obj=category, name=category.name)
    return category


@transaction.atomic
def remove_category_image(*, actor, category, request=None):
    if category.image:
        category.image.delete(save=False)
        category.image = None
        category.save()
        audit.log("category.image_removed", actor=actor, request=request, obj=category, name=category.name)
    return category


# ---------------------------------------------------------------- brands


@transaction.atomic
def save_brand(*, actor, instance=None, data, request=None):
    if "name" in data:
        data["name"] = data["name"].strip()
        taken = Brand.objects.filter(name__iexact=data["name"]).exclude(pk=getattr(instance, "pk", None))
        if taken.exists():
            raise ServiceError("brand_taken", f"The brand '{data['name']}' already exists.",
                               fields={"name": ["A brand with this name already exists."]})
    if instance is None:
        instance = Brand.objects.create(**data)
    else:
        for k, v in data.items():
            setattr(instance, k, v)
        instance.save()
    audit.log("brand.saved", actor=actor, request=request, obj=instance, name=instance.name)
    return instance


@transaction.atomic
def delete_brand(*, actor, brand, request=None):
    if brand.products.filter(deleted_at__isnull=True).exists():
        raise ServiceError("brand_in_use", "Move its products to another brand first, or deactivate it.")
    audit.log("brand.deleted", actor=actor, request=request, obj=brand, name=brand.name)
    if brand.logo:
        brand.logo.delete(save=False)
    brand.delete()


LOGO_RATIO = 1.5  # width / height of every stored brand picture
LOGO_MAX = (960, 640)


def _logo_canvas(file, name):
    """Every brand picture is stored on the same 3:2 transparent canvas (never cropped or stretched), so the shop can
    show all of them at one size. Large pictures are scaled down; small ones are not enlarged."""
    img = _open_upright(file).convert("RGBA")
    img.thumbnail(LOGO_MAX)
    w, h = img.size
    cw, ch = (w, round(w / LOGO_RATIO)) if w / h > LOGO_RATIO else (round(h * LOGO_RATIO), h)
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    canvas.paste(img, ((cw - w) // 2, (ch - h) // 2))
    return to_webp(canvas, name)


@transaction.atomic
def set_brand_logo(*, actor, brand, file, request=None):
    """Replaces the brand's picture (the old file is removed). Same image rules as product images."""
    validate_image(file)
    picture = _logo_canvas(file, f"{brand.slug[:40]}-{timezone.now():%H%M%S%f}")
    if brand.logo:
        brand.logo.delete(save=False)
    brand.logo = picture
    brand.save()
    audit.log("brand.logo_set", actor=actor, request=request, obj=brand, name=brand.name)
    return brand


@transaction.atomic
def remove_brand_logo(*, actor, brand, request=None):
    if brand.logo:
        brand.logo.delete(save=False)
        brand.logo = None
        brand.save()
        audit.log("brand.logo_removed", actor=actor, request=request, obj=brand, name=brand.name)
    return brand


# ---------------------------------------------------------------- price lists


@transaction.atomic
def save_price_list(*, actor, instance=None, data, request=None):
    currency = data.get("currency") or getattr(instance, "currency", None)
    if instance is not None and "currency" in data and data["currency"] != instance.currency:
        if ProductPrice.objects.filter(price_list=instance).exists():
            raise ServiceError("currency_locked", "A price list that already has prices cannot change currency.")
    if data.get("is_default"):
        PriceList.objects.filter(currency=currency, is_default=True).exclude(
            pk=getattr(instance, "pk", None)).update(is_default=False)
    if instance is None:
        instance = PriceList(**data)
    else:
        for k, v in data.items():
            setattr(instance, k, v)
    instance.save()
    audit.log("price_list.saved", actor=actor, request=request, obj=instance)
    return instance


# ---------------------------------------------------------------- variants & prices


def _check_sku_barcode(sku, barcode, exclude_pk=None):
    qs = ProductVariant.objects.exclude(pk=exclude_pk)
    if qs.filter(sku__iexact=sku).exists():
        raise ServiceError("sku_taken", f"SKU {sku} is already used.", fields={"sku": ["Already used."]})
    if barcode and qs.filter(barcode=barcode).exists():
        raise ServiceError("barcode_taken", f"Barcode {barcode} is already used.",
                           fields={"barcode": ["Already used."]})


@transaction.atomic
def set_prices(*, actor, variant, rows, request=None):
    """Replace the variant's prices with `rows` [{price_list, price, compare_at_price?}]."""
    seen = set()
    for row in rows:
        pl = row["price_list"]
        if pl.pk in seen:
            raise ServiceError("duplicate_price_list", fields={"prices": ["Each price list only once."]})
        seen.add(pl.pk)
        if not pl.is_active:
            raise ServiceError("price_list_inactive", f"Price list {pl.name} is inactive.")
        compare = row.get("compare_at_price")
        if compare is not None and compare < row["price"]:
            raise ServiceError("invalid_compare_price", fields={"prices": ["compare_at_price must be >= price."]})
    ProductPrice.objects.filter(variant=variant).exclude(price_list_id__in=seen).delete()
    for row in rows:
        ProductPrice.objects.update_or_create(
            variant=variant, price_list=row["price_list"],
            defaults={"price": row["price"], "compare_at_price": row.get("compare_at_price")},
        )
    audit.log("variant.prices_set", actor=actor, request=request, obj=variant, count=len(rows))
    return variant


@transaction.atomic
def add_variant(*, actor, product, data, prices=None, request=None):
    if product.deleted_at:
        raise ServiceError("product_deleted", "This product was deleted.")
    data = dict(data)
    data["sku"] = data["sku"].strip()
    _check_sku_barcode(data["sku"], data.get("barcode", ""))
    variant = ProductVariant.objects.create(product=product, **data)
    if data.get("cost_price"):
        audit.log("variant.cost_changed", actor=actor, request=request, obj=variant, sku=variant.sku,
                  old="0", new=str(variant.cost_price), source="manual")
    if prices:
        set_prices(actor=actor, variant=variant, rows=prices, request=request)
    audit.log("variant.created", actor=actor, request=request, obj=variant, sku=variant.sku)
    return variant


@transaction.atomic
def update_variant(*, actor, variant, data, request=None):
    if "sku" in data:
        data["sku"] = data["sku"].strip()
        if data["sku"] != variant.sku and _inventory().has_stock_history([variant.pk]):
            raise ServiceError("sku_locked", "The SKU cannot change once the variant has stock history.")
    _check_sku_barcode(data.get("sku", variant.sku), data.get("barcode", variant.barcode), exclude_pk=variant.pk)
    old_cost = variant.cost_price
    for k, v in data.items():
        setattr(variant, k, v)
    variant.save()
    if "cost_price" in data and variant.cost_price != old_cost:
        audit.log("variant.cost_changed", actor=actor, request=request, obj=variant, sku=variant.sku,
                  old=str(old_cost), new=str(variant.cost_price), source="manual")
    audit.log("variant.updated", actor=actor, request=request, obj=variant, fields=sorted(data))
    return variant


@transaction.atomic
def delete_variant(*, actor, variant, request=None):
    if _inventory().has_stock_history([variant.pk]):
        raise ServiceError("variant_in_use", "This variant has stock history. Deactivate it instead.")
    active_left = variant.product.variants.filter(deleted_at__isnull=True, is_active=True).exclude(pk=variant.pk)
    if variant.product.status == ProductStatus.ACTIVE and not active_left.exists():
        raise ServiceError("last_variant", "An active product needs at least one active variant. Archive the product instead.")
    variant.deleted_at = timezone.now()
    variant.is_active = False
    variant.save(update_fields=["deleted_at", "is_active", "updated_at"])
    audit.log("variant.deleted", actor=actor, request=request, obj=variant, sku=variant.sku)


# ---------------------------------------------------------------- products


@transaction.atomic
def create_product(*, actor, data, variants, request=None):
    skus = [v["sku"].strip().lower() for v in variants]
    if len(skus) != len(set(skus)):
        raise ServiceError("sku_taken", "Duplicate SKU in the request.", fields={"variants": ["Duplicate SKU."]})
    product = Product(created_by=actor, **data)
    product.slug = unique_slug(Product, data["name"])
    product.save()
    for v in variants:
        v = dict(v)
        prices = v.pop("prices", None)
        add_variant(actor=actor, product=product, data=v, prices=prices, request=request)
    audit.log("product.created", actor=actor, request=request, obj=product, name=product.name)
    return product


@transaction.atomic
def update_product(*, actor, product, data, request=None):
    if product.deleted_at:
        raise ServiceError("product_deleted", "This product was deleted.")
    if data.get("track_inventory") is False and product.track_inventory:
        if _inventory().has_stock_history(list(product.variants.values_list("pk", flat=True))):
            raise ServiceError("tracking_locked", "Inventory tracking cannot be turned off once there is stock history.")
    for k, v in data.items():
        setattr(product, k, v)
    if "name" in data:
        product.slug = unique_slug(Product, product.name, exclude_pk=product.pk)
    product.save()
    audit.log("product.updated", actor=actor, request=request, obj=product, fields=sorted(data))
    return product


def _live(product):
    if product.deleted_at:
        raise ServiceError("product_deleted", "This product was deleted.")


@transaction.atomic
def publish(*, actor, product, request=None):
    _live(product)
    if not product.variants.filter(deleted_at__isnull=True, is_active=True).exists():
        raise ServiceError("no_variants", "Add at least one active variant before publishing.")
    product.status = ProductStatus.ACTIVE
    product.save(update_fields=["status", "updated_at"])
    audit.log("product.published", actor=actor, request=request, obj=product)
    return product


@transaction.atomic
def archive(*, actor, product, request=None):
    _live(product)
    product.status, product.is_visible_on_store = ProductStatus.ARCHIVED, False
    product.save(update_fields=["status", "is_visible_on_store", "updated_at"])
    audit.log("product.archived", actor=actor, request=request, obj=product)
    return product


@transaction.atomic
def set_visibility(*, actor, product, visible, request=None):
    """Show/hide on the storefront. Customers see it only while status is also `active`."""
    _live(product)
    product.is_visible_on_store = visible
    product.save(update_fields=["is_visible_on_store", "updated_at"])
    audit.log("product.shown" if visible else "product.hidden", actor=actor, request=request, obj=product)
    return product


@transaction.atomic
def delete_product(*, actor, product, request=None):
    _live(product)
    ids = list(product.variants.values_list("pk", flat=True))
    if _inventory().has_stock_history(ids):
        raise ServiceError("product_in_use", "This product has stock history. Archive it instead of deleting.")
    now = timezone.now()
    product.deleted_at, product.status, product.is_visible_on_store = now, ProductStatus.ARCHIVED, False
    product.save(update_fields=["deleted_at", "status", "is_visible_on_store", "updated_at"])
    product.variants.update(deleted_at=now, is_active=False)
    audit.log("product.deleted", actor=actor, request=request, obj=product, name=product.name)


def bulk(*, actor, products, action, request=None):
    fn = {"show": lambda p: set_visibility(actor=actor, product=p, visible=True, request=request),
          "hide": lambda p: set_visibility(actor=actor, product=p, visible=False, request=request),
          "archive": lambda p: archive(actor=actor, product=p, request=request)}[action]
    with transaction.atomic():
        for p in products:
            fn(p)
    return len(products)


# ---------------------------------------------------------------- images


def validate_image(file):
    if file.size > settings.MAX_UPLOAD_IMAGE_BYTES:
        raise ServiceError("image_too_large", f"Images can be at most {settings.MAX_UPLOAD_IMAGE_BYTES // (1024 * 1024)} MB.")
    data = file.read()
    file.seek(0)
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
            fmt = img.format
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ServiceError("invalid_image", "The file is not a valid image.")
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ServiceError("invalid_image", "Only JPEG, PNG and WebP images are allowed.")
    return fmt


@transaction.atomic
def add_image(*, actor, product, file, alt="", variant=None, is_primary=False, request=None):
    _live(product)
    validate_image(file)
    if variant is not None and variant.product_id != product.pk:
        raise ServiceError("variant_mismatch", "The variant belongs to a different product.")
    picture = to_webp(_open_upright(file), f"{product.slug[:40]}-{timezone.now():%H%M%S%f}")
    first = not product.images.exists()
    image = ProductImage.objects.create(
        product=product, variant=variant, image=picture, alt=alt[:200],
        sort_order=product.images.count(), is_primary=is_primary or first,
    )
    if image.is_primary:
        product.images.exclude(pk=image.pk).update(is_primary=False)
    audit.log("product.image_added", actor=actor, request=request, obj=product, image_id=image.pk)
    return image


@transaction.atomic
def delete_image(*, actor, image, request=None):
    product, was_primary = image.product, image.is_primary
    image.image.delete(save=False)
    audit.log("product.image_deleted", actor=actor, request=request, obj=product, image_id=image.pk)
    image.delete()
    if was_primary:
        nxt = product.images.first()
        if nxt:
            nxt.is_primary = True
            nxt.save(update_fields=["is_primary"])
