# مواصفات الـ API

## عام
- Base: `/api/v1/`، JSON، المصادقة JWT: `Authorization: Bearer <token>`.
- معرّفات في الـ URL = `uuid`.
- المبالغ والكميات كنص Decimal (`"12.5000"`).
- ترقيم صفحات: `?page=&page_size=`؛ فلترة `django-filter`؛ بحث `?search=`؛ ترتيب `?ordering=`.
- أخطاء موحّدة: `{ "code": "insufficient_stock", "detail": "...", "fields": {...} }`.
- الكتابة الحساسة (checkout، دفعات) تقبل `Idempotency-Key`.
- مجموعتان: **Admin API** (`/api/v1/admin/...`، للموظفين) و**Storefront API** (`/api/v1/store/...`، للزبائن/عام).

## Auth
| Method | Path | وصف |
|---|---|---|
| POST | `/auth/refresh` | تجديد |
| POST | `/auth/whatsapp/send-code` | `{phone}` — إرسال رمز تحقق عبر واتساب (6 أرقام، 5 دقائق، مهلة 60ث بين الإرسالات، 5/ساعة لكل رقم) |
| POST | `/auth/whatsapp/verify` | `{phone, code, full_name?}` — يتحقق من الرمز ثم **يسجّل الدخول أو ينشئ حساب الزبون**. لرقم جديد يلزم `full_name` (وإلا `400 full_name_required` والرمز يبقى صالحاً). الرد: `created`, `user_type`, `access`, `refresh` |
> واتساب هو **طريقة الدخول الوحيدة** لكل الأنواع (زبون/موظف/أدمن). رقم غير مسجّل = إنشاء حساب زبون؛ الموظفون والأدمن ينشئهم الأدمن مسبقاً برقمهم، ثم يدخلون بنفس الصفحة ويحدد `user_type` وجهتهم.
| GET/PATCH | `/auth/me` | الملف الشخصي |

## Admin — المستخدمون والصلاحيات (يتطلب `users.*`)
| Method | Path | وصف |
|---|---|---|
| GET/POST | `/admin/users` | قائمة الموظفين / إنشاء موظف برقم واتساب |
| GET/PATCH | `/admin/users/{id}` | تفاصيل / تعديل |
| POST | `/admin/users/{id}/deactivate` · `/activate` | تعطيل / تفعيل (يُبطل توكناته) |
| PUT | `/admin/users/{id}/roles` | تعيين أدواره `{roles:[...]}` |
| PUT | `/admin/users/{id}/permissions` | الصلاحيات المباشرة `[{permission, effect, scope, expires_at, reason}]` |
| GET | `/admin/users/{id}/effective-permissions` | الصلاحيات الفعلية النهائية (للمراجعة) |
| GET | `/admin/customers/?search=` · POST `/admin/customers/{uuid}/promote/` | بحث الزبائن وترقية زبون إلى موظف `{role_ids?}` (`users.create`) |
| GET | `/admin/permissions` | كتالوج الصلاحيات (للقراءة) |
| CRUD | `/admin/roles` | إدارة الأدوار |
| PUT | `/admin/roles/{id}/permissions` | تحديد صلاحيات الدور مع النطاقات |
| GET | `/auth/me` | يعيد أيضاً `permissions` الفعلية لإخفاء/إظهار عناصر الواجهة |

> الواجهة تعتمد على `permissions` لإخفاء الأزرار فقط؛ **الفرض الفعلي دائماً في الخادم**: كل endpoint يعلن الصلاحية المطلوبة (مثل `required_permission = "inventory.adjust"`) ويعيد 403 عند غيابها.

## Admin — الكتالوج
| Method | Path | وصف |
|---|---|---|
| GET/POST | `/admin/products` | قائمة (فلاتر: status, category, visible, low_stock) / إنشاء |
| GET/PATCH/DELETE | `/admin/products/{id}` | تفاصيل / تعديل / حذف محمي (soft) |
| POST | `/admin/products/{id}/publish` | status=active |
| POST | `/admin/products/{id}/archive` | أرشفة |
| POST | `/admin/products/{id}/show` · `/hide` | إظهار / إخفاء عن المتجر |
| POST | `/admin/products/bulk` | `{ids, action: show|hide|archive}` |
| GET/POST | `/admin/products/{id}/variants` | المتغيّرات |
| PATCH/DELETE | `/admin/variants/{id}` | تعديل / حذف محمي |
| PUT | `/admin/variants/{id}/prices` | أسعار حسب قائمة الأسعار |
| POST | `/admin/products/{id}/images` | رفع صور |
| POST | `/admin/products/import` · GET `/export` | CSV |
| CRUD | `/admin/categories` | التصنيفات |
| CRUD | `/admin/price-lists` | قوائم الأسعار |

## Admin — المخزون
| Method | Path | وصف |
|---|---|---|
| CRUD | `/admin/warehouses` | المخازن |
| GET | `/admin/stock` | أرصدة (فلاتر: warehouse, variant, low_stock) |
| GET | `/admin/stock/movements` | سجل الحركات (read-only) |
| POST | `/admin/stock/adjustments` | تعديل/جرد `{warehouse, reason, lines[]}` |
| POST | `/admin/stock/adjustments/{id}/post` | ترحيل |
| POST | `/admin/stock/transfers` | تحويل بين مخازن |
| POST | `/admin/stock/transfers/{id}/receive` | استلام التحويل |
| GET | `/admin/stock/low` | تحت حد الطلب |

## Admin — الطلبيات
| Method | Path | وصف |
|---|---|---|
| GET/POST | `/admin/orders` | قائمة (status, payment_status, date, customer) / إنشاء يدوي |
| GET/PATCH | `/admin/orders/{id}` | تفاصيل / تعديل (ما دامت pending) |
| POST | `/admin/orders/{id}/confirm` | تأكيد + حجز |
| POST | `/admin/orders/{id}/process` | تجهيز |
| POST | `/admin/orders/{id}/ship` | شحن `{tracking, carrier}` |
| POST | `/admin/orders/{id}/deliver` | تسليم |
| POST | `/admin/orders/{id}/cancel` | إلغاء `{reason}` |
| GET | `/admin/orders/{id}/history` | سجل الحالات |
| POST | `/admin/orders/{id}/payments` | تسجيل دفعة |
| POST | `/admin/orders/{id}/returns` | مرتجع |
| GET | `/admin/orders/{id}/invoice.pdf` | الفاتورة |
| CRUD | `/admin/customers` | الزبائن |

## Admin — المشتريات
`CRUD /admin/suppliers`، `CRUD /admin/purchase-orders`، `POST /admin/purchase-orders/{id}/receive`، `CRUD /admin/purchase-invoices`، `POST /admin/supplier-payments`.

## Admin — المحاسبة
| Method | Path | وصف |
|---|---|---|
| CRUD | `/admin/accounts` | شجرة الحسابات |
| CRUD | `/admin/fiscal-periods` · POST `/{id}/close` | الفترات |
| GET/POST | `/admin/journal-entries` | قيود (يدوي = draft) |
| POST | `/admin/journal-entries/{id}/post` · `/reverse` | ترحيل / عكس |
| CRUD | `/admin/tax-rates` | الضرائب |
| CRUD | `/admin/currencies` · `/exchange-rates` | العملات والأسعار |
| GET/PUT | `/admin/account-mappings` | ربط الحسابات الافتراضية |
| GET | `/admin/reports/trial-balance?from=&to=` | ميزان المراجعة |
| GET | `/admin/reports/general-ledger?account=&from=&to=` | الأستاذ |
| GET | `/admin/reports/income-statement` · `/balance-sheet` | القوائم |
| GET | `/admin/reports/aged-receivables` · `/aged-payables` | الأعمار |
| GET | `/admin/reports/inventory-valuation` · `/sales` | مخزون / مبيعات |

## Storefront (عام)
| Method | Path | Auth | وصف |
|---|---|---|---|
| GET | `/store/categories` | — | التصنيفات |
| GET | `/store/products` | — | **الظاهرة فقط**، فلترة/بحث/ترتيب، `?currency=` |
| GET | `/store/products/{slug}` | — | تفاصيل + variants + توفر |
| GET/POST/PATCH/DELETE | `/store/cart` · `/cart/items` | session/JWT | السلة |
| POST | `/store/checkout` | JWT/guest + Idempotency-Key | إنشاء طلبية |
| GET | `/store/orders` · `/orders/{id}` | JWT | طلباتي |
| POST | `/store/orders/{id}/cancel` | JWT | إلغاء (pending فقط) |
| CRUD | `/store/addresses` | JWT | عناويني |

## أمثلة

**إنشاء منتج**
```json
POST /admin/products
{ "name": "قميص", "category": "<uuid>", "is_visible_on_store": false,
  "variants": [{ "sku": "SH-BL-L", "attributes": {"color":"blue","size":"L"},
                 "prices": [{"price_list":"<uuid>","price":"25.0000"}] }] }
```

**تعديل كمية (جرد)**
```json
POST /admin/stock/adjustments
{ "warehouse": "<uuid>", "reason": "count",
  "lines": [{ "variant": "<uuid>", "counted_qty": "48.000" }] }
```

**Checkout**
```json
POST /store/checkout   (Idempotency-Key: 9f2c…)
{ "currency": "USD", "shipping_address": {...}, "payment_method": "cod",
  "items": [{ "variant": "<uuid>", "quantity": "2" }] }
```
أخطاء محتملة: `insufficient_stock`, `product_unavailable`, `price_missing_for_currency`, `invalid_transition`, `period_closed`, `unbalanced_entry`.
