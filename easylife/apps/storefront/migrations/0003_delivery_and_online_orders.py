from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid

class Migration(migrations.Migration):
    dependencies = [("storefront", "0002_coordinate_validators"), (settings.AUTH_USER_MODEL.split(".")[0], "0001_initial")]
    operations = [
        migrations.CreateModel(
            name="DeliveryArea", fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, unique=True)), ("fee", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("is_active", models.BooleanField(default=True)),
            ], options={"ordering": ["name"]},
        ),
        migrations.AddConstraint(model_name="deliveryarea", constraint=models.CheckConstraint(condition=models.Q(("fee__gte", 0)), name="delivery_area_fee_gte0")),
        migrations.CreateModel(
            name="OnlineOrder", fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("number", models.CharField(max_length=32, unique=True)), ("confirmation_token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("status", models.CharField(choices=[("pending", "Pending confirmation"), ("confirmed", "Confirmed")], db_index=True, default="pending", max_length=12)),
                ("customer_name", models.CharField(max_length=120)), ("phone", models.CharField(max_length=30)), ("city", models.CharField(max_length=120)), ("address", models.CharField(max_length=300)),
                ("lines", models.JSONField(default=list)), ("subtotal", models.DecimalField(decimal_places=2, max_digits=18)), ("delivery_fee", models.DecimalField(decimal_places=2, max_digits=18)), ("total", models.DecimalField(decimal_places=2, max_digits=18)),
                ("area", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="orders", to="storefront.deliveryarea")),
                ("customer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="online_orders", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ["-created_at"]},
        ),
    ]
