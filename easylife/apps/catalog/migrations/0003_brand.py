"""Brands become a managed list: Product.brand (free text) -> Product.brand (FK to Brand).

Existing brand names are kept: one Brand row per distinct name (case-insensitive), then products are linked.
"""

import uuid

import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models


def texts_to_brands(apps, schema_editor):
    Brand = apps.get_model("catalog", "Brand")
    Product = apps.get_model("catalog", "Product")
    by_key = {}
    for product in Product.objects.exclude(brand="").only("id", "brand"):
        name = product.brand.strip()
        if not name:
            continue
        key = name.lower()
        if key not in by_key:
            by_key[key] = Brand.objects.create(name=name[:120])
        Product.objects.filter(pk=product.pk).update(brand_fk=by_key[key])


def brands_to_texts(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    for product in Product.objects.exclude(brand_fk=None).select_related("brand_fk"):
        Product.objects.filter(pk=product.pk).update(brand=product.brand_fk.name[:100])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_remove_product_product_storefront_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Brand",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddConstraint(
            model_name="brand",
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower("name"), name="brand_name_ci_unique"),
        ),
        migrations.AddField(
            model_name="product",
            name="brand_fk",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="products",
                to="catalog.brand",
            ),
        ),
        migrations.RunPython(texts_to_brands, brands_to_texts),
        migrations.RemoveField(model_name="product", name="brand"),
        migrations.RenameField(model_name="product", old_name="brand_fk", new_name="brand"),
    ]
