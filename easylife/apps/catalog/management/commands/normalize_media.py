import io
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import BaseCommand
from django.core.files.storage import default_storage
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.catalog.models import Brand, Category, ProductImage
from apps.storefront.models import StoreAd


class Command(BaseCommand):
    help = "Convert referenced raster media to WebP and remove unreferenced files from MEDIA_ROOT."

    def handle(self, *args, **options):
        converted = 0
        skipped = 0
        referenced = set()

        targets = [
            (Category, "image"),
            (Brand, "logo"),
            (ProductImage, "image"),
            (StoreAd, "image"),
        ]
        for model, field_name in targets:
            for instance in model.objects.all().iterator():
                field = getattr(instance, field_name)
                if not field or not field.name:
                    continue
                old_name = field.name
                source = Path(default_storage.path(old_name))
                if not source.exists():
                    continue
                suffix = source.suffix.lower()
                if suffix == ".webp":
                    referenced.add(old_name.replace("\\", "/"))
                    continue
                if suffix not in {".png", ".jpg", ".jpeg"}:
                    referenced.add(old_name.replace("\\", "/"))
                    skipped += 1
                    continue

                try:
                    with Image.open(source) as image:
                        image = ImageOps.exif_transpose(image)
                        has_alpha = "A" in image.getbands() or "transparency" in image.info
                        image = image.convert("RGBA" if has_alpha else "RGB")
                        output = io.BytesIO()
                        image.save(output, "WEBP", quality=88, method=4)
                except (UnidentifiedImageError, OSError):
                    referenced.add(old_name.replace("\\", "/"))
                    skipped += 1
                    continue

                new_name = f"{old_name.rsplit('.', 1)[0]}.webp"
                if default_storage.exists(new_name):
                    default_storage.delete(new_name)
                field.save(new_name, ContentFile(output.getvalue()), save=False)
                instance.save(update_fields=[field_name])
                if old_name != new_name:
                    default_storage.delete(old_name)
                referenced.add(new_name.replace("\\", "/"))
                converted += 1

        media_root = Path(settings.MEDIA_ROOT)
        removed = 0
        for path in media_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(media_root).as_posix()
            if relative not in referenced:
                path.unlink()
                removed += 1

        self.stdout.write(self.style.SUCCESS(
            f"Media normalized: {converted} converted to WebP, {removed} unused files removed, {skipped} non-raster files kept."
        ))
