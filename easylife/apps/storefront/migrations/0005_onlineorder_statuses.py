from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("storefront", "0004_onlineorder_discount")]
    operations = [migrations.AlterField(
        model_name="onlineorder", name="status",
        field=models.CharField(choices=[("pending", "Pending confirmation"), ("confirmed", "Confirmed"), ("preparing", "Being prepared"), ("ready_for_shipping", "Ready for shipping"), ("out_for_delivery", "Out for delivery"), ("delivered", "Delivered"), ("cancelled", "Cancelled"), ("delivery_failed", "Delivery failed"), ("returned", "Returned")], db_index=True, default="pending", max_length=20),
    )]
