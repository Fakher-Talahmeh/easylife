import uuid

from django.core.validators import URLValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("storefront", "0007_favorite")]
    operations = [migrations.CreateModel(
        name="StoreAd",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ("image", models.ImageField(upload_to="store/ads/")),
            ("description", models.TextField(blank=True)),
            ("button_text", models.CharField(blank=True, max_length=80)),
            ("button_url", models.URLField(blank=True, max_length=500, validators=[URLValidator(schemes=["http", "https"])])),
            ("sort_order", models.PositiveIntegerField(default=0)),
            ("is_active", models.BooleanField(default=True)),
        ],
        options={"ordering": ["sort_order", "-created_at"]},
    )]
