from decimal import Decimal
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("storefront", "0003_delivery_and_online_orders")]
    operations = [migrations.AddField(model_name="onlineorder", name="discount_total", field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=18))]
