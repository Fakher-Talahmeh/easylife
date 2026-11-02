from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files import File
from django.core.management import BaseCommand, call_command
from django.db import transaction

from apps.catalog.models import Category, Offer, OfferItem, OfferType, PriceList, Product, ProductImage, ProductPrice, ProductStatus, ProductVariant
from apps.currencies.models import Currency
from apps.inventory import services as inventory
from apps.inventory.models import MovementType, StockLevel, Warehouse


DEMO_PREFIX = "demo-"


def demo_asset(name):
    """Prefer the optimized WebP demo asset while keeping the seed readable by slug/name."""
    path = Path(settings.BASE_DIR) / "demo_assets" / "products" / name
    webp = path.with_suffix(".webp")
    return webp if webp.exists() else path


def category_svg(label, color):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600">
  <rect width="600" height="600" rx="56" fill="{color}"/>
  <circle cx="300" cy="250" r="130" fill="#ffffff" fill-opacity=".85"/>
  <text x="300" y="450" text-anchor="middle" font-family="Arial, sans-serif" font-size="42" font-weight="700" fill="#17324d">{label}</text>
</svg>'''.encode()


CATEGORIES = [
    {"slug": "demo-kitchen", "name": "مستلزمات المطبخ", "name_en": "Kitchen supplies", "name_he": "מוצרי מטבח", "parent": None, "label": "Kitchen", "color": "#d9edf0", "image_asset": "plastic-cups.png"},
    {"slug": "demo-cups", "name": "الأكواب والضيافة", "name_en": "Cups and hospitality", "name_he": "כוסות ואירוח", "parent": None, "label": "Cups", "color": "#e7f1df", "image_asset": "coffee-cups.png"},
    {"slug": "demo-paper-cups", "name": "كاسات كرتون", "name_en": "Paper cups", "name_he": "כוסות קרטון", "parent": "demo-cups", "label": "Paper", "color": "#f8e5d4", "image_asset": "plastic-cups-white.png"},
    {"slug": "demo-glass-cups", "name": "كاسات زجاج", "name_en": "Glass cups", "name_he": "כוסות זכוכית", "parent": "demo-cups", "label": "Glass", "color": "#dce7f6", "image_asset": "coffee-cups.png"},
    {"slug": "demo-cleaning", "name": "مواد التنظيف", "name_en": "Cleaning products", "name_he": "חומרי ניקוי", "parent": None, "label": "Cleaning", "color": "#e8def5", "image_asset": "plastic-cups.png"},
    {"slug": "demo-surface-cleaners", "name": "منظفات الأسطح", "name_en": "Surface cleaners", "name_he": "חומרי ניקוי משטחים", "parent": "demo-cleaning", "label": "Surface", "color": "#f6e5ec", "image_asset": "plastic-cups.png"},
    {"slug": "demo-disposable", "name": "المستلزمات أحادية الاستخدام", "name_en": "Disposable supplies", "name_he": "מוצרים חד פעמיים", "parent": None, "label": "Plastic", "color": "#dcebf3", "image_asset": "plastic-cups-transparent.png"},
    {"slug": "demo-cutlery", "name": "الملاعق والشوك", "name_en": "Spoons and forks", "name_he": "כפות ומזלגות", "parent": "demo-disposable", "label": "Cutlery", "color": "#e8f0f4", "image_asset": "forks-transparent.png"},
    {"slug": "demo-bagasse", "name": "صحون قصب السكر", "name_en": "Sugarcane plates", "name_he": "צלחות קנה סוכר", "parent": "demo-disposable", "label": "Bagasse", "color": "#eef0df", "image_asset": "bagasse-plates.png"},
    {"slug": "demo-foil", "name": "القصدير وعلب الطعام", "name_en": "Foil and food containers", "name_he": "אלומיניום וקופסאות אוכל", "parent": None, "label": "Foil", "color": "#e8e9ec", "image_asset": "foil-round-container.png"},
    {"slug": "demo-foil-plates", "name": "صحون وصواني قصدير", "name_en": "Foil plates and trays", "name_he": "צלחות ומגשי אלומיניום", "parent": "demo-foil", "label": "Plates", "color": "#edf0f2", "image_asset": "foil-round-plates.png"},
    {"slug": "demo-foil-containers", "name": "علب قصدير مع أغطية", "name_en": "Foil containers with lids", "name_he": "קופסאות אלומיניום עם מכסים", "parent": "demo-foil", "label": "Containers", "color": "#e5e6e8", "image_asset": "foil-round-container.png"},
    {"slug": "demo-garden", "name": "الزراعة والديكور", "name_en": "Garden and decor", "name_he": "גינון ועיצוב", "parent": None, "label": "Garden", "color": "#e4f0df", "image_asset": "artificial-monstera.png"},
    {"slug": "demo-pots", "name": "قواوير وأحواض الزراعة", "name_en": "Plant pots and planters", "name_he": "עציצים ואדניות", "parent": "demo-garden", "label": "Pots", "color": "#f0e5d8", "image_asset": "pot-round-terracotta.png"},
    {"slug": "demo-artificial-plants", "name": "النباتات الصناعية", "name_en": "Artificial plants", "name_he": "צמחים מלאכותיים", "parent": "demo-garden", "label": "Plants", "color": "#e2eedc", "image_asset": "artificial-monstera.png"},
    {"slug": "demo-chairs", "name": "الكراسي البلاستيك", "name_en": "Plastic chairs", "name_he": "כיסאות פלסטיק", "parent": None, "label": "Chairs", "color": "#eee8df", "image_asset": "plastic-chair-white.png"},
]

PRODUCTS = [
    {"slug": "demo-paper-cups-20", "name": "كاسات كرتون 20 قطعة", "name_en": "Paper cups · 20 pcs", "category": "demo-paper-cups", "price": "12.00", "compare": "15.00", "stock": 40},
    {"slug": "demo-paper-cups-50", "name": "كاسات كرتون 50 قطعة", "name_en": "Paper cups · 50 pcs", "category": "demo-paper-cups", "price": "25.00", "compare": None, "stock": 30},
    {"slug": "demo-glass-cups-set", "name": "طقم كاسات زجاج", "name_en": "Glass cups set", "category": "demo-glass-cups", "price": "38.00", "compare": "45.00", "stock": 18},
    {"slug": "demo-glass-cups-tall", "name": "كاسات زجاج طويلة", "name_en": "Tall glass cups", "category": "demo-glass-cups", "price": "32.00", "compare": None, "stock": 22},
    {"slug": "demo-kitchen-towels", "name": "مناديل مطبخ", "name_en": "Kitchen towels", "category": "demo-kitchen", "price": "9.00", "compare": None, "stock": 50},
    {"slug": "demo-surface-spray", "name": "بخاخ تنظيف الأسطح", "name_en": "Surface cleaner spray", "category": "demo-surface-cleaners", "price": "16.00", "compare": "19.00", "stock": 35},
    {"slug": "demo-plastic-cups", "name": "كاسات بلاستيك", "name_en": "Plastic cups", "name_he": "כוסות פלסטיק", "description": "كاسات بلاستيك شفافة للاستخدام اليومي والضيافة.", "description_en": "Clear disposable plastic cups for everyday use and hospitality.", "description_he": "כוסות פלסטיק שקופות לשימוש יומיומי ולאירוח.", "category": "demo-disposable", "price": "10.00", "compare": "12.00", "stock": 80, "image": "plastic-cups.png"},
    {"slug": "demo-coffee-cups", "name": "فناجين قهوة", "name_en": "Coffee cups", "name_he": "כוסות קפה", "description": "فناجين قهوة أنيقة للضيافة والمناسبات.", "description_en": "Elegant coffee cups for hospitality and special occasions.", "description_he": "כוסות קפה אלגנטיות לאירוח ולאירועים.", "category": "demo-cups", "price": "24.00", "compare": "29.00", "stock": 35, "image": "coffee-cups.png"},
    {"slug": "demo-foil-plates", "name": "صحون قصدير", "name_en": "Aluminum foil plates", "name_he": "צלחות אלומיניום", "description": "صحون قصدير دائرية ومستطيلة لتقديم الطعام.", "description_en": "Round and rectangular aluminum foil plates for serving food.", "description_he": "צלחות אלומיניום עגולות ומלבניות להגשת אוכל.", "category": "demo-foil", "price": "18.00", "compare": None, "stock": 45, "image": "foil-plates.png"},
    {"slug": "demo-bagasse-plates", "name": "صحون قصب سكر - كرتون", "name_en": "Sugarcane bagasse plates", "name_he": "צלחות קנה סוכר", "description": "صحون صديقة للبيئة من ألياف قصب السكر.", "description_en": "Eco-friendly plates made from sugarcane bagasse fiber.", "description_he": "צלחות ידידותיות לסביבה מסיבי קנה סוכר.", "category": "demo-disposable", "price": "22.00", "compare": "26.00", "stock": 55, "image": "bagasse-plates.png"},
    {"slug": "demo-plant-pots", "name": "قواوير زراعة", "name_en": "Plant pots", "name_he": "עציצים", "description": "قواوير زراعة بلاستيك بأحجام وألوان متعددة.", "description_en": "Plastic plant pots in assorted sizes and colors.", "description_he": "עציצי פלסטיק במגוון גדלים וצבעים.", "category": "demo-garden", "price": "15.00", "compare": None, "stock": 28, "image": "plant-pots.png"},
    {"slug": "demo-artificial-plants", "name": "شجر صناعي بأشكاله", "name_en": "Artificial plants and trees", "name_he": "צמחים ועצים מלאכותיים", "description": "نباتات وأشجار صناعية للبيت والمكتب والديكور.", "description_en": "Artificial plants and trees for home, office and decor.", "description_he": "צמחים ועצים מלאכותיים לבית, למשרד ולעיצוב.", "category": "demo-garden", "price": "65.00", "compare": "75.00", "stock": 16, "image": "artificial-plants.png"},
    {"slug": "demo-plastic-cutlery", "name": "معالق وشوك بلاستيك", "name_en": "Plastic spoons and forks", "name_he": "כפות ומזלגות פלסטיק", "description": "معالق وشوك بلاستيك للرحلات والمناسبات.", "description_en": "Disposable plastic spoons and forks for events and takeaways.", "description_he": "כפות ומזלגות פלסטיק חד פעמיים לאירועים ולטייק אוויי.", "category": "demo-disposable", "price": "9.00", "compare": None, "stock": 70, "image": "plastic-cutlery.png"},
    {"slug": "demo-foil-containers", "name": "قصدير: صحون وصواني ومعلبات مع غطا", "name_en": "Foil trays and containers with lids", "name_he": "מגשי וקופסאות אלומיניום עם מכסים", "description": "علب وصواني قصدير بأشكال مختلفة مع أغطية.", "description_en": "Aluminum foil trays and containers in different shapes with lids.", "description_he": "מגשי וקופסאות אלומיניום במגוון צורות עם מכסים.", "category": "demo-foil", "price": "28.00", "compare": "32.00", "stock": 42, "image": "foil-containers.png"},
    {"slug": "demo-plastic-chairs", "name": "كراسي بلاستيك", "name_en": "Plastic chairs", "name_he": "כיסאות פלסטיק", "description": "كراسي بلاستيك متينة للاستخدام المنزلي والمناسبات.", "description_en": "Durable plastic chairs for homes, events and gatherings.", "description_he": "כיסאות פלסטיק עמידים לבית, לאירועים ולמפגשים.", "category": "demo-chairs", "price": "85.00", "compare": "95.00", "stock": 20, "image": "plastic-chairs.png"},
    {"slug": "demo-forks-transparent", "name": "شوك بلاستيك شفافة", "name_en": "Transparent plastic forks", "name_he": "מזלגות פלסטיק שקופים", "description": "شوك بلاستيك شفافة للاستخدام الواحد.", "description_en": "Clear disposable plastic forks.", "description_he": "מזלגות פלסטיק שקופים חד פעמיים.", "category": "demo-disposable", "price": "5.00", "compare": None, "stock": 65, "image": "forks-transparent.png"},
    {"slug": "demo-forks-white", "name": "شوك بلاستيك بيضاء", "name_en": "White plastic forks", "name_he": "מזלגות פלסטיק לבנים", "description": "شوك بلاستيك بيضاء للمناسبات والضيافة.", "description_en": "White disposable plastic forks for events and hospitality.", "description_he": "מזלגות פלסטיק לבנים לאירוח ולאירועים.", "category": "demo-disposable", "price": "5.00", "compare": None, "stock": 65, "image": "forks-white.png"},
    {"slug": "demo-spoons-transparent", "name": "ملاعق بلاستيك شفافة", "name_en": "Transparent plastic spoons", "name_he": "כפות פלסטיק שקופות", "description": "ملاعق بلاستيك شفافة للاستخدام الواحد.", "description_en": "Clear disposable plastic spoons.", "description_he": "כפות פלסטיק שקופות חד פעמיות.", "category": "demo-disposable", "price": "5.00", "compare": None, "stock": 65, "image": "spoons-transparent.png"},
    {"slug": "demo-spoons-white", "name": "ملاعق بلاستيك بيضاء", "name_en": "White plastic spoons", "name_he": "כפות פלסטיק לבנות", "description": "ملاعق بلاستيك بيضاء للمناسبات والضيافة.", "description_en": "White disposable plastic spoons for events and hospitality.", "description_he": "כפות פלסטיק לבנות לאירוח ולאירועים.", "category": "demo-disposable", "price": "5.00", "compare": None, "stock": 65, "image": "spoons-white.png"},
    {"slug": "demo-foil-round-plates", "name": "صحون قصدير دائرية", "name_en": "Round foil plates", "name_he": "צלחות אלומיניום עגולות", "category": "demo-foil", "price": "16.00", "compare": None, "stock": 40, "image": "foil-round-plates.png"},
    {"slug": "demo-foil-rect-plates", "name": "صحون قصدير مستطيلة", "name_en": "Rectangular foil plates", "name_he": "צלחות אלומיניום מלבניות", "category": "demo-foil", "price": "18.00", "compare": None, "stock": 40, "image": "foil-rect-plates.png"},
    {"slug": "demo-bagasse-round", "name": "صحون قصب سكر دائرية", "name_en": "Round sugarcane plates", "name_he": "צלחות קנה סוכר עגולות", "category": "demo-disposable", "price": "20.00", "compare": None, "stock": 45, "image": "bagasse-plates.png"},
    {"slug": "demo-bagasse-compartment", "name": "صحون قصب سكر مقسمة", "name_en": "Compartment sugarcane plates", "name_he": "צלחות קנה סוכר מחולקות", "category": "demo-disposable", "price": "24.00", "compare": None, "stock": 45, "image": "bagasse-plates.png"},
    {"slug": "demo-pots-small", "name": "قواوير زراعة صغيرة", "name_en": "Small plant pots", "name_he": "עציצים קטנים", "category": "demo-garden", "price": "8.00", "compare": None, "stock": 30, "image": "pot-round-white.png"},
    {"slug": "demo-pots-large", "name": "قواوير زراعة كبيرة", "name_en": "Large plant pots", "name_he": "עציצים גדולים", "category": "demo-garden", "price": "24.00", "compare": None, "stock": 20, "image": "pot-round-terracotta.png"},
    {"slug": "demo-artificial-tree", "name": "شجر صناعي طويل", "name_en": "Tall artificial tree", "name_he": "עץ מלאכותי גבוה", "category": "demo-garden", "price": "90.00", "compare": None, "stock": 10, "image": "artificial-olive.png"},
    {"slug": "demo-foil-round-container", "name": "علبة قصدير دائرية مع غطا", "name_en": "Round foil container with lid", "name_he": "קופסת אלומיניום עגולה עם מכסה", "category": "demo-foil", "price": "20.00", "compare": None, "stock": 35, "image": "foil-round-container.png"},
    {"slug": "demo-foil-rect-container", "name": "علبة قصدير مستطيلة مع غطا", "name_en": "Rectangular foil container with lid", "name_he": "קופסת אלומיניום מלבנית עם מכסה", "category": "demo-foil", "price": "25.00", "compare": None, "stock": 35, "image": "foil-rect-container.png"},
    {"slug": "demo-chair-white", "name": "كرسي بلاستيك أبيض", "name_en": "White plastic chair", "name_he": "כיסא פלסטיק לבן", "category": "demo-chairs", "price": "80.00", "compare": None, "stock": 12, "image": "plastic-chairs.png"},
    {"slug": "demo-chair-black", "name": "كرسي بلاستيك أسود", "name_en": "Black plastic chair", "name_he": "כיסא פלסטיק שחור", "category": "demo-chairs", "price": "80.00", "compare": None, "stock": 12, "image": "plastic-chairs.png"},
    # Extra demo products: each visible shape/colour from the reference images gets its own product.
    {"slug": "demo-pot-round-terracotta", "name": "قصرية زراعة دائرية فخاري", "name_en": "Round terracotta plant pot", "name_he": "עציץ עגול בצבע טרקוטה", "category": "demo-garden", "price": "18.00", "compare": None, "stock": 18, "image": "pot-round-terracotta.png"},
    {"slug": "demo-pot-round-green", "name": "قصرية زراعة دائرية خضراء", "name_en": "Round green plant pot", "name_he": "עציץ עגול ירוק", "category": "demo-garden", "price": "18.00", "compare": None, "stock": 18, "image": "pot-round-green.png"},
    {"slug": "demo-pot-round-white", "name": "قصرية زراعة دائرية بيضاء", "name_en": "Round white plant pot", "name_he": "עציץ עגול לבן", "category": "demo-garden", "price": "20.00", "compare": None, "stock": 16, "image": "pot-round-white.png"},
    {"slug": "demo-pot-square-green", "name": "قصرية زراعة مربعة خضراء", "name_en": "Green square plant pot", "name_he": "עציץ מרובע ירוק", "category": "demo-garden", "price": "22.00", "compare": None, "stock": 14, "image": "pot-square-green.png"},
    {"slug": "demo-pot-rectangular-terracotta", "name": "حوض زراعة مستطيل فخاري", "name_en": "Rectangular terracotta planter", "name_he": "אדנית מלבנית בצבע טרקוטה", "category": "demo-garden", "price": "28.00", "compare": None, "stock": 12, "image": "pot-rectangular-terracotta.png"},
    {"slug": "demo-pot-ribbed-green", "name": "قصرية زراعة مخططة خضراء", "name_en": "Green ribbed plant pot", "name_he": "עציץ ירוק מחורץ", "category": "demo-garden", "price": "24.00", "compare": None, "stock": 12, "image": "pot-ribbed-green.png"},
    {"slug": "demo-artificial-olive", "name": "شجرة زيتون صناعية", "name_en": "Artificial olive tree", "name_he": "עץ זית מלאכותי", "category": "demo-garden", "price": "95.00", "compare": None, "stock": 8, "image": "artificial-olive.png"},
    {"slug": "demo-artificial-fiddle-leaf", "name": "شجرة فيكس صناعية", "name_en": "Artificial fiddle leaf tree", "name_he": "פיקוס כינורי מלאכותי", "category": "demo-garden", "price": "110.00", "compare": None, "stock": 8, "image": "artificial-fiddle-leaf.png"},
    {"slug": "demo-artificial-monstera", "name": "نبتة مونستيرا صناعية", "name_en": "Artificial monstera plant", "name_he": "מונסטרה מלאכותית", "category": "demo-garden", "price": "75.00", "compare": None, "stock": 10, "image": "artificial-monstera.png"},
    {"slug": "demo-artificial-palm", "name": "نخلة صناعية", "name_en": "Artificial palm plant", "name_he": "דקל מלאכותי", "category": "demo-garden", "price": "85.00", "compare": None, "stock": 10, "image": "artificial-palm.png"},
    {"slug": "demo-artificial-snake", "name": "نبتة سانسيفيريا صناعية", "name_en": "Artificial snake plant", "name_he": "סנסיווריה מלאכותית", "category": "demo-garden", "price": "58.00", "compare": None, "stock": 14, "image": "artificial-snake.png"},
    {"slug": "demo-artificial-eucalyptus", "name": "نبتة أوكالبتوس صناعية", "name_en": "Artificial eucalyptus plant", "name_he": "אקליפטוס מלאכותי", "category": "demo-garden", "price": "52.00", "compare": None, "stock": 14, "image": "artificial-eucalyptus.png"},
    {"slug": "demo-artificial-pothos", "name": "نبتة بوتس صناعية متدلية", "name_en": "Artificial hanging pothos", "name_he": "פוטוס מלאכותי נשפך", "category": "demo-garden", "price": "48.00", "compare": None, "stock": 14, "image": "artificial-pothos.png"},
    {"slug": "demo-artificial-succulent", "name": "صبار صناعي صغير", "name_en": "Artificial succulent", "name_he": "סוקולנט מלאכותי", "category": "demo-garden", "price": "35.00", "compare": None, "stock": 18, "image": "artificial-succulent.png"},
    {"slug": "demo-foil-deep-round-plates", "name": "صحون قصدير عميقة دائرية", "name_en": "Deep round foil plates", "name_he": "צלחות אלומיניום עגולות עמוקות", "category": "demo-foil", "price": "19.00", "compare": None, "stock": 38, "image": "foil-deep-round-plates.png"},
    {"slug": "demo-foil-square-plates", "name": "صحون قصدير مربعة", "name_en": "Square foil plates", "name_he": "צלחות אלומיניום מרובעות", "category": "demo-foil", "price": "19.00", "compare": None, "stock": 38, "image": "foil-square-plates.png"},
    {"slug": "demo-foil-small-round-container", "name": "علبة قصدير صغيرة دائرية مع غطا", "name_en": "Small round foil container with lid", "name_he": "קופסת אלומיניום עגולה קטנה עם מכסה", "category": "demo-foil", "price": "14.00", "compare": None, "stock": 40, "image": "foil-small-round-container.png"},
    {"slug": "demo-foil-square-container", "name": "علبة قصدير مربعة مع غطا", "name_en": "Square foil container with lid", "name_he": "קופסת אלומיניום מרובעת עם מכסה", "category": "demo-foil", "price": "22.00", "compare": None, "stock": 35, "image": "foil-square-container.png"},
    {"slug": "demo-foil-deep-round-container", "name": "علبة قصدير عميقة دائرية مع غطا", "name_en": "Deep round foil container with lid", "name_he": "קופסת אלומיניום עגולה עמוקה עם מכסה", "category": "demo-foil", "price": "24.00", "compare": None, "stock": 35, "image": "foil-deep-round-container.png"},
    {"slug": "demo-chair-beige", "name": "كرسي بلاستيك بيج", "name_en": "Beige plastic chair", "name_he": "כיסא פלסטיק בז׳", "category": "demo-chairs", "price": "82.00", "compare": None, "stock": 12, "image": "plastic-chairs.png"},
    {"slug": "demo-cups-transparent", "name": "كاسات بلاستيك شفافة", "name_en": "Transparent plastic cups", "name_he": "כוסות פלסטיק שקופות", "category": "demo-disposable", "price": "10.00", "compare": None, "stock": 40, "image": "plastic-cups.png"},
    {"slug": "demo-cups-white", "name": "كاسات بلاستيك بيضاء", "name_en": "White plastic cups", "name_he": "כוסות פלסטיק לבנות", "category": "demo-disposable", "price": "10.00", "compare": None, "stock": 40, "image": "plastic-cups.png"},
]

LEGACY_COMBINED_SLUGS = {
    "demo-plastic-cutlery", "demo-plastic-cups", "demo-foil-plates", "demo-bagasse-plates",
    "demo-plant-pots", "demo-artificial-plants", "demo-foil-containers", "demo-plastic-chairs",
}

DEMO_IMAGE_OVERRIDES = {
    "demo-chair-white": "plastic-chair-white.png",
    "demo-chair-black": "plastic-chair-black.png",
    "demo-chair-beige": "plastic-chair-beige.png",
    "demo-cups-transparent": "plastic-cups-transparent.png",
    "demo-cups-white": "plastic-cups-white.png",
}

DEMO_CATEGORY_OVERRIDES = {
    "demo-forks-transparent": "demo-cutlery", "demo-forks-white": "demo-cutlery",
    "demo-spoons-transparent": "demo-cutlery", "demo-spoons-white": "demo-cutlery",
    "demo-bagasse-plates": "demo-bagasse", "demo-bagasse-round": "demo-bagasse", "demo-bagasse-compartment": "demo-bagasse",
    "demo-foil-plates": "demo-foil-plates", "demo-foil-round-plates": "demo-foil-plates", "demo-foil-rect-plates": "demo-foil-plates",
    "demo-foil-deep-round-plates": "demo-foil-plates", "demo-foil-square-plates": "demo-foil-plates",
    "demo-foil-containers": "demo-foil-containers", "demo-foil-round-container": "demo-foil-containers", "demo-foil-rect-container": "demo-foil-containers",
    "demo-foil-small-round-container": "demo-foil-containers", "demo-foil-square-container": "demo-foil-containers", "demo-foil-deep-round-container": "demo-foil-containers",
    "demo-plant-pots": "demo-pots", "demo-pots-small": "demo-pots", "demo-pots-large": "demo-pots",
    "demo-pot-round-terracotta": "demo-pots", "demo-pot-round-green": "demo-pots", "demo-pot-round-white": "demo-pots",
    "demo-pot-square-green": "demo-pots", "demo-pot-rectangular-terracotta": "demo-pots", "demo-pot-ribbed-green": "demo-pots",
    "demo-artificial-plants": "demo-artificial-plants", "demo-artificial-tree": "demo-artificial-plants", "demo-artificial-olive": "demo-artificial-plants",
    "demo-artificial-fiddle-leaf": "demo-artificial-plants", "demo-artificial-monstera": "demo-artificial-plants", "demo-artificial-palm": "demo-artificial-plants",
    "demo-artificial-snake": "demo-artificial-plants", "demo-artificial-eucalyptus": "demo-artificial-plants", "demo-artificial-pothos": "demo-artificial-plants", "demo-artificial-succulent": "demo-artificial-plants",
}

OFFERS = [
    {"name": "عرض مناديل المطبخ", "name_en": "Kitchen towels offer", "name_he": "מבצע מגבות מטבח", "type": OfferType.PRODUCT_PRICE, "price": "7.00", "items": [("demo-kitchen-towels", 1)]},
    {"name": "3 كاسات كرتون بسعر خاص", "name_en": "3 paper cups packs special price", "name_he": "3 מארזי כוסות קרטון במחיר מיוחד", "type": OfferType.QUANTITY_PRICE, "price": "30.00", "items": [("demo-paper-cups-20", 3)]},
    {"name": "بكج الضيافة والتنظيف", "name_en": "Hospitality and cleaning bundle", "name_he": "מארז אירוח וניקיון", "type": OfferType.BUNDLE, "price": "35.00", "items": [("demo-paper-cups-50", 1), ("demo-surface-spray", 1)]},
    {"name": "بكج الضيافة البلاستيكي", "name_en": "Plastic hospitality bundle", "name_he": "מארז אירוח מפלסטיק", "type": OfferType.BUNDLE, "price": "42.00", "items": [("demo-cups-transparent", 1), ("demo-spoons-transparent", 1)]},
]

DEMO_COLORS = {
    "demo-plastic-cups": ["شفاف", "أبيض"],
    "demo-coffee-cups": ["أبيض", "كريمي"],
    "demo-plastic-chairs": ["أبيض", "أسود", "بيج"],
}


class Command(BaseCommand):
    help = "Seed a small, repeatable demo catalog with nested categories, images, products, offers and stock."

    @transaction.atomic
    def handle(self, *args, **options):
        call_command("seed_currencies", verbosity=0)
        currency = Currency.objects.get(code="ILS")
        price_list = PriceList.objects.filter(currency=currency, is_default=True, is_active=True).first()
        if price_list is None:
            price_list = PriceList.objects.filter(currency=currency, is_default=True).first()
        if price_list is None:
            price_list = PriceList.objects.create(name="Retail ILS", currency=currency, is_default=True, is_active=True)
        elif not price_list.is_active:
            price_list.is_active = True
            price_list.save(update_fields=["is_active"])
        warehouse = Warehouse.objects.filter(is_active=True).order_by("-is_default", "code").first()
        if warehouse is None:
            warehouse = Warehouse.objects.create(code="DEMO", name="Demo warehouse", is_active=True)

        categories = {}
        # The old combined cutlery demo item is replaced by four separately filterable products below.
        Product.objects.filter(slug__in=LEGACY_COMBINED_SLUGS).update(status=ProductStatus.ARCHIVED, is_visible_on_store=False)
        for row in CATEGORIES:
            parent = categories.get(row["parent"]) if row["parent"] else None
            category, _ = Category.objects.update_or_create(
                slug=row["slug"],
                defaults={
                    "name": row["name"], "name_ar": row.get("name_ar", row["name"]), "name_en": row["name_en"], "name_he": row.get("name_he", ""), "parent": parent,
                    "is_active": True, "sort_order": 0,
                },
            )
            asset_name = row.get("image_asset")
            asset = demo_asset(asset_name) if asset_name else None
            stored_name = f"demo/categories/{row['slug']}-{asset.name}" if asset and asset.exists() else f"{DEMO_PREFIX}{category.slug}.svg"
            if category.image.name != stored_name:
                if asset and asset.exists():
                    with asset.open("rb") as handle:
                        category.image.save(stored_name, File(handle), save=False)
                else:
                    category.image.save(stored_name, ContentFile(category_svg(row["label"], row["color"])), save=False)
                category.save(update_fields=["image"])
            categories[row["slug"]] = category

        for row in PRODUCTS:
            product, _ = Product.objects.update_or_create(
                slug=row["slug"],
                defaults={
                    "name": row["name"], "name_ar": row.get("name_ar", row["name"]), "name_en": row["name_en"], "name_he": row.get("name_he", ""),
                    "description": row.get("description", ""), "description_ar": row.get("description", ""), "description_en": row.get("description_en", ""), "description_he": row.get("description_he", ""),
                    "category": categories[DEMO_CATEGORY_OVERRIDES.get(row["slug"], row["category"])],
                    "status": ProductStatus.ACTIVE, "is_visible_on_store": True, "track_inventory": True,
                },
            )
            variant, _ = ProductVariant.objects.get_or_create(
                sku=f"{row['slug'].upper()}-1", defaults={"product": product, "name": "القياسي"},
            )
            if variant.product_id != product.pk:
                variant.product = product
                variant.save(update_fields=["product"])
            colors = DEMO_COLORS.get(row["slug"], [])
            if colors:
                variant.name = colors[0]
                variant.attributes = {"color": colors[0]}
                variant.save(update_fields=["name", "attributes"])
            ProductPrice.objects.update_or_create(
                variant=variant, price_list=price_list,
                defaults={"price": Decimal(row["price"]), "compare_at_price": row["compare"]},
            )
            image_name = DEMO_IMAGE_OVERRIDES.get(row["slug"], row.get("image"))
            if image_name:
                image = ProductImage.objects.filter(product=product, is_primary=True).first()
                if image is None:
                    image = ProductImage(product=product, is_primary=True)
                asset = demo_asset(image_name)
                stored_name = f"demo/products/{row['slug']}-{asset.name}"
                if asset.exists() and image.image.name != stored_name:
                    with asset.open("rb") as handle:
                        image.image.save(stored_name, File(handle), save=False)
                    image.alt = row["name"]
                    image.save()
            elif not ProductImage.objects.filter(product=product).exists():
                image = ProductImage(product=product, alt=row["name"], is_primary=True)
                image.image.save(
                    f"{DEMO_PREFIX}{row['slug']}.svg",
                    ContentFile(category_svg(row["name_en"], "#eef4f7")),
                    save=True,
                )
            level = StockLevel.objects.get_or_create(variant=variant, warehouse=warehouse)[0]
            missing = Decimal(str(row["stock"])) - level.available
            if missing > 0:
                inventory.move_stock(
                    variant=variant, warehouse=warehouse, quantity=missing,
                    type=MovementType.PURCHASE_IN, unit_cost=Decimal(row["price"]),
                    note="Demo catalog seed",
                )
            for index, color in enumerate(colors[1:], start=2):
                color_variant, _ = ProductVariant.objects.get_or_create(
                    sku=f"{row['slug'].upper()}-{index}",
                    defaults={"product": product, "name": color, "attributes": {"color": color}},
                )
                color_variant.name = color
                color_variant.attributes = {"color": color}
                color_variant.product = product
                color_variant.save(update_fields=["product", "name", "attributes"])
                ProductPrice.objects.update_or_create(
                    variant=color_variant, price_list=price_list,
                    defaults={"price": Decimal(row["price"]), "compare_at_price": row["compare"]},
                )
                color_level = StockLevel.objects.get_or_create(variant=color_variant, warehouse=warehouse)[0]
                color_missing = Decimal(str(row["stock"])) - color_level.available
                if color_missing > 0:
                    inventory.move_stock(
                        variant=color_variant, warehouse=warehouse, quantity=color_missing,
                        type=MovementType.PURCHASE_IN, unit_cost=Decimal(row["price"]),
                        note="Demo catalog color seed",
                    )

        # Keep the original grouped demo records for history, but show only the split records in the storefront.
        Product.objects.filter(slug__in=LEGACY_COMBINED_SLUGS).update(status=ProductStatus.ARCHIVED, is_visible_on_store=False)

        for row in OFFERS:
            offer, _ = Offer.objects.update_or_create(
                name=row["name"], price_list=price_list,
                defaults={"type": row["type"], "fixed_price": Decimal(row["price"]), "name_ar": row["name"], "name_en": row.get("name_en", ""), "name_he": row.get("name_he", ""), "is_active": True},
            )
            offer.items.all().delete()
            OfferItem.objects.bulk_create([
                OfferItem(
                    offer=offer,
                    variant=ProductVariant.objects.get(product__slug=slug, sku=f"{slug.upper()}-1"),
                    quantity=quantity,
                )
                for slug, quantity in row["items"]
            ])

        self.stdout.write(self.style.SUCCESS(
            f"Demo store ready: {len(CATEGORIES)} categories, {len(PRODUCTS)} products and {len(OFFERS)} offers."
        ))
