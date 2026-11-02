from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    dependencies = [("storefront", "0008_storead")]
    operations = [
        migrations.AddField(model_name="storead", name="starts_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="storead", name="ends_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddConstraint(
            model_name="storead",
            constraint=models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(starts_at__isnull=True) | Q(ends_at__gt=F("starts_at")), name="store_ad_end_after_start"),
        ),
    ]
