# EasyLife — API Documentation

المرجع الرسمي لكل endpoints النظام الموجودة فعلاً: الغرض، من يستطيع استخدامها، الـ Request، الـ Response، والأخطاء. الردود الواردة هنا مأخوذة من تشغيل النظام الفعلي.

> **قاعدة إلزامية:** كل endpoint جديد يُضاف إلى النظام يجب توثيقه في هذا الملف **في نفس التغيير**، بصيغة العنوان `#### METHOD /path` كما في الأقسام أدناه. اختبار تلقائي (`apps/core/tests/test_api_docs.py`) يفشل إن وُجد endpoint غير موثّق هنا، أو توثيق لـ endpoint لم يعد موجوداً. انظر القسم 14 لقالب الإضافة.

## الفهرس
1. [اتفاقيات عامة](#1-اتفاقيات-عامة)
2. [أنواع المستخدمين والصلاحيات](#2-أنواع-المستخدمين-والصلاحيات)
3. [المصادقة والجلسة (Auth)](#3-المصادقة-والجلسة-auth)
4. [الأدوار](#4-الأدوار)
5. [إدارة الموظفين](#5-إدارة-الموظفين)
6. [إدارة الزبائن وترقيتهم لموظفين](#6-إدارة-الزبائن-وترقيتهم-لموظفين)
7. [العملات وأسعار الصرف](#7-العملات-وأسعار-الصرف)
8. [المنتجات والكتالوج](#8-المنتجات-والكتالوج)
9. [المخزون](#9-المخزون)
10. [واجهة المتجر العامة](#10-واجهة-المتجر-العامة-للزبائن)
10-ب. [حساب الزبون: ديونه وفواتيره](#10-ب-حساب-الزبون-ديونه-وفواتيره)
11. [المحاسبة](#11-المحاسبة)
11-ب. [نقطة البيع (POS)](#11-ب-نقطة-البيع-pos)
12. [كتالوج الأخطاء](#12-كتالوج-الأخطاء)
13. [سيناريوهات كاملة](#13-سيناريوهات-كاملة)
14. [كيف توثّق endpoint جديداً](#14-كيف-توثّق-endpoint-جديداً)
15. [ملحقات](#15-ملحقات)

---

## 1) اتفاقيات عامة

### العنوان والصيغة
- **Base URL (تطوير):** `http://127.0.0.1:8000/api/v1`
- كل المسارات في هذا الملف **نسبة إلى** الـ Base URL. مثال: `/auth/me` = `http://127.0.0.1:8000/api/v1/auth/me`.
- الطلبات والردود JSON، أرسل الترويسة `Content-Type: application/json` مع أي body.
- **الشرطة المائلة في النهاية:** مسارات `/auth/...` و`/admin/roles` **بدون** `/` في النهاية. مسارات `/admin/users/...` و`/admin/customers/...` **بها** `/` في النهاية. الخطأ في ذلك يعطي 404.
- المعرّفات `{uuid}` من نوع UUID (مثل `c28e24e8-6e42-4fa7-babc-15b3b0ca2332`). التواريخ بصيغة ISO-8601 بتوقيت UTC.

### التوثيق (Authentication)
الدخول **بواتساب فقط** (لا بريد ولا كلمة مرور). بعد الدخول تحصل على توكنين:

| التوكن | العمر | الاستخدام |
|---|---|---|
| `access` | 15 دقيقة | يُرسل مع كل طلب محمي: `Authorization: Bearer <access>` |
| `refresh` | 7 أيام | لتجديد `access` فقط عبر `POST /auth/refresh` |

**مهم:** كل تجديد يعطيك `refresh` **جديداً** ويُبطل القديم؛ استبدل الاثنين دائماً. عند `401` على طلب عادي: جرّب التجديد مرة واحدة ثم أعد الطلب، فإن فشل التجديد فوجّه المستخدم لصفحة الدخول.

عند تعطيل حساب تُبطل كل جلساته فوراً (يرجع `401` لطلباته التالية).

### الصلاحيات
كل endpoint إداري يحدد الصلاحية المطلوبة (مثل `users.create`). غياب الصلاحية يرجع `403 permission_denied`. التفاصيل في القسم 2.

### شكل الأخطاء (موحّد لكل الـ API)
```json
{ "code": "invalid_code", "detail": "The code is incorrect or has expired." }
```
مع أخطاء الحقول يضاف `fields`:
```json
{ "code": "validation_error", "detail": "Invalid input.",
  "fields": { "phone": ["This field is required."] } }
```
- **اعتمد على `code`** في الواجهة (ثابت)، لا على `detail` (للقراءة البشرية وقد يتغير).
- قائمة كل الأكواد في القسم 12.

### التقسيم إلى صفحات (Pagination)
قوائم `GET /admin/users/` و`GET /admin/customers/` مقسّمة: **25 عنصراً** في الصفحة، وتنتقل بـ `?page=2`.
```json
{ "count": 60, "next": "http://127.0.0.1:8000/api/v1/admin/users/?page=2",
  "previous": null, "results": [ ... ] }
```
`count` العدد الكلي، `next`/`previous` روابط الصفحة التالية/السابقة (`null` إن لم توجد). `GET /admin/roles` غير مقسّمة.

### الملفات والصور
رفع الصور فقط عبر `multipart/form-data` (انظر `POST /admin/products/{uuid}/images/`). الروابط في الردود مطلقة (`http://…/media/…`). في التطوير يخدم Django مجلد `media/`، وفي الإنتاج يجب أن يخدمه الخادم الأمامي/التخزين السحابي.

### الأرقام العشرية
الكميات (`on_hand`, `quantity`…) بثلاث منازل، والمبالغ والأسعار بأربع، وأسعار الصرف بثمانٍ، وكلها **نصوص** (`"20.000"`) لتفادي أخطاء الفاصلة العائمة. أرسلها نصوصاً أو أرقاماً، وتعامل مع الردود كنصوص.

### التعديل: PATCH وليس PUT
معظم الموارد تُعدَّل بـ `PATCH` (حقول اختيارية). `PUT` غير مدعوم إلا حيث يُذكر صراحة (`PUT /admin/users/{uuid}/`، `.../roles/`، `.../prices/`).

### صيغ رقم الهاتف
الهاتف هو هوية الحساب. المقبول: `0591234567` · `+970 59 123 4567` · `00970591234567` · `970591234567`. كلها تُخزَّن وتُرجَع بصيغة أرقام دولية فقط: **`970591234567`**. الرقم المحلي الذي يبدأ بصفر يأخذ مقدّمة الدولة الافتراضية `970`. الطول المقبول بين 8 و15 رقماً.

### حدود المعدل (Throttling)
عند التجاوز يرجع `429`، فلا تُعد المحاولة تلقائياً.

| الإجراء | الحد |
|---|---|
| `POST /auth/whatsapp/send-code` | 10/ساعة لكل IP + 5/ساعة لكل رقم + 60 ثانية بين رمزين |
| `POST /auth/whatsapp/verify` | 30/ساعة لكل IP + 5 محاولات خاطئة لكل رمز |
| `POST /auth/refresh` | 10/دقيقة |
| endpoints المتجر العامة (`/store/…`) | 100/دقيقة لكل IP |
| باقي الـ endpoints | 300/دقيقة لكل مستخدم موثّق |

---

## 2) أنواع المستخدمين والصلاحيات

### الأنواع الثلاثة
| النوع | `user_type` | `is_superadmin` | الوصول |
|---|---|---|---|
| زبون | `customer` | `false` | المتجر فقط. أي endpoint إداري يرجع له `403` |
| موظف | `staff` | `false` | لوحة الإدارة بحسب الصلاحيات الممنوحة له فقط (لا صلاحية افتراضية) |
| أدمن | `staff` | `true` | كل الصلاحيات تلقائياً |

بعد الدخول يوجّه الـ Frontend المستخدم حسب `user_type`: `customer` → المتجر، `staff` → لوحة الإدارة.

### كيف تُحسب صلاحيات الموظف (RBAC)
صلاحيات الموظف = صلاحيات **أدواره** (يمكن أكثر من دور). الأدمن يمنح الأدوار. تُطبَّق التغييرات **فوراً** دون إعادة تسجيل دخول. الأدمن يحصل دائماً على كل الصلاحيات.

**قيد منع تصعيد الصلاحيات:** لا يستطيع أحد منح دور يحوي صلاحيات لا يملكها هو (`cannot_grant`). إنشاء أدمن للأدمن فقط.

### كتالوج الصلاحيات (`module.action`)
| الوحدة | الصلاحيات |
|---|---|
| `products` | view, create, update, delete, publish, toggle_visibility, archive, import, export, manage_prices, manage_cost |
| `categories` | view, manage |
| `brands` | view, manage |
| `storefront` | view, manage |
| `storefront` | view, manage |
| `inventory` | view, adjust, transfer, stocktake, view_cost, manage_warehouses |
| `orders` | view, create, update, confirm, process, ship, deliver, cancel, return, apply_discount |
| `payments` | view, record, refund |
| `customers` | view, create, update, delete, view_balance |
| `purchasing` | view, create, approve, receive, pay_supplier, manage_suppliers |
| `accounting` | view, create_entry, post_entry, reverse_entry, manage_accounts, close_period, manage_tax, manage_currencies |
| `sales` | pos, view, discount, refund, credit |
| `reports` | sales, inventory, financial, export |
| `users` | view, create, update, deactivate, manage_roles, manage_permissions |
| `settings` | view, manage, view_audit_log |

الصلاحيات المستعملة فعلاً في الـ endpoints حالياً: `users.view` `users.create` `users.update` `users.deactivate` `users.manage_roles` · `customers.view` · `accounting.view` `accounting.manage_currencies` · `categories.view` `categories.manage` · `brands.view` `brands.manage` · `storefront.view` `storefront.manage` · `products.view` `products.create` `products.update` `products.delete` `products.publish` `products.archive` `products.toggle_visibility` `products.manage_prices` `products.manage_cost` `products.import` · `inventory.view` `inventory.view_cost` `inventory.adjust` `inventory.transfer` `inventory.manage_warehouses` · `accounting.manage_accounts` `accounting.manage_tax` `accounting.close_period` `accounting.create_entry` `accounting.post_entry` `accounting.reverse_entry` · `reports.financial` `reports.sales` `reports.export` · `sales.pos` `sales.view` `sales.discount` `sales.refund` `sales.credit` · `customers.create` `customers.view_balance` `payments.record`. باقي الكتالوج (مثل `inventory.stocktake` و`products.export`) محجوز لوحدات قادمة.

### الأدوار الجاهزة (تُزرع بـ `python manage.py sync_permissions`)
| الدور | الغرض |
|---|---|
| `accountant` | المحاسبة والتقارير + عرض المبيعات والمخزون |
| `warehouse` | المخزون، التجهيز والشحن، استلام المشتريات (دون رؤية التكلفة) |
| `sales` | الطلبات والزبائن وتسجيل الدفعات (دون إلغاء) |
| `store_manager` | إدارة المنتجات والتصنيفات والماركات + عرض الطلبات |
| `cashier` | نقطة البيع: فتح وردية، البيع (ومنه البيع بالدين على زبون وإضافة الزبون)، ورؤية مبيعاته هو فقط (`sales.pos` `sales.credit`) |

`sync_permissions` لا يعدّل الأدوار الموجودة، **إلا** أنه يضيف للأدوار الجاهزة (`is_system`) أي صلاحية **جديدة على الكتالوج** يتضمنها قالبها (مثل `sales.credit` لدور `cashier`)؛ فما حذفه الأدمن من دور لا يعود إليه.

لا يتضمن أي دور جاهز صلاحيات `users.*` ولا `settings.manage` ولا `accounting.close_period` (للأدمن فقط).

### كائنات الرد المشتركة

**`user`** (ملف المستخدم نفسه):
```json
{ "uuid": "c28e24e8-6e42-4fa7-babc-15b3b0ca2332", "full_name": "Sara", "phone": "970591234567",
  "user_type": "customer", "is_superadmin": false, "permissions": [] }
```
`permissions`: أكواد الصلاحيات الفعلية (للموظف). استعملها لإظهار/إخفاء عناصر الواجهة فقط؛ الخادم هو الذي يفرضها.

**`staff`** (موظف كما يراه الأدمن):
```json
{ "uuid": "4e694393-9e2d-447b-b419-4b017f91c64b", "full_name": "Wally", "phone": "970592222222",
  "is_active": true, "is_superadmin": false, "last_login": null,
  "created_at": "2026-10-01T21:33:45.013602Z", "roles": ["accountant"] }
```

**`customer`** (زبون كما يراه الأدمن):
```json
{ "uuid": "75b23a5c-3e94-4923-9258-c9a0fd67281c", "full_name": "Umm Ali", "phone": "970599123456",
  "is_active": true, "created_at": "2026-10-03T15:46:37.377602+03:00", "balance": "7.00" }
```
`balance`: ما على الزبون من دين الآن (مجموع `balance_due` لفواتيره). يظهر لمن يملك `customers.view_balance` أو `sales.credit`، وإلا `null`.

---

## 3) المصادقة والجلسة (Auth)

### التدفق الموصى به في الواجهة
```
1. المستخدم يدخل رقمه            → POST /auth/whatsapp/send-code
2. يدخل الرمز الذي وصله بواتساب  → POST /auth/whatsapp/verify   (بدون full_name)
3. إن رجع 400 full_name_required  → رقم جديد: اعرض حقل الاسم وأعد verify بنفس الرمز + full_name
4. خزّن access و refresh           ووجّه حسب user_type
```

#### POST /auth/whatsapp/send-code
يرسل رمز تحقق من 6 أرقام إلى واتساب الرقم. نفس الـ endpoint لكل المستخدمين (جدد وقدامى، زبائن وموظفين).

- **التوثيق:** غير مطلوب · **الصلاحية:** —
- **صلاحية الرمز:** 5 دقائق، يُستعمل مرة واحدة. الرمز الأحدث فقط هو الصالح.
- **الرد متطابق سواء كان الرقم مسجلاً أم لا** (لا يكشف وجود الحسابات).

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `phone` | string | نعم | بأي من الصيغ المقبولة |

```json
{ "phone": "0591234567" }
```

**Response `200`**
```json
{ "detail": "Code sent.", "expires_in": 300, "resend_in": 60 }
```
`expires_in`: عمر الرمز بالثواني. `resend_in`: أقل مدة قبل طلب رمز جديد (استعملها لعدّاد زر «إعادة الإرسال»).

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `validation_error` | `phone` مفقود |
| 400 | `invalid_phone` | رقم غير صالح |
| 429 | `otp_cooldown` | طلبت رمزاً قبل مرور 60 ثانية |
| 429 | `otp_limit` | تجاوز 5 رموز/ساعة لهذا الرقم |
| 429 | `throttled` | كثرة الطلبات من نفس الجهاز |
| 502 | `delivery_failed` | تعذّر الإرسال عبر واتساب؛ أعد المحاولة (لا يُحتسب كإرسال) |

---

#### POST /auth/whatsapp/verify
يتحقق من الرمز ثم: **يسجّل دخول** الحساب إن كان الرقم مسجلاً (زبون أو موظف أو أدمن)، أو **ينشئ حساب زبون** إن كان الرقم جديداً. هذه طريقة الدخول الوحيدة في النظام.

- **التوثيق:** غير مطلوب · **الصلاحية:** —

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `phone` | string | نعم | نفس الرقم الذي أُرسل له الرمز |
| `code` | string | نعم | الرمز المكوّن من 6 أرقام |
| `full_name` | string | لرقم جديد فقط | الاسم لإنشاء الحساب (حتى 150 حرفاً). يُتجاهل للحسابات الموجودة |

حساب موجود:
```json
{ "phone": "0591234567", "code": "483920" }
```
رقم جديد:
```json
{ "phone": "0591234567", "code": "483920", "full_name": "Sara Ahmad" }
```

**Response `200` (دخول) أو `201` (تم إنشاء حساب جديد)**
```json
{
  "created": true,
  "user_type": "customer",
  "is_superadmin": false,
  "user": { "uuid": "c28e24e8-6e42-4fa7-babc-15b3b0ca2332", "full_name": "Sara Ahmad",
            "phone": "970591234567", "user_type": "customer",
            "is_superadmin": false, "permissions": [] },
  "access": "eyJhbGciOi…", "refresh": "eyJhbGciOi…"
}
```
| الحقل | معناه |
|---|---|
| `created` | `true` إن أُنشئ الحساب الآن (201)، `false` لدخول حساب موجود (200) |
| `user_type` / `is_superadmin` | لتوجيه المستخدم: `customer` → المتجر، `staff` → لوحة الإدارة |
| `user` | ملف المستخدم مع `permissions` |
| `access` / `refresh` | التوكنات |

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `validation_error` | حقل مفقود |
| 400 | `invalid_phone` | رقم غير صالح |
| 400 | `full_name_required` | رقم جديد بلا `full_name`. **الرمز لم يُستهلك**: أعد الطلب بنفس الرمز مع الاسم |
| 400 | `invalid_code` | رمز خاطئ أو منتهٍ أو مستخدم، أو الحساب معطّل (رسالة واحدة عمداً). بعد 5 محاولات خاطئة يُحرق الرمز فاطلب رمزاً جديداً |
| 429 | `throttled` | كثرة المحاولات |

> رقم غير مسجّل يصبح **زبوناً** دائماً. الموظفون والأدمن لا ينشئون أنفسهم؛ يُضافون أو يُرقَّون بواسطة الأدمن (الأقسام 5 و6) ثم يدخلون بنفس الـ endpoint.

---

#### POST /auth/refresh
يجدّد التوكنات باستخدام `refresh`.

- **التوثيق:** غير مطلوب (يُرسل `refresh` في الـ body) · **الصلاحية:** —

**Request body**
| الحقل | النوع | مطلوب |
|---|---|---|
| `refresh` | string | نعم |

```json
{ "refresh": "eyJhbGciOi…" }
```

**Response `200`** — استبدل التوكنين معاً:
```json
{ "access": "eyJhbGciOi…", "refresh": "eyJhbGciOi…" }
```

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `validation_error` | `refresh` مفقود |
| 401 | `invalid_token` | منتهٍ، أو أُبطل (تسجيل خروج/تجديد سابق/تعطيل الحساب) → وجّه لصفحة الدخول |

---

#### POST /auth/logout
يُبطل `refresh` المُرسَل. يجب حذف التوكنين من جهاز العميل بعدها.

- **التوثيق:** مطلوب (Bearer access) · **الصلاحية:** أي مستخدم موثّق

**Request body**
| الحقل | النوع | مطلوب |
|---|---|---|
| `refresh` | string | نعم |

```json
{ "refresh": "eyJhbGciOi…" }
```

**Response `204`** بدون محتوى. (توكن غير صالح لا يسبب خطأ، فالنتيجة نفسها.)

**الأخطاء:** `400 validation_error` (`refresh` مفقود) · `401 not_authenticated` / `token_not_valid`.

---

#### GET /auth/me
ملف المستخدم الحالي مع صلاحياته. استدعه عند فتح التطبيق لمعرفة النوع والصلاحيات **الحالية** (تتحدث فور تغييرها، حتى لو بقي المستخدم بنفس التوكن).

- **التوثيق:** مطلوب · **الصلاحية:** أي مستخدم موثّق
- **Request:** بدون body.

**Response `200`**
```json
{ "uuid": "c28e24e8-6e42-4fa7-babc-15b3b0ca2332", "full_name": "Sara", "phone": "970591234567",
  "user_type": "customer", "is_superadmin": false, "permissions": [] }
```
**الأخطاء:** `401 not_authenticated` (لا توكن) · `401 token_not_valid` (توكن منتهٍ/غير صالح).

---

#### PATCH /auth/me
يعدّل اسم المستخدم نفسه.

- **التوثيق:** مطلوب · **الصلاحية:** أي مستخدم موثّق

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `full_name` | string | لا | الحقل الوحيد القابل للتعديل |

```json
{ "full_name": "Sara A" }
```
باقي الحقول (`phone`, `user_type`, `is_superadmin`, `permissions`) **للقراءة فقط** ويُتجاهل أي تعديل عليها بصمت (لا خطأ).

**Response `200`:** كائن `user` المحدّث (نفس شكل `GET /auth/me`).
**الأخطاء:** `400 validation_error` (اسم فارغ/طويل) · `401`.

---

## 4) الأدوار

#### GET /admin/roles
قائمة كل الأدوار المتاحة للتعيين (لاستخدامها في اختيار `role_ids`). غير مقسّمة صفحات.

- **التوثيق:** مطلوب · **الصلاحية:** `users.view`
- **Request:** بدون body.

**Response `200`**
```json
[
  { "id": 1, "name": "accountant", "description": "Accounting and financial reports", "is_system": true },
  { "id": 3, "name": "sales", "description": "Orders, customers and payments", "is_system": true },
  { "id": 4, "name": "store_manager", "description": "Catalog management", "is_system": true },
  { "id": 2, "name": "warehouse", "description": "Inventory, receiving and shipping", "is_system": true }
]
```
`is_system: true` = دور جاهز مزروع من النظام. استعمل `id` في `role_ids`.

**الأخطاء:** `401` · `403 permission_denied`.

---

## 5) إدارة الموظفين

كل هذه المسارات تعمل على المستخدمين من نوع `staff` فقط (والأدمن). مسارات `{uuid}` تُرجع `404 not_found` إن لم يكن الحساب موظفاً.

#### GET /admin/users/
قائمة الموظفين (الأحدث أولاً)، مقسّمة صفحات (25).

- **الصلاحية:** `users.view`
- **Query:** `page` (اختياري، رقم الصفحة).

**Response `200`**
```json
{
  "count": 2, "next": null, "previous": null,
  "results": [
    { "uuid": "6a00a8e3-8fdc-416c-bd96-672f13f99899", "full_name": "Boss", "phone": "970590000009",
      "is_active": true, "is_superadmin": true, "last_login": null,
      "created_at": "2026-10-01T21:33:44.988872Z", "roles": [] },
    { "uuid": "c28e24e8-6e42-4fa7-babc-15b3b0ca2332", "full_name": "Sara A", "phone": "970591234567",
      "is_active": true, "is_superadmin": false, "last_login": null,
      "created_at": "2026-10-01T21:33:44.913625Z", "roles": ["accountant"] }
  ]
}
```

---

#### POST /admin/users/
يضيف موظفاً جديداً برقم هاتفه. **لا كلمة مرور**: يصل الموظف رسالة واتساب، ثم يدخل من صفحة الدخول العادية برقمه. للزبون الموجود استعمل [الترقية](#post-admincustomersuuidpromote).

- **الصلاحية:** `users.create` (+ `users.manage_roles` إن أرسلت `role_ids`)
- يُسجَّل في سجل التدقيق: `staff.created`.

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `phone` | string | نعم | رقم واتساب الموظف |
| `full_name` | string | نعم | حتى 150 حرفاً |
| `role_ids` | int[] | لا | الأدوار الأولية. الافتراضي `[]` = بلا صلاحيات |
| `is_superadmin` | bool | لا | `true` لإنشاء أدمن (للأدمن فقط). الافتراضي `false` |

```json
{ "phone": "0592222222", "full_name": "Wally", "role_ids": [2] }
```

**Response `201`** — كائن `staff` مع حقل إضافي:
```json
{ "uuid": "4e694393-9e2d-447b-b419-4b017f91c64b", "full_name": "Wally", "phone": "970592222222",
  "is_active": true, "is_superadmin": false, "last_login": null,
  "created_at": "2026-10-01T21:33:45.013602Z", "roles": ["warehouse"],
  "whatsapp_notified": true }
```
`whatsapp_notified`: هل قبل الـ API رسالة الإشعار؟ `false` يعني أن الموظف أُضيف لكن الرسالة لم تُرسل (أخبره يدوياً). فشل الرسالة لا يُلغي الإضافة.

**نص الرسالة التي تصل الموظف:** «مرحباً Wally، تمت إضافتك كموظف في EasyLife. الأدوار: warehouse. سجّل الدخول من صفحة الدخول برقمك هذا وسيصلك رمز تحقق على واتساب.»

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `validation_error` | `phone` أو `full_name` مفقود |
| 400 | `invalid_phone` | رقم غير صالح |
| 400 | `phone_taken` | الرقم مسجّل مسبقاً (حتى لو كزبون؛ استعمل الترقية) |
| 400 | `invalid_role` | أحد `role_ids` غير موجود |
| 403 | `cannot_grant` | `is_superadmin` من غير أدمن، أو أدوار تحوي صلاحيات لا تملكها (التفاصيل في `fields.permissions`) |
| 403 | `forbidden` | أرسلت `role_ids` ولا تملك `users.manage_roles` |
| 403 | `permission_denied` | لا تملك `users.create` |

---

#### GET /admin/users/{uuid}/
تفاصيل موظف واحد.

- **الصلاحية:** `users.view`
- **Response `200`:** كائن `staff`.
- **الأخطاء:** `404 not_found` · `403` · `401`.

---

#### PATCH /admin/users/{uuid}/
يعدّل بيانات موظف. الحقل القابل للتعديل: `full_name` فقط (الهاتف والحالة والأدوار لها مسارات خاصة).

- **الصلاحية:** `users.update`

**Request body**
| الحقل | النوع | مطلوب |
|---|---|---|
| `full_name` | string | لا |

```json
{ "full_name": "Wally B" }
```
**Response `200`:** كائن `staff` المحدّث. **الأخطاء:** `400 validation_error` · `404` · `403` · `401`.

---

#### PUT /admin/users/{uuid}/
مثل `PATCH` لكن `full_name` **مطلوب**. يُفضّل استعمال `PATCH`.

- **الصلاحية:** `users.update`
- **Request:** `{ "full_name": "Wally C" }` · **Response `200`:** كائن `staff`.
- **الأخطاء:** `400 validation_error` (إن غاب `full_name`) · `404` · `403` · `401`.

---

#### POST /admin/users/{uuid}/deactivate/
يعطّل حساب موظف: لا يستطيع الدخول، وتُبطل **كل جلساته فوراً** (access وrefresh).

- **الصلاحية:** `users.deactivate` · **Request:** بدون body.
- يُسجَّل: `staff.deactivated`.

**Response `200`:** كائن `staff` مع `"is_active": false`.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `self_lockout` | لا تستطيع تعطيل نفسك |
| 400 | `last_admin` | آخر أدمن فعّال لا يُعطَّل |
| 403 | `forbidden` | تعطيل أدمن من غير أدمن |
| 404 | `not_found` | ليس موظفاً |

---

#### POST /admin/users/{uuid}/activate/
يعيد تفعيل موظف معطّل (يحتاج تسجيل دخول جديد).

- **الصلاحية:** `users.deactivate` · **Request:** بدون body · **Response `200`:** كائن `staff` مع `"is_active": true`.
- **الأخطاء:** `404` · `403` · `401`.

---

#### PUT /admin/users/{uuid}/roles/
يعيّن أدوار الموظف. **يستبدل الأدوار الحالية بالكامل** (`[]` يسحبها كلها). يسري فوراً.

- **الصلاحية:** `users.manage_roles`
- يُسجَّل: `staff.roles_changed` (قبل/بعد).

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `role_ids` | int[] | نعم | من `GET /admin/roles` |

```json
{ "role_ids": [3, 2] }
```
**Response `200`:** كائن `staff` بأدواره الجديدة.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `validation_error` | `role_ids` مفقود |
| 400 | `invalid_role` | دور غير موجود |
| 403 | `cannot_grant` | الأدوار تحوي صلاحيات لا تملكها أنت (`fields.permissions`) |
| 403 | `forbidden` | غير أدمن يغيّر أدوار نفسه |
| 404 | `not_found` | ليس موظفاً |

---

#### GET /admin/users/{uuid}/effective-permissions/
الصلاحيات الفعلية النهائية للموظف (لمراجعة ما يستطيع فعله). للأدمن تُرجع كل الكتالوج.

- **الصلاحية:** `users.view` · **Request:** بدون body.

**Response `200`**
```json
{ "permissions": ["accounting.view", "customers.view", "inventory.view", "orders.view"] }
```
**الأخطاء:** `404` · `403` · `401`.

---

## 6) إدارة الزبائن وترقيتهم لموظفين

#### GET /admin/customers/
قائمة الزبائن (أحدثهم أولاً) مع بحث، مقسّمة صفحات (25). الموظفون لا يظهرون هنا.

- **الصلاحية:** `customers.view` أو `sales.credit` (الكاشير يبحث عن الزبون الذي يسجّل عليه الدين)

**Query**
| المعامل | ملاحظات |
|---|---|
| `search` | بحث جزئي في الاسم أو الهاتف (مثل `?search=sara` أو `?search=97059`) |
| `page` | رقم الصفحة |

**Response `200`**
```json
{
  "count": 1, "next": null, "previous": null,
  "results": [
    { "uuid": "75b23a5c-3e94-4923-9258-c9a0fd67281c", "full_name": "Umm Ali", "phone": "970599123456",
      "is_active": true, "created_at": "2026-10-03T15:46:37.377602+03:00", "balance": "7.00" }
  ]
}
```

---

#### POST /admin/customers/
**يضيف زبوناً** بالاسم ورقم الواتساب (من صفحة الزبائن، أو من نقطة البيع لتسجيل دين عليه). يُطبَّع الرقم كما في الدخول. لا تُرسل رسالة؛ الزبون يدخل لاحقاً للمنصة بكود واتساب على نفس الرقم فيجد حسابه وديونه.

- **الصلاحية:** `customers.create` أو `sales.credit`
- يُسجَّل: `customer.created`.

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `full_name` | string | نعم | حتى 150 حرفاً (تُزال المسافات الطرفية) |
| `phone` | string | نعم | رقم الواتساب بأي صيغة مقبولة (`0599123456` · `+970599123456`) |

```json
{ "full_name": "Umm Ali", "phone": "0599123456" }
```

**Response `201`:** كائن `customer`:
```json
{
  "uuid": "75b23a5c-3e94-4923-9258-c9a0fd67281c",
  "full_name": "Umm Ali",
  "phone": "970599123456",
  "is_active": true,
  "created_at": "2026-10-03T15:46:37.377602+03:00",
  "balance": "0.00"
}
```

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `invalid_phone` | رقم غير صالح |
| 400 | `validation_error` | الاسم أو الرقم ناقص |
| 409 | `customer_exists` | الرقم لزبون مسجَّل أصلاً (ابحث عنه بالرقم واختره) |
| 409 | `phone_taken` | الرقم لموظف |
| 403 | `permission_denied` | تنقصك الصلاحية |

---

#### GET /admin/customers/{uuid}/
تفاصيل زبون.

- **الصلاحية:** `customers.view` أو `sales.credit`
- **Response `200`:** كائن `customer`.
- **الأخطاء:** `404 not_found` (غير موجود، أو هو موظف) · `403` · `401`.

---

#### POST /admin/customers/{uuid}/promote/
**يحوّل زبوناً إلى موظف.** يختفي من قائمة الزبائن ويظهر في `GET /admin/users/`، ويصله إشعار واتساب. لا يملك أي صلاحية حتى تُعطى له أدوار (هنا أو لاحقاً عبر `PUT /admin/users/{uuid}/roles/`). **لا يحتاج الموظف لإعادة تسجيل الدخول**: أول `GET /auth/me` يعيد له `user_type: "staff"` وصلاحياته الجديدة.

- **الصلاحية:** `users.create` (+ `users.manage_roles` إن أرسلت `role_ids`، ما لم تكن أدمن)
- يُسجَّل: `staff.promoted`.
- الاتجاه واحد: زبون ← موظف. لإيقاف موظف استعمل `deactivate`.

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `role_ids` | int[] | لا | أدوار أولية. الافتراضي `[]` |

```json
{ "role_ids": [3] }
```

**Response `200`** — كائن `staff` مع `whatsapp_notified`:
```json
{ "uuid": "c28e24e8-6e42-4fa7-babc-15b3b0ca2332", "full_name": "Sara A", "phone": "970591234567",
  "is_active": true, "is_superadmin": false, "last_login": null,
  "created_at": "2026-10-01T21:33:44.913625Z", "roles": ["sales"],
  "whatsapp_notified": true }
```
`whatsapp_notified: false` = تمت الترقية لكن لم تُرسل الرسالة (أخبره يدوياً). فشل الرسالة لا يُلغي الترقية.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `inactive_account` | الحساب معطّل؛ لا يُرقّى |
| 400 | `invalid_role` | دور غير موجود |
| 403 | `cannot_grant` | الأدوار تحوي صلاحيات لا تملكها (`fields.permissions`)؛ لا يتغير شيء |
| 403 | `forbidden` / `permission_denied` | تنقصك `users.create` أو `users.manage_roles` |
| 404 | `not_found` | غير موجود، أو هو موظف أصلاً (لم يعد في قائمة الزبائن) |

---

### ملف الزبون: ديونه وفواتيره ودفعاته

**الدين:** عند البيع بالدين يبقى جزء من الفاتورة (أو كلها) على حساب الزبون (`credit_amount`)، و`balance_due` هو المتبقي منه الآن. **الدفعة** (سند قبض `RCV-000001`) تسدّد **أقدم الفواتير أولاً**، ويُسجَّل لكل فاتورة كم سُدّد منها. القيد: مدين الصندوق (نقداً) أو البنك (بطاقة/تحويل) / دائن **الذمم المدينة** (`receivable`). الدفعة **النقدية** تحتاج وردية مفتوحة لمستلمها وتدخل في نقد ورديته المتوقع.

#### GET /admin/customers/{uuid}/account/
ملخص حساب الزبون.

- **الصلاحية:** `customers.view_balance`

**Response `200`**
```json
{
  "customer": {
    "uuid": "75b23a5c-3e94-4923-9258-c9a0fd67281c",
    "full_name": "Umm Ali",
    "phone": "970599123456",
    "is_active": true,
    "created_at": "2026-10-03T15:46:37.377602+03:00"
  },
  "balance": "7.00",
  "open_invoices": 1,
  "invoices": 2,
  "total_purchases": "30.00",
  "total_paid_off": "18.00"
}
```
`balance` الدين الحالي · `open_invoices` فواتير عليها دين · `invoices` و`total_purchases` الفواتير المكتملة (غير المسترجعة) ومجموعها · `total_paid_off` مجموع دفعاته على الدين.

- **الأخطاء:** `404 not_found` (غير موجود أو موظف) · `403` · `401`.

---

#### GET /admin/customers/{uuid}/invoices/
فواتير الزبون (الأحدث أولاً)، مقسّمة صفحات (24).

- **الصلاحية:** `customers.view_balance`
- **Query:** `open=1` الفواتير التي عليها دين فقط · `page` · `page_size`.

**Response `200`** (عناصر بشكل `GET /admin/pos/sales/`)
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "uuid": "09422533-2507-41ff-b976-bd6e06320358",
      "number": "POS-000002",
      "status": "completed",
      "created_at": "2026-10-03T15:46:37.408331+03:00",
      "cashier": "Staff",
      "shift": "SH-000001",
      "customer": "Umm Ali",
      "items": 1,
      "total": "10.00",
      "credit_amount": "10.00",
      "balance_due": "7.00"
    },
    {
      "uuid": "b6d79983-6905-497b-9928-58295ecda76f",
      "number": "POS-000001",
      "status": "completed",
      "created_at": "2026-10-03T15:46:37.388822+03:00",
      "cashier": "Staff",
      "shift": "SH-000001",
      "customer": "Umm Ali",
      "items": 2,
      "total": "20.00",
      "credit_amount": "15.00",
      "balance_due": "0.00"
    }
  ]
}
```
- **الأخطاء:** `404 not_found` · `403` · `401`.

---

#### GET /admin/customers/{uuid}/payments/
دفعات الزبون على دينه (الأحدث أولاً) ومن استلمها وأي فواتير سدّدت. مقسّمة صفحات (24).

- **الصلاحية:** `customers.view_balance`

**Response `200`**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "uuid": "7b14468e-32e1-4403-bd9c-53ee91d2a540",
      "number": "RCV-000001",
      "created_at": "2026-10-03T15:46:37.425975+03:00",
      "amount": "18.00",
      "method": "card",
      "reference": "slip 7",
      "note": "",
      "received_by": "Admin",
      "invoices": [
        { "uuid": "b6d79983-6905-497b-9928-58295ecda76f", "number": "POS-000001", "amount": "15.00" },
        { "uuid": "09422533-2507-41ff-b976-bd6e06320358", "number": "POS-000002", "amount": "3.00" }
      ]
    }
  ]
}
```
- **الأخطاء:** `404 not_found` · `403` · `401`.

---

#### POST /admin/customers/{uuid}/payments/
**يسجّل دفعة من الزبون على دينه.** تسدّد أقدم الفواتير أولاً، وتُنشئ قيداً مرحَّلاً (مصدره `debt_payment`).

- **الصلاحية:** `payments.record`
- يُسجَّل: `customer.debt_paid`.

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `amount` | decimal | نعم | > 0، ولا يزيد عن الدين |
| `method` | string | نعم | `cash` (يحتاج وردية مفتوحة لك) · `card` · `transfer` |
| `reference` | string | لا | رقم الإيصال/التحويل |
| `note` | string | لا | |

```json
{ "amount": "18", "method": "card", "reference": "slip 7" }
```

**Response `201`:** الدفعة + ملخص الحساب بعدها:
```json
{
  "payment": {
    "uuid": "7b14468e-32e1-4403-bd9c-53ee91d2a540",
    "number": "RCV-000001",
    "created_at": "2026-10-03T15:46:37.425975+03:00",
    "amount": "18.00",
    "method": "card",
    "reference": "slip 7",
    "note": "",
    "received_by": "Admin",
    "invoices": [
      { "uuid": "b6d79983-6905-497b-9928-58295ecda76f", "number": "POS-000001", "amount": "15.00" },
      { "uuid": "09422533-2507-41ff-b976-bd6e06320358", "number": "POS-000002", "amount": "3.00" }
    ]
  },
  "balance": "7.00",
  "open_invoices": 1,
  "invoices": 2,
  "total_purchases": "30.00",
  "total_paid_off": "18.00"
}
```

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `payment_exceeds_debt` | المبلغ أكبر من الدين (`fields`: `amount` `owed`) |
| 400 | `no_debt` | لا دين على الزبون |
| 400 | `shift_required` | دفعة نقدية بلا وردية مفتوحة لك |
| 400 | `validation_error` / `invalid_amount` | مبلغ أو طريقة غير صالحة |
| 400 | `period_closed` / `mapping_missing` | الفترة المحاسبية أو الربط (`cash` `bank` `receivable`) |
| 403 | `permission_denied` | تنقصك `payments.record` |
| 404 | `not_found` | الزبون غير موجود |

---

## 7) العملات وأسعار الصرف

الدفاتر بعملة أساسية واحدة هي **الشيكل (ILS)**، والدولار (USD) والدينار (JOD) عملات معاملات. تُزرع العملات الثلاث بالأمر `python manage.py seed_currencies` (آمن للتكرار). **أسعار الصرف لا تُزرع**: يدخلها الأدمن، ولا يجوز اختراع سعر صرف. العملة الأساسية لا تُغيَّر من الـ API.

التحويل بين العملات (`convert`) يتم داخلياً: يمرّ عبر العملة الأساسية بآخر سعر معرَّف في أو قبل تاريخ المعاملة، ويقرّب إلى منازل العملة الهدف (الشيكل والدولار منزلتان، الدينار ثلاث منازل). غياب السعر يوقف العملية بالخطأ `rate_missing`.

**الصلاحيات:** القراءة تكفيها أي من `accounting.view` أو `products.view` أو `inventory.view`. الكتابة تحتاج `accounting.manage_currencies`.

**كائن `currency`:**
```json
{ "code": "ILS", "name": "Israeli New Shekel", "symbol": "₪", "decimals": 2, "is_base": true, "is_active": true }
```

#### GET /admin/currencies/
قائمة العملات (الأساسية أولاً ثم بحسب الرمز). غير مقسّمة صفحات.

- **الصلاحية:** `accounting.view` أو `products.view` أو `inventory.view`
- **Response `200`:** مصفوفة كائنات `currency`.
```json
[ { "code": "ILS", "name": "Israeli New Shekel", "symbol": "₪", "decimals": 2, "is_base": true, "is_active": true },
  { "code": "JOD", "name": "Jordanian Dinar", "symbol": "JD", "decimals": 3, "is_base": false, "is_active": true },
  { "code": "USD", "name": "US Dollar", "symbol": "$", "decimals": 2, "is_base": false, "is_active": true } ]
```

---

#### POST /admin/currencies/
يضيف عملة جديدة (معاملات فقط).

- **الصلاحية:** `accounting.manage_currencies`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `code` | string | نعم | 3 أحرف ISO 4217، يُحوَّل لأحرف كبيرة (`eur` ← `EUR`)، فريد |
| `name` | string | نعم | حتى 64 حرفاً |
| `symbol` | string | نعم | حتى 8 أحرف |
| `decimals` | int | لا | 0–4، الافتراضي 2 |
| `is_active` | bool | لا | الافتراضي `true` |

`is_base` **للقراءة فقط**: إرساله يُتجاهل، والعملة الجديدة ليست أساسية أبداً.

```json
{ "code": "eur", "name": "Euro", "symbol": "€", "decimals": 2 }
```
**Response `201`:** كائن `currency` (`"code": "EUR"`, `"is_base": false`).

**الأخطاء:** `400 validation_error` (رمز غير صالح/مكرر، منازل خارج 0–4) · `401` · `403 permission_denied`.

---

#### GET /admin/currencies/{code}/
عملة واحدة بالرمز (غير حساس لحالة الأحرف: `usd` تعمل).

- **الصلاحية:** `accounting.view` أو `products.view` أو `inventory.view`
- **Response `200`:** كائن `currency` · **الأخطاء:** `404 not_found` · `403` · `401`.

---

#### PATCH /admin/currencies/{code}/
يعدّل اسم العملة أو رمزها المرئي أو منازلها أو تفعيلها. **رمز العملة لا يتغير.**

- **الصلاحية:** `accounting.manage_currencies`
- **Request body (كلها اختيارية):** `name`, `symbol`, `decimals`, `is_active`.
```json
{ "name": "Euro!" }
```
**Response `200`:** كائن `currency` المحدّث.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `validation_error` | محاولة تغيير `code`، أو تعطيل العملة الأساسية، أو `decimals` خارج النطاق |
| 404 | `not_found` | |

---

#### GET /admin/exchange-rates/
أسعار الصرف المدخلة (الأحدث تاريخاً أولاً)، مقسّمة صفحات (25).

- **الصلاحية:** `accounting.view` أو `products.view` أو `inventory.view`

**Query**
| المعامل | ملاحظات |
|---|---|
| `currency` | رمز العملة (`USD`) |
| `from` / `to` | تاريخ بصيغة `YYYY-MM-DD` (شامل) |
| `page` | رقم الصفحة |

**Response `200`**
```json
{ "count": 1, "next": null, "previous": null,
  "results": [ { "id": 1, "currency": "USD", "date": "2026-10-01", "rate_to_base": "3.70000000" } ] }
```
`rate_to_base` = كم وحدة من العملة الأساسية (شيكل) تساوي وحدة واحدة من العملة.

---

#### POST /admin/exchange-rates/
يسجّل سعر صرف ليوم معيّن. **إن وُجد سعر لنفس العملة والتاريخ يُستبدل (upsert)**: يرجع `201` للجديد و`200` للمُحدَّث. يُسجَّل في سجل التدقيق (`currency.rate_set`).

- **الصلاحية:** `accounting.manage_currencies`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `currency` | string | نعم | رمز عملة فعّالة وليست الأساسية |
| `date` | date | نعم | `YYYY-MM-DD` |
| `rate_to_base` | decimal | نعم | أكبر من صفر، حتى 8 منازل |

```json
{ "currency": "USD", "date": "2026-10-01", "rate_to_base": "3.70" }
```
**Response `201` / `200`:**
```json
{ "id": 1, "currency": "USD", "date": "2026-10-01", "rate_to_base": "3.70000000" }
```
**الأخطاء:** `400 validation_error` (صفر/سالب، عملة غير موجودة أو معطّلة) · `400 base_currency_rate` (العملة الأساسية سعرها 1 دائماً) · `403` · `401`.

---

## 8) المنتجات والكتالوج

### المفاهيم
- **المنتج (Product):** ما يراه الزبون (قميص). **المتغيّر (Variant):** الوحدة التي تُباع وتُخزَّن ولها `sku` (قميص-أزرق-L). الأسعار والمخزون على مستوى المتغيّر.
- **حالة المنتج `status`:** `draft` (مسودة) · `active` (فعّال) · `archived` (مؤرشف).
- **ظهور المنتج للزبائن:** يظهر في المتجر فقط إذا كان `status = active` **و** `is_visible_on_store = true` **و** له متغيّر فعّال وله سعر بالعملة المطلوبة. أي شرط ناقص يُخفيه فوراً (انظر القسم 10).

| الإجراء | الأثر |
|---|---|
| `publish` | `status = active` (يحتاج متغيّراً فعّالاً واحداً على الأقل؛ لا يغيّر الظهور) |
| `show` / `hide` | يضبط `is_visible_on_store` فقط (إخفاء مؤقت دون أرشفة) |
| `archive` | `status = archived` ويُخفى عن المتجر |
| `DELETE` | حذف ناعم (soft). **ممنوع** إن كان للمنتج حركات مخزون أو كمية؛ استعمل الأرشفة |

- **التكلفة `cost_price`:** متوسط مرجّح بالعملة الأساسية، يتحدث تلقائياً مع كل إدخال بضاعة بتكلفة (افتتاحي/تسوية زيادة/مشتريات). **يمكن إدخالها يدوياً** (عند إنشاء المتغيّر أو تعديله) لمن يملك **`products.manage_cost`**، وكل تغيير يدوي يُسجَّل في التدقيق `variant.cost_changed` (القديم والجديد). التكلفة المُدخلة يدوياً هي ما تستعمله المبيعات (تكلفة البضاعة) من لحظتها، ولا تُنشئ قيداً محاسبياً لقيمة المخزون الحالي. **تظهر فقط لمن يملك `inventory.view_cost`**، وغيرهم يرون `null`.
- **`sku`** فريد في النظام بغض النظر عن حالة الأحرف، ولا يُعاد استعماله حتى بعد حذف المتغيّر، ولا يتغير بعد وجود حركة مخزون. الـ `barcode` فريد إن وُجد.
- **قوائم الأسعار (PriceList):** كل قائمة بعملة واحدة. **لكل عملة قائمة افتراضية واحدة** هي التي يقرأ منها المتجر. السعر محفوظ لكل متغيّر وقائمة.
- لا يوجد `PUT` على هذه الموارد: التعديل بـ `PATCH`.

### كائنات الرد

**`category`:**
```json
{ "uuid": "e83348a8-66c2-4b6d-ae85-c952a9e9200b", "name": "Shirts", "name_ar": "قمصان", "name_en": "", "name_he": "חולצות",
  "slug": "shirts", "image": "http://127.0.0.1:8000/media/categories/shirts-091122123456.webp", "parent": null, "sort_order": 0, "is_active": true }
```
`parent`: uuid التصنيف الأب أو `null`. الـ `slug` يُولَّد من الاسم تلقائياً. `image`: رابط صورة التصنيف أو `null`؛ **للقراءة فقط** وتُرفع بـ `POST /admin/categories/{uuid}/image/`.

**`price_list`:**
```json
{ "uuid": "c7402064-f696-460f-ae2b-a2ef1867dce5", "name": "Retail ILS", "currency": "ILS",
  "is_default": true, "is_active": true }
```

**`variant`:**
```json
{
  "uuid": "2007ed8a-7ba7-44c7-a927-f05cfdd4b87f", "sku": "SH-BL-L", "barcode": "", "name": "Blue / L",
  "attributes": { "color": "blue", "size": "L" }, "weight": null, "reorder_level": "5.000",
  "is_active": true, "cost_price": "12.5000",
  "prices": [ { "price_list": "c7402064-f696-460f-ae2b-a2ef1867dce5", "price_list_name": "Retail ILS",
                "currency": "ILS", "price": "26.5000", "compare_at_price": "30.0000" } ],
  "stock": { "on_hand": "20.000", "reserved": "0.000", "available": "20.000" }
}
```
`stock`: مجموع كل المخازن (`available = on_hand − reserved`). `cost_price`: `null` لمن لا يملك `inventory.view_cost`.

**`product` (عنصر القائمة):**
```json
{ "uuid": "f6cba954-ba00-4be3-9c7d-3a9f164c7e7f", "name": "Blue Shirt", "slug": "blue-shirt",
  "category": { "uuid": "e83348a8-…", "name": "Shirts" }, "brand": { "uuid": "9b1f0c3e-…", "name": "Acme" },
  "status": "active", "is_visible_on_store": true, "track_inventory": true,
  "variants_count": 2, "available": "20.000",
  "primary_image": "http://127.0.0.1:8000/media/products/2026/10/blue-shirt-215401667891.png",
  "created_at": "2026-10-01T21:54:01.509566Z" }
```
`available`: مجموع المتاح لكل المتغيّرات، و`null` إن كان المنتج لا يتتبع المخزون. `category` و`brand` و`primary_image` قد تكون `null`. `brand`: `{uuid, name}` من قائمة الماركات.

**`product` (التفاصيل):** نفس عنصر القائمة (فيه `name` الافتراضي) + ترجمات الاسم والوصف والحقول:
```json
{ "name_ar": "قميص أزرق", "name_en": "", "name_he": "", "description": "Cotton shirt",
  "description_ar": "", "description_en": "", "description_he": "",
  "variants": [ { …variant }, { …variant } ],
  "images": [ { "id": 1, "url": "http://127.0.0.1:8000/media/products/2026/10/blue-shirt-215401667891.png",
                "alt": "front", "sort_order": 0, "is_primary": true, "variant": null } ] }
```

---

### التصنيفات

**الصلاحيات:** القراءة `categories.view`، والكتابة `categories.manage`.

#### GET /admin/categories/
قائمة التصنيفات (حسب `sort_order` ثم الاسم)، مقسّمة صفحات (25).

- **الصلاحية:** `categories.view`
- **Response `200`:** `{ "count", "next", "previous", "results": [ {category} ] }`.

---

#### POST /admin/categories/
- **الصلاحية:** `categories.manage`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `name` | string | نعم | الاسم **الافتراضي** (وبديل أي لغة بلا ترجمة)، حتى 120 حرفاً. يولّد `slug` فريداً تلقائياً |
| `name_ar` · `name_en` · `name_he` | string | لا | الاسم بالعربية/الإنجليزية/العبرية (حتى 120). فارغ = يُستعمل `name` |
| `parent` | uuid | لا | تصنيف أب، أو `null` |
| `sort_order` | int | لا | الافتراضي 0 |
| `is_active` | bool | لا | الافتراضي `true` |

```json
{ "name": "Shirts", "parent": null }
```
**Response `201`:** كائن `category`. **الأخطاء:** `400 validation_error` · `403` · `401`.

---

#### GET /admin/categories/{uuid}/
- **الصلاحية:** `categories.view` · **Response `200`:** كائن `category` · **الأخطاء:** `404`.

---

#### POST /admin/categories/{uuid}/image/
يرفع صورة التصنيف أو يستبدلها (الملف القديم يُحذف). تظهر للزبائن في بطاقة التصنيف (`image` في `GET /store/categories/`).

- **الصلاحية:** `categories.manage`
- **Request:** `multipart/form-data` فيه الحقل `image` (JPEG أو PNG أو WebP، بنفس شروط صور المنتجات).
- **المعالجة:** تُقصّ من المنتصف إلى **مربع** وتُصغَّر إلى 800×800 كحد أقصى (ولا تُكبَّر)، وتُحفظ WebP، لأنها تُعرض في إطار دائري.
- **Response `200`:** كائن `category` المحدَّث.
- **الأخطاء:** `400 invalid_image` · `400 image_too_large` · `400 validation_error` (بلا `image`) · `403` · `401` · `404`.

---

#### DELETE /admin/categories/{uuid}/image/
يحذف صورة التصنيف (والملف). لا خطأ إن لم تكن له صورة.

- **الصلاحية:** `categories.manage` · **Response `204`** · **الأخطاء:** `403` · `401` · `404`.

---

#### PATCH /admin/categories/{uuid}/
- **الصلاحية:** `categories.manage`
- **Request body (كلها اختيارية):** `name` (يغيّر الـ slug), `parent`, `sort_order`, `is_active`.

**Response `200`:** كائن `category`.
**الأخطاء:** `400 category_cycle` (محاولة جعل التصنيف تحت أحد أبنائه) · `400 validation_error` · `404` · `403`.

---

#### DELETE /admin/categories/{uuid}/
حذف فعلي. مرفوض إن كان له تصنيفات فرعية أو منتجات.

- **الصلاحية:** `categories.manage` · **Response `204`** بدون محتوى.
- **الأخطاء:** `400 category_in_use` (انقل منتجاته وفروعه أولاً، أو عطّله بـ `is_active: false`) · `404` · `403`.

---

### الماركات

الماركة قائمة تُدار من الإدارة (بدل نص حر)، وتُربط بالمنتج عبر `brand` (uuid). اسم الماركة **فريد دون اعتبار حالة الأحرف**. واجهة المتجر العامة تعرض `brand` كائناً `{name, slug}` (`null` إن لم يكن للمنتج ماركة) بلغة `?lang=`. **الصلاحيات:** القراءة `brands.view`، والكتابة `brands.manage` (دور `store_manager` يملكهما).

**`brand`:**
```json
{ "uuid": "9b1f0c3e-3a63-4d7e-8b0e-1f8c0f7a2d11", "name": "Acme", "name_ar": "أكمي", "name_en": "", "name_he": "אקמי",
  "slug": "acme", "logo": "http://127.0.0.1:8000/media/brands/acme-091122123456.png", "is_active": true, "products_count": 3 }
```
`products_count`: عدد المنتجات غير المحذوفة المرتبطة بها. `logo`: رابط صورة الماركة أو `null`؛ **للقراءة فقط** وتُرفع بـ `POST /admin/brands/{uuid}/logo/`.

#### GET /admin/brands/
كل الماركات مرتبة بالاسم (غير مقسّمة صفحات).

- **الصلاحية:** `brands.view`
- **Query:** `search` (جزء من الاسم) · `active` (`true`/`false`).
- **Response `200`:** مصفوفة كائنات `brand`.

---

#### POST /admin/brands/
- **الصلاحية:** `brands.manage`
- **Request body:** `{ "name": "Acme" }` (`name` مطلوب حتى 120 حرفاً ويُقصّ من الفراغات؛ هو الاسم الافتراضي) · `name_ar` / `name_en` / `name_he` اختيارية (ترجمات؛ فارغ = الافتراضي) · `is_active` اختياري (الافتراضي `true`). يُولَّد `slug` للماركة مرة واحدة عند الإنشاء ولا يتغيّر بعدها (حتى لو أُعيدت تسميتها)، وهو **للقراءة فقط**.
- **Response `201`:** كائن `brand`.
- **الأخطاء:** `400 brand_taken` (الاسم مستخدم، `fields.name`) · `400 validation_error` · `403` · `401`.

---

#### GET /admin/brands/{uuid}/
- **الصلاحية:** `brands.view` · **Response `200`:** كائن `brand` · **الأخطاء:** `404`.

---

#### POST /admin/brands/{uuid}/logo/
يرفع صورة الماركة أو يستبدلها (الملف القديم يُحذف). تظهر للزبائن في صفحة الماركات (`logo` في `GET /store/brands/`).

- **الصلاحية:** `brands.manage`
- **Request:** `multipart/form-data` فيه الحقل `image` (JPEG أو PNG أو WebP، بنفس شروط صور المنتجات: حتى الحد الأقصى للحجم، وفحص أن الملف صورة حقيقية).
- **المعالجة:** تُحفظ كل صورة ماركة **WebP على لوحة شفافة بنسبة 3:2** (الشفافية محفوظة) (تُوسَّط دون قص أو تمطيط، وتُصغَّر إن زادت عن 960×640 ولا تُكبَّر)، فتظهر كل الماركات في المتجر بحجم واحد.
- **Response `200`:** كائن `brand` المحدَّث (`logo` رابط مطلق).
- **الأخطاء:** `400 invalid_image` (ليس صورة أو صيغة غير مسموحة) · `400 image_too_large` · `400 validation_error` (بلا `image`) · `403` · `401` · `404`.

---

#### DELETE /admin/brands/{uuid}/logo/
يحذف صورة الماركة (والملف). لا خطأ إن لم تكن لها صورة.

- **الصلاحية:** `brands.manage` · **Response `204`** · **الأخطاء:** `403` · `401` · `404`.

---

#### PATCH /admin/brands/{uuid}/
- **الصلاحية:** `brands.manage` · **Request body (اختيارية):** `name`, `is_active` · **Response `200`:** كائن `brand`.
- **الأخطاء:** `400 brand_taken` · `400 validation_error` · `404` · `403`.
- تعطيل الماركة لا يفصلها عن منتجاتها.

---

#### DELETE /admin/brands/{uuid}/
حذف فعلي. **مرفوض إن كان لها منتجات** (انقل منتجاتها لماركة أخرى، أو عطّلها بدل الحذف).

- **الصلاحية:** `brands.manage` · **Response `204`** بدون محتوى.
- **الأخطاء:** `400 brand_in_use` · `404` · `403`.

---

### قوائم الأسعار

**الصلاحيات:** القراءة `products.view`، والكتابة `products.manage_prices`.

#### GET /admin/price-lists/
كل قوائم الأسعار (غير مقسّمة): **Response `200`:** مصفوفة كائنات `price_list`. **الصلاحية:** `products.view`.

---

#### POST /admin/price-lists/
- **الصلاحية:** `products.manage_prices`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `name` | string | نعم | فريد |
| `currency` | string | نعم | رمز عملة فعّالة |
| `is_default` | bool | لا | عند `true` تصبح هي الافتراضية لعملتها وتُلغى الافتراضية السابقة تلقائياً |
| `is_active` | bool | لا | الافتراضي `true` |

```json
{ "name": "Retail ILS", "currency": "ILS", "is_default": true }
```
**Response `201`:** كائن `price_list`. **الأخطاء:** `400 validation_error` (اسم مكرر، عملة غير موجودة/معطّلة) · `403` · `401`.

---

#### GET /admin/price-lists/{uuid}/
- **الصلاحية:** `products.view` · **Response `200`:** كائن `price_list` · **الأخطاء:** `404`.

---

#### PATCH /admin/price-lists/{uuid}/
- **الصلاحية:** `products.manage_prices`
- **Request body (اختيارية):** `name`, `currency`, `is_default`, `is_active`.

**Response `200`:** كائن `price_list`.
**الأخطاء:** `400 currency_locked` (لا تغيّر عملة قائمة عليها أسعار) · `400 validation_error` · `404` · `403`.

---

### ملف المتجر (من نحن)

محتوى الصفحة العامة «من نحن»: نبذة وعنوان وساعات عمل (لكل منها نص افتراضي + ترجمة عربي/إنجليزي/عبري اختيارية، والفارغة تعود للافتراضي)، رقم الهاتف وواتساب والبريد، موقع المحل، وحسابات التواصل الاجتماعي. **سجل واحد فقط**. **الصلاحيات:** القراءة `storefront.view`، والتعديل `storefront.manage` (دور `store_manager` يملكهما).

#### GET /admin/store-profile/
- **الصلاحية:** `storefront.view`
- **Response `200`:** كائن الملف كاملاً بكل اللغات، مثل:
```json
{ "about": "نسهّل حياتك اليومية", "about_ar": "", "about_en": "Making everyday life easier", "about_he": "",
  "address": "شارع الإرسال، رام الله", "address_ar": "", "address_en": "", "address_he": "",
  "hours": "السبت–الخميس 9:00–18:00", "hours_ar": "", "hours_en": "", "hours_he": "",
  "phone": "0535746112", "whatsapp": "972535746112", "email": "hi@easylife.example",
  "latitude": "31.903400", "longitude": "35.203400", "map_url": "https://maps.google.com/?q=31.9034,35.2034",
  "social_links": [ { "platform": "instagram", "url": "https://instagram.com/easylife", "label": "@easylife", "sort_order": 0, "is_active": true } ],
  "updated_at": "2026-10-03T08:20:11.402113Z" }
```
- **الأخطاء:** `401` · `403`.

---

#### PATCH /admin/store-profile/
تعديل جزئي: أرسل الحقول المراد تغييرها فقط.

- **الصلاحية:** `storefront.manage`
- **Request body (كلها اختيارية)**

| الحقل | النوع | ملاحظات |
|---|---|---|
| `about` · `about_ar` · `about_en` · `about_he` | string | النبذة (الافتراضي + ترجماتها) |
| `address` · `address_ar` · `address_en` · `address_he` | string | حتى 300 حرف |
| `hours` · `hours_ar` · `hours_en` · `hours_he` | string | حتى 200 حرف |
| `phone` | string | أرقام ومسافات و `+ - ( )` فقط |
| `whatsapp` | string | يُقبل بأي تنسيق ويُحفظ أرقاماً فقط (`+972 53-574-6112` ← `972535746112`)، من 8 إلى 15 رقماً |
| `email` | string | بريد صالح |
| `latitude` · `longitude` | decimal | خط العرض (−90..90) والطول (−180..180). **معاً أو لا شيء** (`null` للمسح) |
| `map_url` | string | رابط خريطة `http(s)` |
| `social_links` | array | **تستبدل القائمة كلها** إن أُرسلت (أقصى 20)؛ غيابها يترك الحالية. عنصر: `platform` (انظر القيم أعلاه) · `url` (`http(s)` فقط) · `label` اختياري (حتى 60) · `sort_order` (الافتراضي ترتيب الإرسال) · `is_active` (الافتراضي `true`) |

```json
{ "address": "شارع الإرسال، رام الله", "latitude": "31.9034", "longitude": "35.2034",
  "social_links": [ { "platform": "instagram", "url": "https://instagram.com/easylife", "label": "@easylife" } ] }
```
- **Response `200`:** الملف المحدَّث (كما في `GET`). كل تعديل يُسجَّل في التدقيق `store_profile.updated`.
- **الأخطاء:** `400 validation_error` (رابط غير `http(s)` مثل `javascript:` أو `ftp:`، منصة غير معروفة، إحداثيات ناقصة أو خارج المدى، واتساب غير صالح) · `401` · `403`.

---

### المنتجات

#### GET /admin/products/
قائمة المنتجات غير المحذوفة (الأحدث أولاً)، مقسّمة صفحات (25).

- **الصلاحية:** `products.view`

**Query**
| المعامل | ملاحظات |
|---|---|
| `status` | `draft` / `active` / `archived` |
| `category` | uuid التصنيف |
| `visible` | `true` / `false` (ظهور المتجر) |
| `search` | يبحث في الاسم واسم الماركة و`sku` و`barcode` |
| `low_stock` | `true`: منتجات فيها متغيّر فعّال متاحه ≤ `reorder_level` (و`reorder_level > 0`) |
| `page` | رقم الصفحة |

**Response `200`:** `{ "count", "next", "previous", "results": [ {product list item} ] }`.

---

#### POST /admin/products/
ينشئ منتجاً مع متغيّراته وأسعاره في عملية واحدة (إما كلها أو لا شيء). ينشأ `draft` وغير ظاهر في المتجر.

- **الصلاحية:** `products.create`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `name` | string | نعم | الاسم **الافتراضي** (وبديل أي لغة بلا ترجمة)، حتى 200 حرف. يولّد `slug` فريداً |
| `name_ar` · `name_en` · `name_he` | string | لا | الاسم بكل لغة (فارغ = `name`) |
| `description` | string | لا | الوصف الافتراضي |
| `description_ar` · `description_en` · `description_he` | string | لا | الوصف بكل لغة (فارغ = `description`) |
| `category` | uuid | لا | |
| `brand` | uuid | لا | ماركة من [قائمة الماركات](#الماركات)؛ `null` لإزالتها |
| `track_inventory` | bool | لا | الافتراضي `true`. `false` للخدمات/المنتجات الرقمية |
| `variants` | array | لا | المتغيّرات (انظر التالي) |

**عنصر `variants[]`**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `sku` | string | نعم | فريد (غير حساس لحالة الأحرف) |
| `barcode` | string | لا | فريد إن وُجد |
| `name` | string | لا | مثل `Blue / L` |
| `attributes` | object | لا | مثل `{"color":"blue","size":"L"}` |
| `weight` | decimal | لا | |
| `reorder_level` | decimal | لا | حد التنبيه (الافتراضي 0 = بلا تنبيه) |
| `cost_price` | decimal | لا | تكلفة الوحدة بالعملة الأساسية (≥ 0، حتى 4 منازل). **تحتاج `products.manage_cost`** وإلا `403 forbidden` |
| `is_active` | bool | لا | الافتراضي `true` |
| `prices` | array | لا | `[{"price_list": uuid, "price": "25.00", "compare_at_price": "30.00"?}]` |

```json
{ "name": "Blue Shirt", "description": "Cotton shirt", "brand": "9b1f0c3e-…",
  "variants": [ { "sku": "SH-BL-L", "name": "Blue / L", "attributes": {"color": "blue", "size": "L"},
                  "reorder_level": "5",
                  "prices": [ { "price_list": "c7402064-f696-460f-ae2b-a2ef1867dce5", "price": "25.00" } ] } ] }
```
**Response `201`:** كائن `product` (التفاصيل) بحالة `draft`، `is_visible_on_store: false`، ومخزون صفر.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `validation_error` | حقل مفقود، سعر سالب، قائمة أسعار غير موجودة |
| 400 | `sku_taken` | SKU مستخدم أو مكرر داخل الطلب (يُلغى الطلب كله) |
| 400 | `barcode_taken` | الباركود مستخدم |
| 400 | `duplicate_price_list` / `invalid_compare_price` / `price_list_inactive` | خطأ في الأسعار |
| 403 | `permission_denied` | |

---

#### GET /admin/products/{uuid}/
- **الصلاحية:** `products.view` · **Response `200`:** كائن `product` (التفاصيل) بمتغيّراته الحية فقط · **الأخطاء:** `404` (غير موجود أو محذوف).

---

#### PATCH /admin/products/{uuid}/
يعدّل بيانات المنتج. **الحالة والظهور لا تُعدَّل هنا** (استعمل `publish` و`archive` و`show` و`hide`).

- **الصلاحية:** `products.update`
- **Request body (اختيارية):** `name` (يغيّر الـ slug), `description`, `category`, `brand`, `track_inventory`.

**Response `200`:** كائن `product` (التفاصيل).
**الأخطاء:** `400 tracking_locked` (لا يمكن إطفاء تتبع المخزون بعد وجود حركات) · `400 product_deleted` · `400 validation_error` · `404` · `403`.

---

#### DELETE /admin/products/{uuid}/
حذف ناعم: يختفي من النظام والمتجر. **مرفوض إن كان عليه تاريخ مخزون.**

- **الصلاحية:** `products.delete` · **Response `204`** بدون محتوى.
- **الأخطاء:** `400 product_in_use` (استعمل `archive`) · `404` · `403`.

---

#### POST /admin/products/{uuid}/publish/
يجعل المنتج `active` (يمكن أيضاً إعادة تفعيل مؤرشف). لا يغيّر `is_visible_on_store`.

- **الصلاحية:** `products.publish` · **Request:** بدون body · **Response `200`:** كائن `product` (التفاصيل).
- **الأخطاء:** `400 no_variants` (أضف متغيّراً فعّالاً أولاً) · `404` · `403`.

---

#### POST /admin/products/{uuid}/archive/
`status = archived` ويُخفى عن المتجر.

- **الصلاحية:** `products.archive` · **Request:** بدون body · **Response `200`:** كائن `product` · **الأخطاء:** `404` · `403`.

---

#### POST /admin/products/{uuid}/show/
يضبط `is_visible_on_store = true`. لا يظهر المنتج للزبائن إلا إذا كان `active` وله سعر (انظر القسم 10).

- **الصلاحية:** `products.toggle_visibility` · **Request:** بدون body · **Response `200`:** كائن `product` · **الأخطاء:** `404` · `403`.

---

#### POST /admin/products/{uuid}/hide/
يضبط `is_visible_on_store = false`: **يختفي عن الزبائن فوراً** دون أرشفة (يبقى في النظام وبياناته).

- **الصلاحية:** `products.toggle_visibility` · **Request:** بدون body · **Response `200`:** كائن `product` · **الأخطاء:** `404` · `403`.

---

#### POST /admin/products/bulk/
إجراء على عدة منتجات دفعة واحدة (كلها أو لا شيء).

- **الصلاحية:** `products.toggle_visibility` (و`products.archive` إضافة لإجراء `archive`)

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `ids` | uuid[] | نعم | من 1 إلى 200 |
| `action` | string | نعم | `show` · `hide` · `archive` |

```json
{ "ids": ["f6cba954-ba00-4be3-9c7d-3a9f164c7e7f"], "action": "hide" }
```
**Response `200`:** `{ "action": "hide", "updated": 1 }`

**الأخطاء:** `404 unknown_products` (بعض الـ ids غير موجودة؛ مذكورة في `fields.ids` ولا يتغير شيء) · `400 validation_error` (إجراء غير معروف) · `403 forbidden` (`archive` بدون `products.archive`) · `403 permission_denied`.

---

#### GET /admin/products/import/template/
يُنزّل قالب Excel (`.xlsx`) الجاهز لاستيراد المنتجات، مبنياً من بيانات النظام الحالية.

- **الصلاحية:** `products.import`
- **Response `200`:** ملف `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (`Content-Disposition: attachment; filename="products-import-template.xlsx"`)

أوراق القالب (الرؤوس بالعربية؛ العمود المطلوب يحمل `*`). **كل بيانات المنتجات في ورقة واحدة هي `المنتجات`؛ باقي الأوراق للمساعدة فقط ولا تُقرأ عند الاستيراد:**

| الورقة | محتواها |
|---|---|
| `تعليمات` | خطوات الاستيراد، وشرح كل عمود (مطلوب/اختياري، القاعدة، مثال)، والحدود |
| `المنتجات` | **صف لكل متغيّر (صنف) في مخزن واحد.** الأعمدة: `مرجع المنتج` · `اسم المنتج *` · `الاسم بالعربية/الإنجليزية/العبرية` · `الوصف` (+3 لغات) · `الماركة` · `القسم` · `نشر للزبائن` · `تتبع المخزون` · `الصور` · `رمز الصنف (SKU) *` · `الباركود` · `اسم المتغيّر` · `الخصائص` · `الوزن` · `حد إعادة الطلب` · `فعّال` · `المخزن` · `الكمية` · `تكلفة الوحدة` — ثم **عمودان لكل قائمة أسعار فعّالة**: `السعر - <اسم القائمة>` و`السعر قبل الخصم - <اسم القائمة>` |
| `القوائم` | الماركات والأقسام (بمسارها `الأب > الابن`) والمخازن وقوائم الأسعار الموجودة الآن، وهي مصدر القوائم المنسدلة في الأعمدة المقابلة |

**قواعد الملء:** الصفوف التي لها نفس `مرجع المنتج` منتج واحد بعدة متغيّرات (حقول المنتج تُكتب في أول صف فقط، وأي قيمة مختلفة في صف لاحق خطأ)؛ بدون مرجع كل صف منتج مستقل. **الماركة والقسم والمخزن وقائمة السعر تُكتب بأسمائها** (الماركة والقسم يُقبل اسمهما بأي لغة مسجّلة، والقسم الفرعي بمساره) ولا تُنشأ سجلات جديدة. `نعم`/`لا` للقيم المنطقية. **الصور توضع داخل ملف Excel نفسه في عمود `الصور`** (الصق الصورة فوق خلية العمود في صف المنتج، أو «وضع في الخلية» في Excel 365)؛ لا يوجد رفع منفصل للصور ولا تُقبل أسماء ملفات.

**الأخطاء:** `403` · `401`.

---

#### POST /admin/products/import/
يستورد منتجات جديدة من ملف Excel بقالب `GET /admin/products/import/template/` (الصور مضمّنة داخله). **كله أو لا شيء:** إن وُجد أي خطأ لا يُحفظ شيء. يضيف منتجات جديدة فقط ولا يعدّل الموجود (SKU موجود = خطأ). يُنصح بتشغيله أولاً بـ `dry_run=true` للفحص بدون حفظ.

- **الصلاحية:** `products.import`. إن مُلئت أعمدة `المخزن`/`الكمية`/`تكلفة الوحدة` في أي صف يلزم أيضاً `inventory.adjust` (وإلا `403 forbidden`).
- **النوع:** `multipart/form-data`

**Request**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `file` | ملف `.xlsx` | نعم | حتى 60 MB (يشمل الصور المضمّنة) وحتى 2000 صف في الورقة |
| `dry_run` | bool | لا (`false`) | `true` = فحص فقط، لا يُكتب شيء |

**الصور المضمّنة:** تُقرأ الصور العائمة المثبّتة على خلية (إدراج ← صورة، أو لصق) وكذلك صور «وضع في الخلية» (Excel 365). تُنسب الصورة إلى منتج الصف الموجودة فيه، وفي منتج بعدة متغيّرات تُحسب صور أي صف من صفوفه بترتيب الصفوف، والأولى هي الرئيسية. حتى 10 صور للمنتج وحتى 5 MB للصورة (JPG/PNG/WebP). الصورة خارج عمود `الصور` أو في صف بلا SKU، وكذلك **اسم ملف مكتوب في العمود** بدل الصورة، كلها **أخطاء** وليست تُتجاهل بصمت.

**ما يتم عند الاستيراد (في معاملة واحدة):** يُنشأ كل منتج بمتغيّراته وأسعاره، والصور تُحوَّل إلى WebP، و`نشر للزبائن = نعم` ينشر المنتج ويُظهره (وإلا يبقى مسودة مخفية)، وأعمدة `المخزن`/`الكمية`/`تكلفة الوحدة` تُسجَّل كرصيد افتتاحي للصنف في المخزن (تضبط تكلفة الصنف بتكلفة الوحدة). الأعمدة الثلاثة تُملأ معاً أو تُترك فارغة؛ والمنتج الذي لا يتتبع المخزون لا يقبل كمية. صنف لمخزن واحد في الملف (للمزيد من المخازن استعمل صفحة الجرد). ورقة قديمة اسمها `المخزون` تُتجاهل مع تحذير. عند أي فشل يُحذف كل ما كُتب بما فيه ملفات الصور.

**Response `200` (فحص `dry_run=true`) — صالح**
```json
{ "dry_run": true, "valid": true,
  "summary": { "products": 1, "variants": 1, "images": 0, "stock_lines": 0 },
  "errors": [], "errors_total": 0,
  "warnings": [ { "sheet": "المنتجات", "row": 2, "column": "", "code": "no_image", "detail": "This product has no picture.", "value": "Robot" } ],
  "warnings_total": 1 }
```
**Response `200` (فحص) — فيه أخطاء:** نفس الشكل مع `"valid": false` وقائمة `errors`:
```json
{ "dry_run": true, "valid": false,
  "summary": { "products": 2, "variants": 2, "images": 0, "stock_lines": 0 },
  "errors": [
    { "sheet": "المنتجات", "row": 2, "column": "السعر - Retail ILS", "code": "negative_number", "detail": "The value cannot be negative.", "value": "-3" },
    { "sheet": "المنتجات", "row": 2, "column": "الماركة", "code": "unknown_brand", "detail": "There is no brand named 'Nope'. Add it first or fix the name.", "value": "Nope" },
    { "sheet": "المنتجات", "row": 3, "column": "رمز الصنف (SKU)", "code": "duplicate_sku_in_file", "detail": "The SKU 'A1' is repeated (also in row 2).", "value": "A1" } ],
  "errors_total": 3, "warnings": [], "warnings_total": 0 }
```
**Response `201` (استيراد فعلي ناجح):** نفس شكل الفحص بـ `"dry_run": false, "valid": true`؛ `summary` بما أُنشئ.

**`errors` / `warnings`:** كل عنصر `{ sheet, row, column, code, detail, value }` (`row` رقم الصف في Excel). يُرجَع حتى 200 عنصر من كل نوع، و`errors_total`/`warnings_total` بالعدد الكلي. **التحذيرات لا تمنع الاستيراد:** `no_image` (بلا صورة) · `published_without_price` و`published_without_stock` (منتج منشور لن يراه الزبائن حتى يُسعَّر ويدخل له مخزون) · `unknown_column` (عمود غير معروف يُتجاهل) · `stock_sheet_ignored` (ورقة `المخزون` القديمة لا تُقرأ؛ ضع بياناتها في ورقة `المنتجات`) · `in_cell_pictures_unreadable` (تعذّرت قراءة صور «وضع في الخلية»؛ الصقها فوق الخلايا بدلاً من ذلك).

**أكواد الأخطاء على مستوى الصف (`errors[].code`):** `required` · `too_long` · `invalid_number` · `negative_number` · `zero_not_allowed` · `too_many_decimals` · `number_too_big` · `invalid_boolean` · `invalid_attributes` · `duplicate_sku_in_file` · `sku_taken` · `duplicate_barcode_in_file` · `barcode_taken` · `product_field_mismatch` · `unknown_brand` · `ambiguous_brand` · `brand_inactive` · `unknown_category` · `ambiguous_category` · `category_inactive` · `unknown_price_list` · `price_list_inactive` · `compare_without_price` · `invalid_compare_price` · `no_active_variant` · `too_many_images` · `image_not_embedded` (اسم ملف مكتوب في عمود `الصور`) · `image_too_large` · `invalid_image` · `image_wrong_column` (صورة مضمّنة خارج عمود `الصور`) · `image_without_row` (صورة في صف بلا SKU أو في صف العناوين) · `image_unplaced` (صورة غير مثبّتة على خلية) · `not_tracked` · `unknown_warehouse` · `ambiguous_warehouse` · `warehouse_inactive` · `missing_header` · `missing_column` · `duplicate_column` · `empty_file`.

**الأخطاء العامة:**
- `400 import_invalid` — **استيراد فعلي** وفي الملف أخطاء (لم يُحفظ شيء). الجسم: `{ code, detail, dry_run, valid: false, summary, errors, errors_total, warnings, warnings_total }`
- `400 invalid_file` (ليس `.xlsx` سليماً) · `400 file_too_large` · `400 too_many_rows` · `400 missing_sheet` (لا توجد ورقة `المنتجات`) · `400 validation_error` (`file` مفقود)
- `403 forbidden` — أسطر مخزون بلا `inventory.adjust` · `403`/`401` بلا الصلاحية/التوثيق

---

#### GET /admin/products/{uuid}/variants/
متغيّرات المنتج الحية.

- **الصلاحية:** `products.view` · **Response `200`:** مصفوفة كائنات `variant` · **الأخطاء:** `404` · `403`.

---

#### POST /admin/products/{uuid}/variants/
يضيف متغيّراً إلى منتج موجود.

- **الصلاحية:** `products.update`
- **Request body:** نفس عنصر `variants[]` في إنشاء المنتج (`sku` مطلوب، والباقي اختياري بما فيه `prices`).
```json
{ "sku": "SH-BL-M", "name": "Blue / M", "attributes": { "size": "M" } }
```
**Response `201`:** كائن `variant`.
**الأخطاء:** `400 sku_taken` / `barcode_taken` · `400 product_deleted` · `400 validation_error` · `404` · `403`.

---

#### POST /admin/products/{uuid}/images/
يرفع صورة للمنتج. **الطلب `multipart/form-data` وليس JSON.**

- **الصلاحية:** `products.update`
- أول صورة تصبح الرئيسية تلقائياً. الصيغ المسموحة للرفع: JPEG وPNG وWebP، الحد الأقصى 5 ميغابايت، ويُتحقق من محتوى الملف الفعلي لا من امتداده. **كل صورة تُخزَّن WebP دائماً** (تُحوَّل عند الرفع، مع تطبيق دوران EXIF والحفاظ على الشفافية)، فينتهي `url` في الرد بـ `.webp` أياً كانت صيغة الملف المرفوع.

**Form fields**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `image` | file | نعم | |
| `alt` | string | لا | نص بديل |
| `is_primary` | bool | لا | يجعلها الرئيسية ويُلغي السابقة |
| `variant` | uuid | لا | ربط الصورة بمتغيّر من نفس المنتج |

**Response `201`:**
```json
{ "id": 1, "url": "http://127.0.0.1:8000/media/products/2026/10/blue-shirt-215401667891.png",
  "alt": "front", "sort_order": 0, "is_primary": true, "variant": null }
```
**الأخطاء:** `400 invalid_image` (ليس صورة صالحة أو صيغة غير مسموحة) · `400 image_too_large` · `400 variant_mismatch` · `400 validation_error` · `404` · `403`.

---

### المتغيّرات والأسعار

#### PATCH /admin/variants/{uuid}/
يعدّل متغيّراً. **الأسعار لها endpoint خاص** (`PUT .../prices/`) ويُتجاهل حقل `prices` هنا.

- **الصلاحية:** `products.update`
- **Request body (اختيارية):** `sku`, `barcode`, `name`, `attributes`, `weight`, `reorder_level`, `is_active`, `cost_price` (تحتاج `products.manage_cost`).

**Response `200`:** كائن `variant`.
**الأخطاء:** `400 sku_taken` / `barcode_taken` · `400 sku_locked` (لا يتغير الـ SKU بعد وجود حركة مخزون) · `400 validation_error` · `404` · `403`.

---

#### DELETE /admin/variants/{uuid}/
حذف ناعم لمتغيّر (لا يُعاد استعمال SKU).

- **الصلاحية:** `products.delete` · **Response `204`** بدون محتوى.
- **الأخطاء:** `400 variant_in_use` (له تاريخ مخزون؛ عطّله بـ `is_active: false`) · `400 last_variant` (آخر متغيّر فعّال في منتج `active`؛ أرشف المنتج بدلاً) · `404` · `403`.

---

#### PUT /admin/variants/{uuid}/prices/
يضبط أسعار المتغيّر **باستبدال كامل**: القوائم غير المذكورة تُحذف أسعارها. أرسل `{"prices": []}` لمسح كل الأسعار.

- **الصلاحية:** `products.manage_prices`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `prices` | array | نعم | عناصر `{price_list, price, compare_at_price?}` |
| `prices[].price_list` | uuid | نعم | قائمة فعّالة، وكل قائمة مرة واحدة |
| `prices[].price` | decimal | نعم | ≥ 0، حتى 4 منازل |
| `prices[].compare_at_price` | decimal | لا | السعر قبل الخصم؛ يجب أن يكون ≥ `price` |

```json
{ "prices": [ { "price_list": "c7402064-f696-460f-ae2b-a2ef1867dce5", "price": "26.5", "compare_at_price": "30" } ] }
```
**Response `200`:** كائن `variant` بأسعاره الجديدة.
**الأخطاء:** `400 duplicate_price_list` · `400 invalid_compare_price` · `400 price_list_inactive` · `400 validation_error` · `404` · `403`.

---

### الصور

#### DELETE /admin/images/{id}/
يحذف صورة (والملف). إن كانت الرئيسية تُرقّى التالية تلقائياً.

- **الصلاحية:** `products.update` · **Response `204`** بدون محتوى · **الأخطاء:** `404` · `403`.

---

## 9) المخزون

### المفاهيم
- المخزون لكل **متغيّر × مخزن**: `on_hand` (الكمية الفعلية) و`reserved` (المحجوز لطلبات) و`available = on_hand − reserved`. **لا يمكن أن ينزل أي منها تحت الصفر**، ولا أن يتجاوز المحجوز الفعلي (يُفرض أيضاً في قاعدة البيانات).
- **كل تغيير في الكمية يترك سطراً في سجل الحركات (`movements`)** ولا يُعدَّل ولا يُحذف أبداً. التصحيح يكون بحركة جديدة. الحركات المتزامنة على نفس الصنف تُسلسَل بقفل الصف فلا يُباع آخر قطعة مرتين.
- **التكلفة:** متوسط مرجّح بالعملة الأساسية يُحدَّث عند إدخال بضاعة بتكلفة (افتتاحي/تسوية زيادة/مشتريات)، ويشمل كل المخازن. الإخراج والتحويل لا يغيّران المتوسط.
- لا تُسجَّل الحركة إلا على منتجات `track_inventory = true`، وفي مخازن فعّالة، ولمتغيّرات غير محذوفة.
- **أنواع الحركة `type`:** `opening` (رصيد افتتاحي) · `purchase_in` (استلام مشتريات) · `sale_out` (بيع) · `return_in` · `return_out` · `adjustment` (تسوية/جرد) · `transfer_in` · `transfer_out`.
- **الحجز** (للطلبات): يقلل المتاح دون تحريك الكمية الفعلية، ويصبح بيعاً عند تنفيذ الطلب أو يُحرَّر عند الإلغاء/انتهاء الصلاحية. وظائفه جاهزة في خدمة المخزون، وسيُستخدم عبر endpoints الطلبات عند بنائها (لا endpoint مباشر له).
- **الأرقام المتسلسلة:** التسويات `ADJ-000001` والتحويلات `TRF-000001`، بلا فجوات ولا تكرار.
- **الصلاحيات:** القراءة `inventory.view`. التكلفة (`unit_cost`) تظهر فقط لمن يملك `inventory.view_cost` وإلا `null`.

### كائنات الرد

**`warehouse`:**
```json
{ "uuid": "29fd65ef-06ad-4027-9f63-f1291a45a549", "code": "MAIN", "name": "Main warehouse",
  "address": "Ramallah", "is_default": true, "is_active": true }
```

**`stock` (سطر مخزون):**
```json
{ "variant": { "uuid": "2007ed8a-…", "sku": "SH-BL-L", "name": "Blue / L", "product": "Blue Shirt" },
  "warehouse": { "uuid": "29fd65ef-…", "code": "MAIN", "name": "Main warehouse" },
  "on_hand": "20.000", "reserved": "0.000", "available": "20.000",
  "reorder_level": "3.000", "low_stock": false }
```
`low_stock`: `true` إذا كان `reorder_level > 0` والمتاح ≤ `reorder_level`.

**`movement` (حركة):**
```json
{ "uuid": "83e0fd24-…", "variant": { "uuid": "2007ed8a-…", "sku": "SH-BL-L", "name": "Blue / L", "product": "Blue Shirt" },
  "warehouse": { "uuid": "29fd65ef-…", "code": "MAIN", "name": "Main warehouse" },
  "type": "opening", "quantity": "20.000", "unit_cost": "12.5000", "balance_after": "20.000",
  "ref_type": "adjustment", "ref_id": "1", "note": "ADJ-000001 (opening)",
  "created_by": "Admin", "created_at": "2026-10-01T21:54:01.686920Z" }
```
`quantity` بإشارة: موجب = دخول، سالب = خروج. `balance_after` الرصيد بعد الحركة في ذلك المخزن.

**`adjustment` (تسوية/جرد):**
```json
{ "uuid": "d6a2ee8e-…", "number": "ADJ-000001",
  "warehouse": { "uuid": "29fd65ef-…", "code": "MAIN", "name": "Main warehouse" },
  "reason": "opening", "note": "Initial stock", "status": "posted",
  "lines": [ { "variant": { "uuid": "2007ed8a-…", "sku": "SH-BL-L", "name": "Blue / L", "product": "Blue Shirt" },
               "counted_qty": "20.000", "system_qty": "0.000", "diff": "20.000", "unit_cost": "12.5000" } ],
  "created_by": "Admin", "created_at": "2026-10-01T21:54:01.682443Z",
  "posted_by": "Admin", "posted_at": "2026-10-01T21:54:01.686920Z" }
```
قبل الترحيل (`draft`) تكون `system_qty` و`diff` و`posted_*` فارغة (`null`).

**`transfer` (تحويل):**
```json
{ "uuid": "96917f7c-…", "number": "TRF-000001",
  "from_warehouse": { "uuid": "29fd65ef-…", "code": "MAIN", "name": "Main warehouse" },
  "to_warehouse": { "uuid": "136f94ea-…", "code": "B2", "name": "Branch 2" },
  "status": "in_transit", "note": "rebalance",
  "lines": [ { "variant": { "uuid": "2007ed8a-…", "sku": "SH-BL-L", "name": "Blue / L", "product": "Blue Shirt" }, "quantity": "5.000" } ],
  "created_by": "Admin", "created_at": "2026-10-01T21:54:01.704429Z", "received_by": null, "received_at": null }
```

---

### المخازن

#### GET /admin/warehouses/
كل المخازن (غير مقسّمة). **الصلاحية:** `inventory.view` · **Response `200`:** مصفوفة `warehouse`.

---

#### POST /admin/warehouses/
- **الصلاحية:** `inventory.manage_warehouses`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `code` | string | نعم | فريد، حتى 32 حرفاً |
| `name` | string | نعم | |
| `address` | string | لا | |
| `is_default` | bool | لا | عند `true` يصبح الافتراضي ويُلغى السابق تلقائياً |
| `is_active` | bool | لا | الافتراضي `true` |

```json
{ "code": "MAIN", "name": "Main warehouse", "address": "Ramallah", "is_default": true }
```
**Response `201`:** كائن `warehouse`. **الأخطاء:** `400 validation_error` (رمز مكرر) · `403` · `401`.

---

#### GET /admin/warehouses/{uuid}/
- **الصلاحية:** `inventory.view` · **Response `200`:** كائن `warehouse` · **الأخطاء:** `404`.

---

#### PATCH /admin/warehouses/{uuid}/
- **الصلاحية:** `inventory.manage_warehouses`
- **Request body (اختيارية):** `code`, `name`, `address`, `is_default`, `is_active`.

**Response `200`:** كائن `warehouse`.
**الأخطاء:** `400 warehouse_has_stock` (لا يُعطَّل مخزن فيه كمية؛ انقلها أو سوّها أولاً) · `400 validation_error` · `404` · `403`.

---

### الأرصدة والحركات

#### GET /admin/stock/
الأرصدة الحالية لكل متغيّر ومخزن (مرتبة بالـ SKU). **لا تظهر الأسطر الفارغة (صفر) افتراضياً.**

- **الصلاحية:** `inventory.view` · مقسّمة صفحات (25)

**Query**
| المعامل | ملاحظات |
|---|---|
| `warehouse` | uuid المخزن |
| `variant` | uuid المتغيّر |
| `search` | في الـ SKU والباركود واسم المتغيّر واسم المنتج |
| `low_stock` | `true`: ما وصل حد إعادة الطلب |
| `include_empty` | `true`: يشمل الأسطر ذات الكمية صفر |
| `page` | |

**Response `200`:** `{ "count", "next", "previous", "results": [ {stock} ] }`.

---

#### GET /admin/stock/count-sheet/
ورقة الجرد: **كل** متغيّر فعّال غير محذوف لمنتج يتتبع المخزون، مع كميته في **مخزن واحد** (صفر إن لم يكن له رصيد هناك). تختلف عن `GET /admin/stock/` بأنها تشمل ما لم يُدخَل له مخزون قط، فتصلح لإدخال الأرصدة وتصحيحها. الحفظ يتم بـ `POST /admin/stock/adjustments/` (`reason: "count"`، `post: true`) والسيرفر يسجّل الفرق فقط.

- **الصلاحية:** `inventory.view` · مقسّمة صفحات (25) · مرتبة باسم المنتج ثم الـ SKU

**Query**
| المعامل | ملاحظات |
|---|---|
| `warehouse` | **مطلوب** — uuid المخزن |
| `search` | في الـ SKU والباركود واسم المتغيّر واسم المنتج |
| `page` | |

**Response `200`**
```json
{ "count": 2, "next": null, "previous": null,
  "results": [
    { "uuid": "9de6087d-a91a-4526-955e-04ee0aa59fe6", "sku": "BS-M", "name": "Var 0", "product": "Blue Shirt",
      "on_hand": "10.000", "reserved": "2.000", "available": "8.000" },
    { "uuid": "f8a93e29-f382-4e32-99c6-f2c0a78518bf", "sku": "GS-M", "name": "Var 0", "product": "Green Shirt",
      "on_hand": "0.000", "reserved": "0.000", "available": "0.000" } ] }
```
`on_hand` الكمية الفعلية في المخزن (هي ما يُصحَّح بالجرد) · `reserved` المحجوز · `available` = الفعلي − المحجوز. لا تظهر التكلفة هنا.

**الأخطاء:** `400 warehouse_required` (لم يُرسل `warehouse`) · `404 warehouse_not_found` · `403`.

---

#### GET /admin/stock/movements/
سجل الحركات (الأحدث أولاً). للقراءة فقط.

- **الصلاحية:** `inventory.view` · مقسّمة صفحات (25)

**Query**
| المعامل | ملاحظات |
|---|---|
| `variant` / `warehouse` | uuid |
| `type` | أحد أنواع الحركة |
| `from` / `to` | تاريخ `YYYY-MM-DD` (شامل) |
| `page` | |

**Response `200`:** `{ "count", "next", "previous", "results": [ {movement} ] }`. `unit_cost` = `null` لمن لا يملك `inventory.view_cost`.

---

### التسويات والجرد

التسوية تقارن **الكمية المعدودة** بكمية النظام وتسجّل الفرق فقط. تبدأ `draft` (لا تغيّر شيئاً)، وبالترحيل تُنشئ حركات `adjustment` (أو `opening` عند السبب `opening`). كل سطر يحوي `counted_qty` (الكمية الفعلية بعد العد، لا الفرق).

#### GET /admin/stock/adjustments/
- **الصلاحية:** `inventory.view` · مقسّمة صفحات (25)
- **Query:** `status` (`draft`/`posted`), `warehouse` (uuid), `page`.
- **Response `200`:** `{ "count", "next", "previous", "results": [ {adjustment} ] }`.

---

#### POST /admin/stock/adjustments/
ينشئ تسوية، ويمكن ترحيلها في نفس الطلب.

- **الصلاحية:** `inventory.adjust` (وهي نفسها المطلوبة للترحيل)
- يُسجَّل في التدقيق: `stock.adjustment_created` ثم `stock.adjustment_posted`.

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `warehouse` | uuid | نعم | مخزن فعّال |
| `reason` | string | نعم | `count` جرد · `damage` تلف · `loss` فقدان · `found` وُجد · `opening` رصيد افتتاحي · `other` |
| `note` | string | لا | حتى 255 حرفاً |
| `lines` | array | نعم | سطر واحد على الأقل، متغيّر واحد مرة واحدة |
| `lines[].variant` | uuid | نعم | متغيّر لمنتج يتتبع المخزون |
| `lines[].counted_qty` | decimal | نعم | ≥ 0، حتى 3 منازل |
| `lines[].unit_cost` | decimal | لا | تكلفة الوحدة الداخلة بالعملة الأساسية. **مطلوبة** لزيادة في رصيد افتتاحي، وإن أُرسلت لزيادة تحدّث المتوسط المرجّح |
| `post` | bool | لا | `true` يرحّلها فوراً. الافتراضي `false` (مسودة) |

```json
{ "warehouse": "29fd65ef-06ad-4027-9f63-f1291a45a549", "reason": "opening", "note": "Initial stock", "post": true,
  "lines": [ { "variant": "2007ed8a-7ba7-44c7-a927-f05cfdd4b87f", "counted_qty": "20", "unit_cost": "12.5" } ] }
```
**Response `201`:** كائن `adjustment`.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `validation_error` | حقل مفقود، `counted_qty` سالبة، سبب غير معروف، `lines` فارغة |
| 400 | `duplicate_line` | نفس المتغيّر مرتين |
| 400 | `not_tracked` / `variant_deleted` / `warehouse_inactive` | صنف أو مخزن غير صالح |
| 400 | `unit_cost_required` | رصيد افتتاحي بزيادة بلا `unit_cost` (لا يتغير شيء) |
| 400 | `below_reserved` | الكمية المعدودة أقل من المحجوز |
| 403 | `permission_denied` | |

---

#### GET /admin/stock/adjustments/{uuid}/
- **الصلاحية:** `inventory.view` · **Response `200`:** كائن `adjustment` · **الأخطاء:** `404`.

---

#### POST /admin/stock/adjustments/{uuid}/post/
يرحّل مسودة: لكل سطر يحسب `diff = counted_qty − كمية النظام` ويسجّل حركة بالفرق (الأسطر ذات الفرق صفر لا تنتج حركة). كل شيء في معاملة واحدة.

- **الصلاحية:** `inventory.adjust` · **Request:** بدون body · **Response `200`:** كائن `adjustment` بحالة `posted` و`system_qty` و`diff` معبّأة.
- **الأخطاء:** `400 already_posted` · `400 unit_cost_required` · `400 below_reserved` · `404` · `403`.

---

### التحويل بين المخازن

دورة التحويل: يُنشأ فيخرج من المخزن المصدر (`in_transit`)، ثم يُستلم في الوجهة (`received`) أو يُلغى فيعود للمصدر (`cancelled`). أثناء الشحن لا تُحتسب الكمية في أي مخزن.

#### GET /admin/stock/transfers/
- **الصلاحية:** `inventory.view` · مقسّمة صفحات (25) · **Query:** `status` (`in_transit`/`received`/`cancelled`), `page`.
- **Response `200`:** `{ "count", "next", "previous", "results": [ {transfer} ] }`.

---

#### POST /admin/stock/transfers/
ينشئ تحويلاً ويخرج الكميات من المصدر فوراً (كلها أو لا شيء).

- **الصلاحية:** `inventory.transfer`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `from_warehouse` | uuid | نعم | |
| `to_warehouse` | uuid | نعم | فعّال، ومختلف عن المصدر |
| `note` | string | لا | |
| `lines` | array | نعم | `[{"variant": uuid, "quantity": "5"}]`، الكمية > 0، متغيّر مرة واحدة |

```json
{ "from_warehouse": "29fd65ef-…", "to_warehouse": "136f94ea-…", "note": "rebalance",
  "lines": [ { "variant": "2007ed8a-7ba7-44c7-a927-f05cfdd4b87f", "quantity": "5" } ] }
```
**Response `201`:** كائن `transfer` بحالة `in_transit`.

**الأخطاء:** `400 same_warehouse` · `400 insufficient_stock` (المتاح في المصدر أقل؛ `fields` فيها `sku` و`requested` و`available`، ولا يتغير شيء) · `400 duplicate_line` / `no_lines` / `not_tracked` / `warehouse_inactive` · `400 validation_error` · `403`.

---

#### GET /admin/stock/transfers/{uuid}/
- **الصلاحية:** `inventory.view` · **Response `200`:** كائن `transfer` · **الأخطاء:** `404`.

---

#### POST /admin/stock/transfers/{uuid}/receive/
يستلم التحويل في المخزن الوجهة (تدخل الكميات هناك بتكلفتها وقت الإرسال).

- **الصلاحية:** `inventory.transfer` · **Request:** بدون body · **Response `200`:** كائن `transfer` بحالة `received`.
- **الأخطاء:** `400 invalid_transfer_state` (ليس `in_transit`) · `400 warehouse_inactive` · `404` · `403`.

---

#### POST /admin/stock/transfers/{uuid}/cancel/
يلغي تحويلاً قيد الشحن ويعيد الكميات للمخزن المصدر.

- **الصلاحية:** `inventory.transfer` · **Request:** بدون body · **Response `200`:** كائن `transfer` بحالة `cancelled`.
- **الأخطاء:** `400 invalid_transfer_state` · `404` · `403`.

---

## 10) واجهة المتجر العامة (للزبائن)

مسارات **عامة للقراءة فقط** بدون تسجيل دخول. أي توكن يُرسَل معها يُتجاهل (حتى لو منتهياً) فلا يعطّل التصفح. الطرق الأخرى غير `GET` ترجع `405`.

**قواعد ما يراه الزبون:** المنتج يظهر فقط إذا اجتمع: `status = active` · `is_visible_on_store = true` · غير محذوف · له متغيّر فعّال غير محذوف **وله سعر في القائمة الافتراضية الفعّالة للعملة المطلوبة** · **وله مخزون متاح**: المنتج الذي لا يملك أي متغيّر منه كمية متاحة (الفعلي − المحجوز > 0 في مخزن فعّال) **لا يظهر أبداً** (لا في القائمة ولا في الماركات ولا `404` عند طلبه بالـ `slug`) ويعود تلقائياً فور دخول مخزون له؛ ويشمل ذلك المنتج الذي لا يتتبع المخزون (لا يمكن أن يملك رصيداً) — فعّل له «تتبّع المخزون» وأدخل كمية ليظهر. إخفاء المنتج من الإدارة (`hide`/`archive`/…) يُخفيه من هنا فوراً. **لا تُعرض أبداً:** التكلفة، أعداد المخزون الفعلية، المسودات، المحذوف. المخزون يظهر كـ `in_stock` (صح/خطأ) فقط، ويحسب المتاح (الفعلي − المحجوز) في المخازن الفعّالة؛ بما أن المنتج بلا مخزون مخفي، فـ `in_stock` على مستوى المنتج دائماً `true`، وقد يكون `false` لمتغيّر نفد داخل منتج بقي له مخزون في متغيّر آخر.

**العملة:** `?currency=USD` (الافتراضي العملة الأساسية ILS). الأسعار المعروضة هي سعر القائمة الافتراضية لتلك العملة، لا تحويل تلقائي. عملة غير معروفة أو معطّلة ← `400 invalid_currency`. عملة ليس لها قائمة أسعار افتراضية ← قائمة فارغة.

**اللغة:** `?lang=ar|en|he` (اختياري) على كل مسارات المتجر: الأسماء (المنتج والتصنيف والماركة) والوصف تُرجَع بتلك اللغة إن أُدخلت لها ترجمة، وإلا بالنص **الافتراضي** (`name` / `description`)، فلا يظهر حقل فارغ أبداً. لغة غير معروفة أو غائبة = النص الافتراضي ولا خطأ. تغيّر اللغة النصوص فقط: `slug`ات التصنيف والماركة والمنتج ثابتة بكل اللغات (روابط الصفحات لا تتغير). `?search=` يبحث بالأسماء بكل اللغات.

#### GET /store/categories/
التصنيفات الفعّالة (غير مقسّمة صفحات).

**Response `200`**
```json
[ { "name": "Clothes", "slug": "clothes", "image": "http://127.0.0.1:8000/media/categories/clothes-091122123456.webp", "parent": null },
  { "name": "Shirts", "slug": "shirts", "image": null, "parent": "clothes" } ]
```

---

#### GET /store/brands/
الماركات التي لها **منتج ظاهر للزبائن واحد على الأقل** (بالعملة المطلوبة)، مرتبة بالاسم، غير مقسّمة صفحات. ماركة معطّلة أو بلا منتجات ظاهرة لا تُعرض، فلا تظهر صفحة ماركة فارغة. `?currency=` كما في بقية المسار (عملة بلا قائمة أسعار افتراضية ← قائمة فارغة).

**Response `200`**
```json
[ { "name": "Sparkle", "slug": "sparkle", "logo": "http://127.0.0.1:8000/media/brands/sparkle-091122123456.png", "products_count": 1 },
  { "name": "test", "slug": "test", "logo": null, "products_count": 1 } ]
```
`slug`: معرّف الماركة الثابت في الروابط وفي فلتر `?brand=`. `logo`: رابط مطلق لصورة الماركة أو `null` إن لم تُرفع لها صورة. `name`: اسمها باللغة المطلوبة (`?lang=`) وإلا الافتراضي، والقائمة مرتبة بهذا الاسم. `products_count`: عدد المنتجات الظاهرة للماركة. **الأخطاء:** `400 invalid_currency`.

---

#### GET /store/products/
المنتجات الظاهرة للزبائن، مقسّمة صفحات (25).

**Query**
| المعامل | ملاحظات |
|---|---|
| `currency` | `ILS` (الافتراضي) / `USD` / `JOD` |
| `category` | `slug` التصنيف، ويشمل تصنيفاته الفرعية |
| `lang` | `ar` / `en` / `he` (انظر أعلاه) |
| `brand` | `slug` الماركة كما يرجع من `GET /store/brands/` (مطابقة تامة). ماركة معطّلة أو غير موجودة ← قائمة فارغة. يجتمع مع `category` و`search` |
| `search` | في اسم المنتج واسم الماركة، **بكل اللغات** |
| `min_price` / `max_price` | حدّ أدنى/أعلى للسعر (شاملان، رقم غير سالب بنقطة عشرية، بعملة `currency`). يُقارَن بـ `price_from` أي أرخص متغيّر. فارغ = بلا حد. يجتمعان مع كل الفلاتر وترتيب النتائج |
| `ordering` | `newest` (الافتراضي) · `name` · `price` (الأرخص أولاً) · `-price` |
| `page` | |

**Response `200`**
```json
{ "count": 1, "next": null, "previous": null,
  "results": [ { "uuid": "f6cba954-ba00-4be3-9c7d-3a9f164c7e7f", "name": "Blue Shirt", "slug": "blue-shirt",
                 "brand": { "name": "Acme", "slug": "acme" }, "category": { "name": "Shirts", "slug": "shirts" },
                 "currency": "ILS", "price_from": "26.5000", "in_stock": true,
                 "image": "http://127.0.0.1:8000/media/products/2026/10/blue-shirt-215401667891.png" } ] }
```
`price_from`: أرخص متغيّر. `brand` و`category` و`image` قد تكون `null`. **الأخطاء:** `400 invalid_currency` · `400 invalid_price` (`min_price` أو `max_price` ليس رقماً أو سالب).

---

#### GET /store/products/price-range/
حدّا شريط فلتر السعر: أرخص وأغلى `price_from` بين المنتجات التي تُبقيها بقية الفلاتر.

- **الصلاحية:** عام (بلا تسجيل دخول).
- **Query:** `currency` · `category` · `brand` · `search` · `lang`، بنفس معانيها في `GET /store/products/`. **يتجاهل** `min_price` و`max_price` عمداً حتى لا يضيق الشريط على نفسه.

**Response `200`**
```json
{ "currency": "ILS", "min": "5.0000", "max": "312.0000" }
```
لا منتجات مطابقة (أو لا قائمة أسعار افتراضية للعملة) ← `"min": null, "max": null` وليس خطأ.
**الأخطاء:** `400 invalid_currency`.

---

#### GET /store/products/{slug}/
تفاصيل منتج ظاهر بالـ `slug` (`?currency=` كما سبق).

**Response `200`**
```json
{ "uuid": "f6cba954-…", "name": "Blue Shirt", "slug": "blue-shirt", "brand": { "name": "Acme", "slug": "acme" },
  "category": { "name": "Shirts", "slug": "shirts" }, "currency": "ILS", "price_from": "26.5000",
  "in_stock": true, "image": "http://127.0.0.1:8000/media/products/2026/10/blue-shirt-215401667891.png",
  "description": "Cotton shirt",
  "variants": [ { "uuid": "2007ed8a-…", "sku": "SH-BL-L", "name": "Blue / L", "attributes": { "color": "blue", "size": "L" },
                  "price": "26.5000", "compare_at_price": "30.0000", "in_stock": true } ],
  "images": [ { "url": "http://127.0.0.1:8000/media/products/2026/10/blue-shirt-215401667891.png",
                "alt": "front", "is_primary": true } ] }
```
`variants` فيها فقط المتغيّرات الفعّالة المسعَّرة. **الأخطاء:** `404 not_found` (غير موجود، أو مخفي، أو ليس فعّالاً، أو بلا سعر بهذه العملة) · `400 invalid_currency`.

---

#### GET /store/about/
محتوى صفحة «من نحن»: نبذة، العنوان، ساعات العمل، وسائل التواصل، الموقع، وحسابات التواصل الاجتماعي. تُدار من الإدارة (`GET/PATCH /admin/store-profile/`). `?lang=ar|en|he` كبقية المسار: النصوص (`about` و`address` و`hours`) باللغة المطلوبة وإلا النص الافتراضي. تعمل قبل إدخال أي شيء (حقول فارغة وقائمة روابط فارغة).

**Response `200`**
```json
{ "about": "نسهّل حياتك اليومية", "address": "شارع الإرسال، رام الله", "hours": "السبت–الخميس 9:00–18:00",
  "phone": "0535746112", "whatsapp": "972535746112", "email": "hi@easylife.example",
  "latitude": "31.903400", "longitude": "35.203400", "map_url": "https://maps.google.com/?q=31.9034,35.2034",
  "social_links": [ { "platform": "instagram", "url": "https://instagram.com/easylife", "label": "@easylife" } ] }
```
- `phone` / `whatsapp` / `email`: فارغة إن لم تُدخَل (الواجهة تعود لرقم النشر الافتراضي). `whatsapp` أرقام بالصيغة الدولية فقط.
- `latitude` / `longitude`: نصوص عشرية أو `null` (يأتيان معاً أو لا يأتيان).
- `social_links`: **الفعّالة فقط** بترتيبها؛ `platform` ∈ `facebook` `instagram` `whatsapp` `tiktok` `youtube` `x` `telegram` `snapchat` `linkedin` `website` `other`. كل الروابط `http(s)` فقط.

---

## 10-ب) حساب الزبون: ديونه وفواتيره

للزبون المسجَّل دخوله (بكود واتساب كالعادة). يرى **بياناته هو فقط**: ما عليه من دين، وفواتيره، ودفعاته. التوثيق مطلوب (`401` بدونه).

#### GET /account/summary/
- **Response `200`:**
```json
{
  "balance": "7.00",
  "open_invoices": 1,
  "invoices": 2,
  "total_purchases": "30.00",
  "total_paid_off": "18.00"
}
```
(نفس معاني ملخص ملف الزبون في القسم 6.)

---

#### GET /account/invoices/
فواتيري (الأحدث أولاً)، مقسّمة صفحات (24). **Query:** `open=1` الفواتير التي عليها دين فقط · `page`.

**Response `200`**
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "uuid": "09422533-2507-41ff-b976-bd6e06320358",
      "number": "POS-000002",
      "status": "completed",
      "created_at": "2026-10-03T15:46:37.408331+03:00",
      "items": 1,
      "total": "10.00",
      "credit_amount": "10.00",
      "balance_due": "7.00"
    },
    {
      "uuid": "b6d79983-6905-497b-9928-58295ecda76f",
      "number": "POS-000001",
      "status": "completed",
      "created_at": "2026-10-03T15:46:37.388822+03:00",
      "items": 2,
      "total": "20.00",
      "credit_amount": "15.00",
      "balance_due": "0.00"
    }
  ]
}
```

---

#### GET /account/invoices/{uuid}/
فاتورة من فواتيري بسطورها وما دُفع عند الشراء والدين وكيف سُدّد. فاتورة زبون آخر = `404`.

**Response `200`**
```json
{
  "uuid": "09422533-2507-41ff-b976-bd6e06320358",
  "number": "POS-000002",
  "status": "completed",
  "created_at": "2026-10-03T15:46:37.408331+03:00",
  "lines": [
    { "line_no": 1, "sku": "DS-1", "name": "Dish Soap · Var 0", "quantity": "1.000", "unit_price": "10.00",
      "discount": "0.00", "line_total": "10.00" }
  ],
  "subtotal": "10.00",
  "discount_total": "0.00",
  "total": "10.00",
  "tax": null,
  "payments": [],
  "credit_amount": "10.00",
  "balance_due": "7.00",
  "debt_payments": [
    { "number": "RCV-000001", "date": "2026-10-03T15:46:37.425975+03:00", "method": "card", "amount": "3.00" }
  ]
}
```
- **الأخطاء:** `404 not_found` · `401`.

---

#### GET /account/payments/
دفعاتي على الدين (الأحدث أولاً)، مقسّمة صفحات (24). نفس شكل `GET /admin/customers/{uuid}/payments/` **بدون** `received_by`.

**Response `200`**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "uuid": "7b14468e-32e1-4403-bd9c-53ee91d2a540",
      "number": "RCV-000001",
      "created_at": "2026-10-03T15:46:37.425975+03:00",
      "amount": "18.00",
      "method": "card",
      "reference": "slip 7",
      "note": "",
      "invoices": [
        { "uuid": "b6d79983-6905-497b-9928-58295ecda76f", "number": "POS-000001", "amount": "15.00" },
        { "uuid": "09422533-2507-41ff-b976-bd6e06320358", "number": "POS-000002", "amount": "3.00" }
      ]
    }
  ]
}
```

---

## 11) المحاسبة

نظام قيد مزدوج بعملة دفاتر أساسية **شيكل (ILS)**، ويدعم USD وJOD في السطور (تُحوَّل للأساسية بسعر الصرف). تهيئة أولية: `python manage.py seed_accounting [--year 2026]` (دليل الحسابات الافتراضي بثلاث لغات + ربط الحسابات الافتراضي + 12 فترة شهرية).

### قواعد لا تتغير (يفرضها الخادم)
| القاعدة | الأثر |
|---|---|
| القيد المرحَّل **متوازن** (مجموع المدين = مجموع الدائن بالعملة الأساسية) وفيه **سطران** على الأقل | `unbalanced_entry` · `entry_too_few_lines` |
| كل سطر **إما مدين أو دائن** على حساب **قابل للترحيل وفعّال** | `account_not_postable` · `account_inactive` (والقاعدة مفروضة أيضاً بقيود في قاعدة البيانات) |
| لا ترحيل ولا إنشاء قيد في فترة **مغلقة أو غير موجودة** | `period_closed` · `period_not_found` |
| **المرحَّل لا يُعدَّل ولا يُحذف** (حتى من الكود)؛ التصحيح بـ**قيد عكسي** | `entry_not_draft` |
| **Maker–Checker:** من أنشأ القيد لا يرحّله (الأدمن مستثنى) | `cannot_post_own_entry` |
| المسودة يعدّلها كاتبها أو الأدمن فقط | `not_entry_owner` |
| القيود التلقائية من الوحدات الأخرى تُنشأ وتُرحَّل مباشرة وتحمل مصدرها (لا تخضع لـ maker–checker) | خدمة داخلية `create_posted_entry` |
| التقارير تقرأ **المرحَّل والمعكوس فقط** (المسودات لا تؤثر) | — |

**مبالغ السطر:** تُدخل `amount` بعملة السطر (`currency` الافتراضية ILS) و`side` (`debit`/`credit`)، فيحسب الخادم المقابل بالأساسية `= amount × rate` ويقرّبه لمنزلتين. `rate` اختياري: يؤخذ **آخر سعر ≤ تاريخ القيد** من أسعار الصرف (وإلا `rate_missing`)، ويمكن تحديده يدوياً. الحقول `debit`/`credit` في الرد **دائماً بالعملة الأساسية**، و`amount_currency` بعملة السطر نفسها.

**ترقيم القيود:** `JV-000001` متسلسل بلا فجوات. **حالات القيد:** `draft` ← `posted` ← `reversed`.

**الصلاحيات:** القراءة `accounting.view` · الحسابات والربط `accounting.manage_accounts` · الضرائب `accounting.manage_tax` · الفترات (إنشاء/إغلاق/إعادة فتح) `accounting.close_period` · القيود: إنشاء/تعديل/حذف مسودة `accounting.create_entry`، ترحيل `accounting.post_entry`، عكس `accounting.reverse_entry` · التقارير `reports.financial` (+ `reports.export` للتصدير). دور `accountant` الجاهز يملك كل ذلك **عدا** `close_period` (للأدمن).

**كائنات الرد:**

`account`:
```json
{ "uuid": "842fab34-…", "code": "1110", "name_ar": "الصندوق", "name_en": "Cash", "name_he": "קופה",
  "type": "asset", "parent": "daa71d41-…", "is_postable": true, "currency": null,
  "is_system": true, "is_active": true, "has_entries": false, "balance": null }
```
`type`: `asset` · `liability` · `equity` · `revenue` · `expense`. `parent`: uuid أو `null`. `currency`: رمز عملة تُقيَّد بها الحساب أو `null` (أي عملة). `balance`: `null` إلا مع `?with_balance=true`. `has_entries`: هل له حركات (يقفل نوعه وأبوه وقابليته للترحيل).

`fiscal_period`:
```json
{ "uuid": "64c80137-…", "name": "2026-01", "start_date": "2026-01-01", "end_date": "2026-01-31",
  "status": "open", "posted_entries": 0, "draft_entries": 0, "closed_at": null }
```

`journal_entry` (التفاصيل) — القائمة تحوي الحقول حتى `created_by` بدون `lines` و`totals`:
```json
{ "uuid": "0a12ff11-…", "number": "JV-000001", "date": "2026-03-10", "period": "2026-03",
  "memo": "Opening capital", "kind": "journal", "status": "posted", "source": { "type": "manual", "id": null },
  "total": "370.00", "created_by": "Admin", "posted_by": "Admin", "posted_at": "2026-10-02T07:11:03Z",
  "reversal_of": null, "reversed_by": null, "reversed_at": null, "reverse_reason": "",
  "totals": { "debit": "370.00", "credit": "370.00", "difference": "0.00", "balanced": true },
  "lines": [
    { "line_no": 1, "account": { "uuid": "384f91ac-…", "code": "1110", "name_ar": "الصندوق", "name_en": "Cash", "name_he": "קופה" },
      "description": "USD cash", "currency": "USD", "amount_currency": "100.00", "rate": "3.70000000",
      "debit": "370.00", "credit": "0.00" } ] }
```
`kind`: `journal` · `opening` · `receipt` · `payment`. `source.type`: `manual` للقيود اليدوية أو اسم الوحدة (`order`…). `reversal_of`/`reversed_by`: `{uuid, number}` أو `null`.

**أخطاء أسطر القيد** تشير إلى رقم السطر (يبدأ من 1): `{"code": "account_not_postable", "fields": {"lines": {"2": {"account": ["1000 is a group account."]}}}}`.

---

### دليل الحسابات

#### GET /admin/accounts/
كل الحسابات (مسطّحة، غير مقسّمة صفحات، مرتبة بالكود). الواجهة تبني الشجرة من `parent`.

- **الصلاحية:** `accounting.view`

**Query**
| المعامل | ملاحظات |
|---|---|
| `type` | أحد الأنواع الخمسة |
| `active` / `postable` | `true` / `false` |
| `search` | في الكود والأسماء الثلاثة |
| `with_balance` | `true`: يملأ `balance` بالرصيد بجهته الطبيعية (أصول/مصروفات مدين، والباقي دائن) بالعملة الأساسية |
| `as_of` | `YYYY-MM-DD` للرصيد (الافتراضي اليوم) |

**Response `200`:** مصفوفة `account`.

---

#### POST /admin/accounts/
ينشئ حساباً. إن كان أبوه قابلاً للترحيل **يصبح الأب حساب تجميع** تلقائياً (ما لم تكن عليه حركات).

- **الصلاحية:** `accounting.manage_accounts`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `code` | string | نعم | أحرف/أرقام/نقطة/شرطة حتى 20، فريد |
| `name_ar` · `name_en` · `name_he` | string | نعم | **الثلاثة مطلوبة** |
| `type` | string | نعم | `asset` · `liability` · `equity` · `revenue` · `expense` |
| `parent` | uuid | لا | يجب أن يطابق النوع |
| `is_postable` | bool | لا | الافتراضي `true` |
| `currency` | string | لا | رمز عملة لتقييد الحساب |
| `is_active` | bool | لا | الافتراضي `true` |

```json
{ "code": "1160", "name_ar": "عهدة موظفين", "name_en": "Staff advances", "name_he": "מקדמות עובדים",
  "type": "asset", "parent": "daa71d41-…" }
```
**Response `201`:** `account`.

**الأخطاء:** `400 code_taken` · `400 parent_type_mismatch` · `400 parent_has_entries` (الأب له حركات) · `400 validation_error` · `403` · `401`.

---

#### GET /admin/accounts/{uuid}/
- **الصلاحية:** `accounting.view` · **Query:** `with_balance`, `as_of` · **Response `200`:** `account` · **الأخطاء:** `404`.

---

#### PATCH /admin/accounts/{uuid}/
يعدّل حساباً. الحقول: كل حقول الإنشاء (اختيارية). **بعد وجود حركات** تُقفَل: `code` · `type` · `parent` · `is_postable` · `currency`؛ وتبقى الأسماء والتفعيل قابلة للتعديل.

- **الصلاحية:** `accounting.manage_accounts`

**Response `200`:** `account`.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `account_in_use` | تغيير حقل مقفل لحساب له حركات |
| 400 | `system_account_locked` | تغيير نوع حساب نظام |
| 400 | `parent_cycle` / `parent_type_mismatch` / `parent_has_entries` | خطأ في الأب |
| 400 | `account_has_children` | جعل حساب له فروع قابلاً للترحيل |
| 400 | `account_mapped` | تعطيل حساب مربوط في الإعدادات الافتراضية؛ أعد الربط أولاً |
| 400 | `code_taken` | |
| 404 | `not_found` | |

---

#### POST /admin/accounts/seed/
يحمّل دليل الحسابات الافتراضي (21 حساباً بثلاث لغات) وربط الحسابات الافتراضي. **آمن للتكرار**: لا يغيّر ما هو موجود ولا يستعيد ما عدّلته.

- **الصلاحية:** `accounting.manage_accounts` · **Request:** بدون body.
- **Response `201`** (أُنشئ شيء) أو `200`: `{ "created": 21, "total": 21 }`

---

### الفترات المالية

#### GET /admin/fiscal-periods/
فترات (شهرية)، مرتبة زمنياً، غير مقسّمة صفحات.

- **الصلاحية:** `accounting.view` · **Query:** `year` (اختياري)
- **Response `200`:** مصفوفة `fiscal_period` (مع عدّاد القيود المرحَّلة والمسودات).

---

#### GET /admin/fiscal-periods/{uuid}/
- **الصلاحية:** `accounting.view` · **Response `200`:** `fiscal_period` · **الأخطاء:** `404`.

---

#### POST /admin/fiscal-periods/generate/
ينشئ 12 فترة شهرية (يناير–ديسمبر) لسنة.

- **الصلاحية:** `accounting.close_period`
- **Request:** `{ "year": 2027 }` (2000–2100) · **Response `201`:** مصفوفة 12 `fiscal_period`.
- **الأخطاء:** `400 year_exists` · `400 validation_error` · `403`.

---

#### GET /admin/fiscal-periods/{uuid}/close-checklist/
فحوص ما قبل الإغلاق (للمعالج).

- **الصلاحية:** `accounting.view`

**Response `200`**
```json
{ "period": "2026-03", "can_close": true,
  "checks": [
    { "code": "no_drafts", "level": "error", "ok": true, "detail": { "drafts": 0 } },
    { "code": "trial_balance_balanced", "level": "error", "ok": true, "detail": { "debit": "370.00", "credit": "370.00" } },
    { "code": "rates_present", "level": "warning", "ok": true, "detail": { "missing": [] } } ] }
```
`level: "error"` يمنع الإغلاق، و`"warning"` تنبيه فقط (`missing`: عملات مستعملة بلا سعر صرف لآخر الفترة).

---

#### POST /admin/fiscal-periods/{uuid}/close/
يغلق الفترة: بعدها لا إنشاء ولا ترحيل ولا عكس في تواريخها. يُسجَّل `period.closed`.

- **الصلاحية:** `accounting.close_period` · **Request:** بدون body · **Response `200`:** `fiscal_period` بحالة `closed`.
- **الأخطاء:** `400 period_has_drafts` (في `fields.drafts` العدد) · `400 trial_balance_unbalanced` · `400 period_already_closed` · `403` · `404`.

---

#### POST /admin/fiscal-periods/{uuid}/reopen/
يعيد فتح فترة مغلقة. **للأدمن فقط وبسبب إلزامي** يُسجَّل في سجل التدقيق.

- **الصلاحية:** `accounting.close_period` (+ أدمن)
- **Request:** `{ "reason": "late supplier invoice" }` · **Response `200`:** `fiscal_period` بحالة `open`.
- **الأخطاء:** `403 forbidden` (غير أدمن) · `400 reason_required` · `400 period_not_closed` · `404`.

---

### الضرائب

كائن `tax_rate`: `{ "uuid", "name", "rate": "16.0000", "collected_account": "<uuid>", "paid_account": "<uuid>", "is_active": true }`. `rate` نسبة مئوية 0–100. **لا نسبة افتراضية**: تُدخَل بحسب نظامكم الضريبي (يُنصح بتأكيدها مع المحاسب القانوني).

#### GET /admin/tax-rates/
قائمة الضرائب (غير مقسّمة). **الصلاحية:** `accounting.view` · **Response `200`:** مصفوفة `tax_rate`.

---

#### POST /admin/tax-rates/
- **الصلاحية:** `accounting.manage_tax`
- **Request:** `{ "name": "VAT", "rate": "16", "collected_account": "<uuid حساب التزامات>", "paid_account": "<uuid حساب أصول>" }` (+ `is_active` اختياري).
- **Response `201`:** `tax_rate`.
- **الأخطاء:** `400 tax_account_type` (المستحقة يجب أن تكون التزامات والمدفوعة أصولاً، وكلاهما قابل للترحيل) · `400 validation_error` (اسم مكرر، نسبة خارج 0–100) · `403`.

---

#### GET /admin/tax-rates/{uuid}/
- **الصلاحية:** `accounting.view` · **Response `200`:** `tax_rate` · **الأخطاء:** `404`.

---

#### PATCH /admin/tax-rates/{uuid}/
- **الصلاحية:** `accounting.manage_tax` · **Request:** أي من حقول الإنشاء · **Response `200`:** `tax_rate` · **الأخطاء:** كما في الإنشاء + `404`.

---

### ربط الحسابات الافتراضية

المفاتيح الاثنا عشر وأنواعها: `sales` (إيراد) · `cogs` (مصروف) · `inventory` (أصل) · `receivable` (أصل) · `payable` (التزام) · `cash` (أصل) · `bank` (أصل) · `tax_collected` (التزام) · `tax_paid` (أصل) · `inventory_adjustment` (مصروف) · `fx_gain` (إيراد) · `fx_loss` (مصروف). تستعملها القيود التلقائية للوحدات الأخرى.

#### GET /admin/account-mappings/
- **الصلاحية:** `accounting.view`

**Response `200`**
```json
{ "items": [ { "key": "sales", "label": "Sales revenue", "expected_type": "revenue",
               "account": { "uuid": "c6bda6c7-…", "code": "4100", "name_ar": "المبيعات", "name_en": "Sales", "name_he": "מכירות" } } ],
  "filled": 12, "total": 12, "complete": true }
```
`account: null` للمفتاح غير المربوط. `complete` = كل المفاتيح مربوطة.

---

#### PUT /admin/account-mappings/
يربط مفاتيح بحسابات (يُحدَّث المرسَل فقط).

- **الصلاحية:** `accounting.manage_accounts`
- **Request:** `{ "mappings": { "sales": "<uuid>", "cash": "<uuid>" } }`
- **Response `200`:** نفس شكل `GET`.
- **الأخطاء:** `400 mapping_type_mismatch` (نوع الحساب لا يطابق المفتاح) · `400 account_not_postable` · `400 validation_error` (مفتاح غير معروف أو `mappings` فارغ) · `403` · `404` (حساب غير موجود).

---

### القيود اليومية

#### GET /admin/journal-entries/
القيود (الأحدث تاريخاً أولاً)، مقسّمة صفحات (25)، بدون الأسطر.

- **الصلاحية:** `accounting.view`

**Query**
| المعامل | ملاحظات |
|---|---|
| `status` | `draft` / `posted` / `reversed` |
| `from` / `to` | تاريخ `YYYY-MM-DD` (شامل) |
| `source` | `manual` للقيود اليدوية أو اسم المصدر (`order`…) |
| `account` | uuid حساب: القيود التي تمسّه |
| `kind` · `created_by` (uuid) | |
| `search` | في الرقم والبيان |
| `page` | |

**Response `200`:** `{ "count", "next", "previous", "results": [ {journal_entry بلا lines} ] }`.

---

#### POST /admin/journal-entries/
ينشئ **مسودة** (لا تؤثر على الأرصدة). الأسطر اختيارية في المسودة (يُتحقق من التوازن عند الترحيل).

- **الصلاحية:** `accounting.create_entry`

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `date` | date | نعم | يجب أن يقع في فترة **مفتوحة** |
| `memo` | string | لا | حتى 255 |
| `kind` | string | لا | الافتراضي `journal` |
| `lines` | array | لا | الأسطر (انظر التالي) |
| `lines[].account` | uuid | نعم | حساب قابل للترحيل وفعّال |
| `lines[].side` | string | نعم | `debit` أو `credit` |
| `lines[].amount` | decimal | نعم | > 0، بعملة السطر |
| `lines[].currency` | string | لا | الافتراضي ILS؛ يجب أن يطابق قيد الحساب إن وُجد |
| `lines[].rate` | decimal | لا | يدوي؛ وإلا آخر سعر ≤ التاريخ |
| `lines[].description` | string | لا | |

```json
{ "date": "2026-03-10", "memo": "Opening capital",
  "lines": [ { "account": "384f91ac-…", "side": "debit", "amount": "100", "currency": "USD", "description": "USD cash" },
             { "account": "25b4c0df-…", "side": "credit", "amount": "370.00" } ] }
```
**Response `201`:** `journal_entry` (التفاصيل) بحالة `draft`.

**الأخطاء:** `400 period_closed` / `period_not_found` · `400 account_not_postable` / `account_inactive` / `currency_mismatch` / `currency_inactive` / `invalid_amount` / `invalid_rate` / `rate_missing` (مع رقم السطر في `fields.lines`) · `400 validation_error` · `403`.

---

#### GET /admin/journal-entries/{uuid}/
- **الصلاحية:** `accounting.view` · **Response `200`:** `journal_entry` (التفاصيل بالأسطر) · **الأخطاء:** `404`.

---

#### PATCH /admin/journal-entries/{uuid}/
يعدّل **مسودة** (كاتبها أو الأدمن فقط). إرسال `lines` **يستبدل كل الأسطر**. تغيير `date` لقيد متعدد العملات يتطلب إرسال `lines` من جديد (الأسعار تتبع التاريخ).

- **الصلاحية:** `accounting.create_entry`
- **Request (اختيارية):** `date`, `memo`, `lines`
- **Response `200`:** `journal_entry`.
- **الأخطاء:** `400 entry_not_draft` · `403 not_entry_owner` · `400 lines_required` · `400 period_closed` / `period_not_found` + أخطاء الأسطر أعلاه · `404`.

---

#### DELETE /admin/journal-entries/{uuid}/
يحذف **مسودة** فقط (كاتبها أو الأدمن).

- **الصلاحية:** `accounting.create_entry` · **Response `204`** بدون محتوى.
- **الأخطاء:** `400 entry_not_draft` (المرحَّل لا يُحذف أبداً) · `403 not_entry_owner` · `404`.

---

#### POST /admin/journal-entries/{uuid}/post/
يرحّل مسودة: تُعاد كل التحققات (سطران، حسابات صالحة، **توازن**، الفترة مفتوحة) ثم تصبح `posted` ولا تُعدَّل بعدها. يُسجَّل `journal.posted`.

- **الصلاحية:** `accounting.post_entry` · **Request:** بدون body · **Response `200`:** `journal_entry` بحالة `posted`.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `unbalanced_entry` | غير متوازن؛ في `fields`: `debit` و`credit` و`difference` |
| 400 | `entry_too_few_lines` | أقل من سطرين |
| 400 | `account_not_postable` / `account_inactive` | تغيّر حساب بعد الحفظ |
| 400 | `period_closed` / `period_not_found` | |
| 400 | `entry_already_posted` | ليست مسودة |
| 403 | `cannot_post_own_entry` | أنت كاتبها (maker–checker) |
| 404 | `not_found` | |

---

#### POST /admin/journal-entries/{uuid}/reverse/
يعكس قيداً مرحَّلاً: ينشئ **قيداً جديداً مرحَّلاً** بالمدين والدائن مقلوبين (بنفس الحسابات والعملات والأسعار)، ويصبح الأصلي `reversed`. صافي الأثر على الدفاتر صفر. يُسجَّل `journal.reversed`.

- **الصلاحية:** `accounting.reverse_entry`
- **Request:** `{ "reason": "wrong account", "date": "2026-03-12" }` (`date` اختياري، الافتراضي اليوم، ويجب أن يقع في فترة مفتوحة).
- **Response `201`:** القيد العاكس (`journal_entry` مع `reversal_of`).
- **الأخطاء:** `400 reason_required` · `400 entry_not_posted` · `400 entry_already_reversed` · `400 cannot_reverse_reversal` · `400 period_closed` / `period_not_found` · `404`.

---

### التقارير

> كلها بالعملة الأساسية، مرحَّل فقط، والاسم بلغة `?lang=ar|en|he` (الافتراضي `ar`). **الصلاحية `reports.financial`**. التواريخ `YYYY-MM-DD`: الافتراضي `from` = أول السنة و`to` = اليوم. **التصدير:** `?export=csv` على ميزان المراجعة وكشف الحساب فقط (ملف UTF-8 مع BOM ليفتح العربي/العبري في Excel)، ويتطلب أيضاً `reports.export`. الأخطاء المشتركة: `400 invalid_date` · `400 invalid_range` (`from` بعد `to`) · `400 export_not_supported` · `403`.

#### GET /admin/reports/trial-balance/
ميزان المراجعة: لكل حساب رصيد أول المدة، حركة الفترة، ورصيد آخر المدة (مدين/دائن).

**Query:** `from`, `to`, `lang`, `include_zero=true` (كل الحسابات القابلة للترحيل ولو بلا حركة)، `export=csv`.

**Response `200`**
```json
{ "from": "2026-03-01", "to": "2026-03-31", "currency": "ILS",
  "rows": [ { "account": { "uuid": "384f91ac-…", "code": "1110", "name": "الصندوق" }, "type": "asset",
              "opening": { "debit": "0.00", "credit": "0.00" }, "movement": { "debit": "370.00", "credit": "0.00" },
              "closing": { "debit": "370.00", "credit": "0.00" } } ],
  "totals": { "opening": { "debit": "0.00", "credit": "0.00" }, "movement": { "debit": "370.00", "credit": "370.00" },
              "closing": { "debit": "370.00", "credit": "370.00" } },
  "balanced": true }
```
`balanced: false` يعني خللاً في الدفاتر ويجب التبليغ عنه (لا يحدث عبر الـ API).

---

#### GET /admin/reports/general-ledger/
كشف حساب (دفتر الأستاذ): حركات حساب مع **رصيد جارٍ** صحيح عبر الصفحات.

**Query:** `account` (uuid، **مطلوب**), `from`, `to`, `lang`, `page`, `page_size` (الافتراضي 100، الأقصى 500), `export=csv`.

**Response `200`**
```json
{ "account": { "uuid": "384f91ac-…", "code": "1110", "name": "الصندوق" }, "from": "2026-03-01", "to": "2026-03-31",
  "currency": "ILS", "balance_side": "debit", "opening_balance": "0.00",
  "rows": [ { "date": "2026-03-10", "entry": { "uuid": "0a12ff11-…", "number": "JV-000001" }, "description": "USD cash",
              "source": { "type": "manual", "id": null }, "currency": "USD", "amount_currency": "100.00", "rate": "3.70000000",
              "debit": "370.00", "credit": "0.00", "balance": "370.00" } ],
  "totals": { "debit": "370.00", "credit": "0.00" }, "closing_balance": "370.00", "count": 1, "page": 1, "page_size": 100 }
```
`balance_side`: جهة الرصيد الطبيعية للحساب (مدين للأصول والمصروفات، دائن للباقي)، والرصيد الجاري بهذه الجهة. `opening_balance` رصيد ما قبل `from`.
**الأخطاء الخاصة:** `400 account_required` · `400 invalid_page` · `404` (حساب غير موجود).

---

#### GET /admin/reports/income-statement/
قائمة الدخل لفترة: الإيرادات ← تكلفة المبيعات (الحساب المربوط بـ`cogs`) ← **مجمل الربح** ← المصروفات ← **صافي الربح**.

**Query:** `from`, `to`, `lang`.

**Response `200`**
```json
{ "from": "2026-03-01", "to": "2026-04-30", "currency": "ILS",
  "revenue": { "rows": [ { "account": { "uuid": "…", "code": "4100", "name": "المبيعات" }, "amount": "300.00" } ], "total": "300.00" },
  "cost_of_goods": { "rows": [ … ], "total": "100.00" }, "gross_profit": "200.00",
  "expenses": { "rows": [ … ], "total": "40.00" }, "net_profit": "160.00" }
```

---

#### GET /admin/reports/balance-sheet/
الميزانية حتى تاريخ. أرباح الفترات (إيرادات − مصروفات حتى التاريخ) تظهر كـ`current_earnings` ضمن حقوق الملكية.

**Query:** `as_of` (الافتراضي اليوم), `lang`.

**Response `200`**
```json
{ "as_of": "2026-03-31", "currency": "ILS",
  "assets": { "rows": [ { "account": { "uuid": "…", "code": "1110", "name": "الصندوق" }, "amount": "370.00" } ], "total": "370.00" },
  "liabilities": { "rows": [], "total": "0.00" },
  "equity": { "rows": [ … ], "total": "370.00", "current_earnings": "0.00", "total_with_earnings": "370.00" },
  "balanced": true }
```
`balanced`: الأصول = الالتزامات + حقوق الملكية + أرباح الفترة.

---

### لوحة المحاسبة

#### GET /admin/accounting/summary/
ملخص الصفحة الرئيسية للمحاسبة.

- **الصلاحية:** `accounting.view`

**Response `200`**
```json
{ "currency": "ILS", "current_period": { "name": "2026-10", "status": "open" },
  "balances": { "cash_bank": "370.00", "receivables": "0.00", "payables": "0.00" },
  "alerts": { "draft_entries": 0, "own_drafts": 0, "unclosed_past_periods": 9,
              "stale_rates": [ { "currency": "USD", "last_date": "2026-03-01" }, { "currency": "JOD", "last_date": null } ] },
  "recent_entries": [ { …journal_entry بلا lines } ] }
```
`balances` للحسابات المربوطة (`cash`+`bank`، `receivable`، `payable`). `stale_rates`: عملات فعّالة بلا سعر صرف أو أقدم من 7 أيام. `current_period: null` إن لم توجد فترة لتاريخ اليوم.

---

## 11-ب) نقطة البيع (POS)

كاشير للمحل الفعلي: وردية صندوق ← بيع ← إيصال ← إغلاق الوردية. كل المبالغ بالعملة الأساسية **شيكل (ILS)**. كل بيع يتم في **معاملة واحدة**: حركة مخزون `sale_out` لكل صنف يتتبع المخزون + **قيد يومية مرحَّل** تلقائياً (مصدره `pos_sale`)، وإن فشل أي جزء (مخزون غير كافٍ، فترة مغلقة، حساب افتراضي غير مربوط) لا يُسجَّل شيء.

**الصلاحيات:** `sales.pos` تشغيل الكاشير (الوردية والبيع ورؤية **مبيعاته هو فقط**) · `sales.view` رؤية كل المبيعات وسجل الورديات · `sales.discount` منح خصم على سطور البيع · `sales.refund` استرجاع بيع · `sales.credit` **البيع بالدين** على زبون مسجَّل (ويسمح أيضاً بالبحث عن الزبائن وإضافتهم). دور `cashier` الجاهز يملك `sales.pos` و`sales.credit`.

**قواعد لا تتغير:**
| القاعدة | الأثر |
|---|---|
| لا بيع بدون **وردية مفتوحة** للكاشير، وله وردية واحدة مفتوحة فقط | `shift_required` · `shift_already_open` |
| السعر من **قائمة الأسعار الافتراضية للعملة الأساسية**، ولا يُرسَل من الواجهة. الأصناف بلا سعر أو غير الفعّالة لا تُباع | `no_price` · `not_sellable` |
| الأسعار **شاملة الضريبة**. عند اختيار `tax_rate`: الضريبة = `الإجمالي × النسبة ÷ (100 + النسبة)` والإيراد المسجَّل هو الباقي | — |
| الدفع يجب أن يغطي الإجمالي؛ **الباقي يُعاد من النقد فقط** ولا يُحسب دفعة | `payment_insufficient` · `change_exceeds_cash` |
| **البيع بالدين** (`on_credit: true`): ما لا تغطيه الدفعات (أو الإجمالي كله بلا دفعات) يُسجَّل ديناً على **زبون مسجَّل** (`credit_amount`)، مدين **الذمم المدينة**. يحتاج `sales.credit` | `customer_required` · `credit_not_allowed` |
| الخصم لكل سطر بمبلغ لا يتجاوز قيمة السطر، ويحتاج `sales.discount`؛ والإجمالي يجب أن يبقى أكبر من صفر | `discount_not_allowed` · `discount_too_large` · `empty_sale` |
| القيد: مدين الصندوق (للنقد) / البنك (للبطاقة والتحويل) · دائن المبيعات والضريبة المستحقة · ومدين تكلفة المبيعات / دائن المخزون بتكلفة الوقت | يتطلب ربط الحسابات الافتراضية `cash` `bank` `sales` `cogs` `inventory` (و`receivable` للبيع بالدين) (`mapping_missing`) |
| الاسترجاع **كامل** لا جزئي: يعيد المخزون بنفس التكلفة، ويعكس القيد (ومعه الدين)، ويُردّ المال المدفوع عند البيع بنفس طرقه من **صندوق وردية المسترجِع**. فاتورة دين سدّد الزبون جزءاً منها لا تُسترجع من هنا | `shift_required` · `sale_already_refunded` · `sale_has_debt_payments` |
| الإغلاق: `المتوقع = رصيد الافتتاح + نقد المبيعات + نقد دفعات الديون − نقد المسترجعات`، و`الفرق = المعدود − المتوقع` | — |

**ترقيم:** الورديات `SH-000001` والمبيعات `POS-000001`، بلا فجوات.

---

#### GET /admin/pos/config/
كل ما تحتاجه الشاشة عند الفتح: الوردية الحالية والمخازن والتصنيفات ونسب الضريبة وما يسمح به للمستخدم.

- **الصلاحية:** `sales.pos`

**Response `200`**
```json
{
  "currency": "ILS",
  "shift": null,
  "warehouses": [
    {
      "uuid": "8e089d02-5dc8-4954-96ae-0ddc27876f4c",
      "code": "MAIN",
      "name": "Warehouse MAIN"
    }
  ],
  "default_warehouse": "8e089d02-5dc8-4954-96ae-0ddc27876f4c",
  "categories": [],
  "tax_rates": [
    {
      "uuid": "5adbbe4a-3a73-443c-aa4c-7faf1b34c428",
      "name": "VAT",
      "rate": "16.0000"
    }
  ],
  "can_discount": true,
  "can_refund": true,
  "can_credit": true
}
```
`can_credit`: يملك `sales.credit` (يُظهر خيار «تسجيل الباقي ديناً»).

---

#### GET /admin/pos/products/
أصناف قابلة للبيع (منتج `active` فعّال وله سعر افتراضي بالأساسية). مقسّمة صفحات (**24** افتراضياً، `page_size` حتى 100).

- **الصلاحية:** `sales.pos`

**Query**
| المعامل | ملاحظات |
|---|---|
| `q` | يبحث (كل كلمة على حدة) في SKU والباركود واسم المتغيّر والمنتج والماركة |
| `barcode` | تطابق تام (للقارئ الضوئي) |
| `category` | uuid تصنيف (يشمل فروعه) |
| `warehouse` | uuid المخزن لحساب `available` (الافتراضي: المخزن الافتراضي) |
| `page` · `page_size` | |

**Response `200`**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "uuid": "0d4431f2-5476-4d72-81dd-0a329353cee4",
      "sku": "DS-1",
      "barcode": "",
      "name": "Var 0",
      "product": "Dish Soap",
      "brand": "",
      "category": null,
      "image": null,
      "price": "10.00",
      "track_inventory": true,
      "available": "20.000"
    }
  ]
}
```
`available`: `null` للأصناف التي لا تتتبع المخزون. `price` شامل الضريبة ومقرَّب لمنزلتين (هو ما سيُحاسَب به).

---

#### GET /admin/pos/shift/
وردية المستخدم المفتوحة مع ملخصها الحي، أو `{"shift": null, "report": null}`.

- **الصلاحية:** `sales.pos`

**Response `200`**
```json
{
  "shift": {
    "uuid": "7169c629-a50a-4f28-9624-e0395203262b",
    "number": "SH-000001",
    "status": "open",
    "cashier": {
      "uuid": "5fea141e-65d2-4d05-b79d-273b045c010c",
      "full_name": "Staff"
    },
    "warehouse": {
      "uuid": "8e089d02-5dc8-4954-96ae-0ddc27876f4c",
      "code": "MAIN",
      "name": "Warehouse MAIN"
    },
    "opening_cash": "100.00",
    "opened_at": "2026-10-02T08:12:52.752426Z",
    "closed_at": null,
    "counted_cash": null,
    "expected_cash": null,
    "difference": null,
    "close_note": ""
  },
  "report": {
    "sales_count": 1,
    "sales_total": "28.00",
    "discount_total": "2.00",
    "tax_total": "3.86",
    "refunds_count": 0,
    "refunds_total": "0.00",
    "by_method": {
      "cash": "28.00",
      "card": "0.00",
      "transfer": "0.00"
    },
    "refunds_by_method": {
      "cash": "0.00",
      "card": "0.00",
      "transfer": "0.00"
    },
    "credit_total": "25.00",
    "debt_collected_by_method": {
      "cash": "0.00",
      "card": "0.00",
      "transfer": "0.00"
    },
    "expected_cash": "128.00"
  }
}
```
`credit_total` ما سُجّل ديناً على الزبائن في مبيعات هذه الوردية · `debt_collected_by_method` دفعات ديون استلمها صاحب الوردية (النقدي منها داخل في `expected_cash`).

---

#### POST /admin/pos/shift/open/
يفتح وردية (رصيد الصندوق الافتتاحي) على مخزن فعّال.

- **الصلاحية:** `sales.pos`
- **Request:** `{ "warehouse": "<uuid>", "opening_cash": "100" }` (`opening_cash` اختياري، الافتراضي 0)
- **Response `201`:**
```json
{
  "uuid": "7169c629-a50a-4f28-9624-e0395203262b",
  "number": "SH-000001",
  "status": "open",
  "cashier": {
    "uuid": "5fea141e-65d2-4d05-b79d-273b045c010c",
    "full_name": "Staff"
  },
  "warehouse": {
    "uuid": "8e089d02-5dc8-4954-96ae-0ddc27876f4c",
    "code": "MAIN",
    "name": "Warehouse MAIN"
  },
  "opening_cash": "100.00",
  "opened_at": "2026-10-02T08:12:52.752426Z",
  "closed_at": null,
  "counted_cash": null,
  "expected_cash": null,
  "difference": null,
  "close_note": ""
}
```
- **الأخطاء:** `400 shift_already_open` · `400 warehouse_inactive` · `400 validation_error` · `403`.

---

#### POST /admin/pos/shift/close/
يغلق وردية المستخدم بعد عدّ النقد.

- **الصلاحية:** `sales.pos`
- **Request:** `{ "counted_cash": "100", "note": "" }`
- **Response `200`:** `{ "shift": {…}, "report": {…} }` (الوردية بحالة `closed` مع `expected_cash` و`difference`، وملخص الإغلاق):
```json
{
  "sales_count": 1,
  "sales_total": "28.00",
  "discount_total": "2.00",
  "tax_total": "3.86",
  "refunds_count": 1,
  "refunds_total": "28.00",
  "by_method": {
    "cash": "28.00",
    "card": "0.00",
    "transfer": "0.00"
  },
  "refunds_by_method": {
    "cash": "28.00",
    "card": "0.00",
    "transfer": "0.00"
  },
  "expected_cash": "100.00"
}
```
- **الأخطاء:** `400 shift_not_open` · `400 validation_error` · `403`.

---

#### POST /admin/pos/sales/
ينشئ بيعاً مكتملاً (مخزون + قيد في معاملة واحدة).

- **الصلاحية:** `sales.pos` (و`sales.discount` عند وجود خصم، و`sales.credit` للبيع بالدين)

**Request body**
| الحقل | النوع | مطلوب | ملاحظات |
|---|---|---|---|
| `lines` | array | نعم | سطر على الأقل |
| `lines[].variant` | uuid | نعم | متغيّر قابل للبيع |
| `lines[].quantity` | decimal | نعم | > 0 حتى 3 منازل |
| `lines[].discount` | decimal | لا | مبلغ خصم على السطر (الافتراضي 0) |
| `payments` | array | نعم | دفعة على الأقل، أو `[]` عند البيع بالدين كاملاً |
| `payments[].method` | string | نعم | `cash` · `card` · `transfer` |
| `payments[].amount` | decimal | نعم | المبلغ **المُسلَّم** (للنقد يمكن أن يزيد عن المطلوب فيُحسب الباقي) |
| `payments[].reference` | string | لا | رقم الإيصال/التحويل |
| `tax_rate` | uuid | لا | نسبة ضريبة فعّالة (الأسعار شاملة لها) |
| `customer` | uuid | لا | زبون مسجَّل (إلزامي مع `on_credit`) |
| `on_credit` | bool | لا | `true`: ما لا تغطيه `payments` يُسجَّل ديناً على `customer` (الافتراضي `false`) |
| `note` | string | لا | |

```json
{ "lines": [ { "variant": "0d4431f2-…", "quantity": "3", "discount": "2" } ],
  "payments": [ { "method": "cash", "amount": "50" } ], "tax_rate": "5adbbe4a-…" }
```
بيع بالدين (دفع 5 والباقي على الزبون):
```json
{ "lines": [ { "variant": "0d4431f2-…", "quantity": "2" } ],
  "payments": [ { "method": "cash", "amount": "5" } ], "customer": "75b23a5c-…", "on_credit": true }
```
**Response `201`:** كائن البيع (هو نفسه ما يُطبع في الإيصال؛ التكلفة لا تظهر أبداً):
```json
{
  "uuid": "a8f93669-4490-4329-a320-7993cb4b3c12",
  "number": "POS-000001",
  "status": "completed",
  "created_at": "2026-10-02T08:12:52.773590Z",
  "cashier": "Staff",
  "shift": "SH-000001",
  "warehouse": {
    "uuid": "8e089d02-5dc8-4954-96ae-0ddc27876f4c",
    "code": "MAIN",
    "name": "Warehouse MAIN"
  },
  "customer": null,
  "lines": [
    {
      "line_no": 1,
      "sku": "DS-1",
      "name": "Dish Soap · Var 0",
      "quantity": "3.000",
      "unit_price": "10.00",
      "discount": "2.00",
      "line_total": "28.00"
    }
  ],
  "subtotal": "30.00",
  "discount_total": "2.00",
  "total": "28.00",
  "tax": {
    "name": "VAT",
    "rate": "16.0000",
    "amount": "3.86"
  },
  "net_total": "24.14",
  "payments": [
    {
      "method": "cash",
      "amount": "28.00",
      "reference": ""
    }
  ],
  "cash_tendered": "50.00",
  "change_due": "22.00",
  "credit_amount": "0.00",
  "balance_due": "0.00",
  "debt_payments": [],
  "note": "",
  "refund": null,
  "journal_entry": "JV-000001"
}
```
`payments` هي المبالغ **المطبَّقة** (بعد خصم الباقي). `journal_entry` رقم القيد الناتج. `credit_amount` ما سُجّل ديناً على الزبون عند البيع، و`balance_due` المتبقي منه الآن، و`debt_payments` دفعات الزبون التي سدّدت منه: `[{ "number": "RCV-000001", "date": "…", "method": "card", "amount": "3.00" }]`.

**الأخطاء**
| الحالة | code | معناها |
|---|---|---|
| 400 | `shift_required` | لا وردية مفتوحة |
| 400 | `no_price` / `not_sellable` | صنف بلا سعر، أو غير فعّال/غير منشور (`fields.sku`) |
| 400 | `insufficient_stock` | المتاح أقل (`fields`: `sku` `requested` `available`) ولا يتغير شيء |
| 400 | `payment_insufficient` | الدفع لا يغطي الإجمالي (`fields`: `total` `paid`) |
| 400 | `change_exceeds_cash` | الباقي أكبر من النقد المُسلَّم |
| 400 | `discount_too_large` / `empty_sale` / `invalid_quantity` | (`fields.lines["<رقم السطر>"]`) |
| 400 | `period_closed` / `period_not_found` / `mapping_missing` | الفترة المحاسبية أو الربط الافتراضي |
| 400 | `customer_required` | `on_credit` بدون `customer` |
| 403 | `discount_not_allowed` | خصم بدون `sales.discount` |
| 403 | `credit_not_allowed` | بيع بالدين بدون `sales.credit` |
| 404 | `customer_not_found` | |
| 400 | `validation_error` · 403 `permission_denied` | |

---

#### GET /admin/pos/sales/
قائمة المبيعات (الأحدث أولاً) مقسّمة صفحات. **من لا يملك `sales.view` يرى مبيعاته فقط.**

- **الصلاحية:** `sales.view` أو `sales.pos`
- **Query:** `status` (`completed`/`refunded`) · `on_credit=1` (فيها دين) · `shift` (uuid) · `from` / `to` (تاريخ) · `search` (رقم البيع أو اسم/هاتف الزبون) · `page`.

**Response `200`**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "uuid": "a8f93669-4490-4329-a320-7993cb4b3c12",
      "number": "POS-000001",
      "status": "completed",
      "created_at": "2026-10-02T08:12:52.773590Z",
      "cashier": "Staff",
      "shift": "SH-000001",
      "customer": null,
      "items": 3,
      "total": "28.00",
      "credit_amount": "0.00",
      "balance_due": "0.00"
    }
  ]
}
```

---

#### GET /admin/pos/sales/{uuid}/
- **الصلاحية:** `sales.view` أو `sales.pos` (لمبيعاته) · **Response `200`:** كائن البيع الكامل كما في الإنشاء (مع `refund` إن استُرجع) · **الأخطاء:** `404`.

---

#### POST /admin/pos/sales/{uuid}/refund/
استرجاع كامل لبيع.

- **الصلاحية:** `sales.refund` · يتطلب **وردية مفتوحة** للمسترجِع.
- **Request:** `{ "reason": "wrong item" }` · **Response `200`:** كائن البيع بحالة `refunded` و`refund: { "at", "by", "reason" }`.
- **الأخطاء:** `400 reason_required` · `400 sale_already_refunded` · `400 sale_has_debt_payments` (فاتورة دين سدّد الزبون جزءاً منها) · `400 shift_required` · `400 period_closed` / `period_not_found` · `404` · `403`.

---

#### GET /admin/reports/sales/
تقرير مبيعات نقطة البيع بين تاريخين يحدّدهما المستخدم: ملخص، يومي، بالساعة، طرق الدفع، الكاشيرية، التصنيفات، وكل منتج بيع.

- **الصلاحية:** `reports.sales` (والتصدير يحتاج أيضاً `reports.export`). **التكلفة والربح لا تظهر إلا لمن يملك `inventory.view_cost`** وإلا تكون `null` (و`cost_visible: false`).
- **النطاق:** المبيعات التي **أُنشئت** بين `from` و`to` (شاملين، **بتوقيت المحل** `TIME_ZONE` وافتراضيه `Asia/Hebron`؛ وبه تُحسب حدود اليوم وساعة اليوم). **الإيراد والإجماليات للمبيعات المكتملة فقط**؛ المبيعات المسترجعة لا تدخل فيها وتظهر منفصلة في `refunds_count`/`refunds_total`. `net` هو الإيراد **بعد استبعاد الضريبة**، و`total` ما دفعه الزبائن.
- الحد الأقصى للنطاق **732 يوماً**.

**Query**
| المعامل | ملاحظات |
|---|---|
| `from` / `to` | `YYYY-MM-DD`. الافتراضي: أول الشهر الحالي ← اليوم |
| `cashier` | uuid موظف: مبيعاته فقط |
| `warehouse` | uuid مخزن: مبيعات هذا المخزن فقط |
| `export=csv` + `section` | ملف UTF-8 مع BOM. `section`: `daily` (الافتراضي) · `products` · `categories` · `cashiers` · `payments` · `invoices` (سطر لكل فاتورة بما فيها المسترجعة) |

**Response `200`** (مختصر: `hourly` فيه 24 عنصراً دائماً، و`daily` يشمل كل أيام النطاق حتى التي بلا مبيعات)
```json
{
  "from": "2026-10-01",
  "to": "2026-10-03",
  "currency": "ILS",
  "cost_visible": true,
  "summary": {
    "sales_count": 2,
    "items_sold": "4",
    "gross": "40.00",
    "discounts": "2.00",
    "total": "38.00",
    "tax": "4.14",
    "net": "33.86",
    "avg_ticket": "19.00",
    "refunds_count": 1,
    "refunds_total": "20.00",
    "cost": "16.00",
    "profit": "17.86",
    "margin_pct": "52.75"
  },
  "daily": [
    {
      "date": "2026-10-02",
      "sales_count": 2,
      "total": "38.00",
      "net": "33.86",
      "discounts": "2.00",
      "tax": "4.14",
      "refunds_count": 1,
      "refunds_total": "20.00",
      "profit": "17.86"
    }
  ],
  "hourly": [
    {
      "hour": 22,
      "sales_count": 2,
      "total": "38.00"
    },
    {
      "…": "24 entries, one per hour"
    }
  ],
  "payments": [
    {
      "method": "cash",
      "count": 2,
      "amount": "38.00"
    }
  ],
  "cashiers": [
    {
      "uuid": "890736bd-e29e-4b08-a140-4416be09757b",
      "name": "Staff",
      "sales_count": 2,
      "total": "38.00",
      "avg_ticket": "19.00",
      "discounts": "2.00"
    }
  ],
  "categories": [
    {
      "name": "",
      "quantity": "4",
      "orders": 2,
      "revenue": "33.86",
      "cost": "16.00",
      "profit": "17.86"
    }
  ],
  "products": [
    {
      "uuid": "1c2ff8dc-5065-4173-94dd-ace4323f042c",
      "sku": "DS-1",
      "name": "Dish Soap · Var 0",
      "quantity": "4",
      "orders": 2,
      "discount": "2.00",
      "revenue": "33.86",
      "cost": "16.00",
      "profit": "17.86",
      "margin_pct": "52.75"
    }
  ],
  "filters": {
    "cashier": null,
    "warehouse": null
  }
}
```
- `summary.avg_ticket` = الإجمالي ÷ عدد الفواتير. `margin_pct` = الربح ÷ الإيراد الصافي × 100.
- `products[]` و`categories[]` مرتّبان بالإيراد الصافي تنازلياً؛ إيراد السطر = قيمته بعد الخصم مطروحاً منه حصته من ضريبة الفاتورة. `orders` = عدد الفواتير التي ضمّت الصنف.
- `payments[]`: ما طُبِّق على الفواتير فعلاً (الباقي المُعاد ليس دفعة).

**الأخطاء:** `400 invalid_date` · `400 invalid_range` · `400 range_too_large` · `400 export_not_supported` (تنسيق أو `section` غير معروف) · `403 forbidden` (`export` بدون `reports.export`) · `403 permission_denied` · `404 not_found` (`cashier` أو `warehouse` غير موجود) · `401`.

---

#### GET /admin/pos/shifts/
سجل الورديات (الأحدث أولاً) مقسّم صفحات. **الصلاحية:** `sales.view` · **Query:** `status` · `cashier` (uuid) · `page`.

---

#### GET /admin/pos/shifts/{uuid}/
وردية مع تقريرها: `{ "shift": {…}, "report": {…} }`. **الصلاحية:** `sales.view` · **الأخطاء:** `404`.

---

## 12) كتالوج الأخطاء

**الحالة HTTP + `code`.** أكواد النظام الثابتة التي يجب أن تتعامل معها الواجهة:

| الحالة | code | يظهر في | المعنى / ماذا تفعل |
|---|---|---|---|
| 400 | `validation_error` | أي endpoint | حقل مفقود/غير صالح؛ التفاصيل في `fields` |
| 400 | `invalid_phone` | send-code, verify, POST users | رقم غير صالح؛ اطلب تصحيحه |
| 400 | `invalid_code` | verify | رمز خاطئ/منتهٍ/مستخدم/حساب معطّل؛ اطلب رمزاً جديداً بعد 5 أخطاء |
| 400 | `full_name_required` | verify | رقم جديد؛ اعرض حقل الاسم وأعد الطلب بنفس الرمز |
| 400 | `phone_taken` | POST users | الرقم مسجّل؛ استعمل الترقية للزبائن |
| 400 | `invalid_role` | POST users, roles, promote | دور غير موجود |
| 400 | `self_lockout` | deactivate | لا تعطّل نفسك |
| 400 | `last_admin` | deactivate | آخر أدمن |
| 400 | `inactive_account` | promote | فعّل الحساب أولاً |
| 400 | `not_staff` | deactivate/roles | الحساب ليس موظفاً |
| 401 | `not_authenticated` | الموثّقة | لم يُرسل توكن |
| 401 | `token_not_valid` | الموثّقة | access منتهٍ/غير صالح → جدّد ثم أعد الطلب |
| 401 | `invalid_token` | refresh | refresh غير صالح → صفحة الدخول |
| 403 | `permission_denied` | الإدارية | لا تملك الصلاحية المطلوبة (أو أنت زبون) |
| 403 | `cannot_grant` | POST users, roles, promote | تحاول منح ما لا تملكه؛ انظر `fields.permissions` |
| 403 | `forbidden` | users, promote | عملية غير مسموحة لك (مثل تغيير أدوار نفسك) |
| 404 | `not_found` | `{uuid}` | غير موجود أو من نوع آخر |
| 405 | `method_not_allowed` | أي | طريقة HTTP غير مدعومة للمسار |
| 429 | `otp_cooldown` | send-code | انتظر `resend_in` ثانية |
| 429 | `otp_limit` | send-code | 5 رموز/ساعة لهذا الرقم |
| 429 | `throttled` | أي | كثرة طلبات؛ انتظر |
| 502 | `delivery_failed` | send-code | تعذّر إرسال واتساب؛ أعد المحاولة |
| 400 | `base_currency_rate` | POST exchange-rates | العملة الأساسية سعرها 1 دائماً |
| 400 | `invalid_currency` | /store/… | عملة غير مدعومة أو معطّلة في `?currency=` |
| 400 | `invalid_price` | GET /store/products/ | `min_price` أو `max_price` ليس رقماً (أو سالب أو غير منتهٍ) |
| 400 | `category_cycle` | PATCH categories | لا يصح جعل التصنيف تحت أحد فروعه |
| 400 | `brand_taken` | POST/PATCH brands | اسم الماركة مستخدم (دون اعتبار حالة الأحرف) |
| 400 | `brand_in_use` | DELETE brands | للماركة منتجات؛ انقلها أو عطّلها بدل الحذف |
| 400 | `category_in_use` | DELETE categories | له فروع أو منتجات؛ انقلها أو عطّله |
| 400 | `currency_locked` | PATCH price-lists | عليها أسعار فلا تغيّر عملتها |
| 400 | `sku_taken` / `barcode_taken` | منتجات/متغيّرات | مستخدم (حتى لو محذوفاً أو بحالة أحرف مختلفة) |
| 400 | `sku_locked` | PATCH variants | لا يتغير SKU بعد وجود حركة مخزون |
| 400 | `variant_in_use` / `product_in_use` | DELETE | له تاريخ مخزون؛ عطّل/أرشف بدلاً |
| 400 | `last_variant` | DELETE variants | آخر متغيّر فعّال في منتج `active` |
| 400 | `no_variants` | publish | أضف متغيّراً فعّالاً أولاً |
| 400 | `tracking_locked` | PATCH products | لا يُطفأ تتبع المخزون بعد وجود حركات |
| 400 | `product_deleted` | منتجات | المنتج محذوف |
| 400 | `duplicate_price_list` / `invalid_compare_price` / `price_list_inactive` | PUT prices | خطأ في الأسعار |
| 400 | `invalid_image` / `image_too_large` / `variant_mismatch` | POST images | ليست صورة JPEG/PNG/WebP صالحة، أو أكبر من 5MB، أو المتغيّر من منتج آخر |
| 400 | `import_invalid` | POST products/import (استيراد فعلي) | في ملف Excel أخطاء؛ لم يُحفظ شيء. التفاصيل في `errors` (ورقة/صف/عمود/كود) |
| 400 | `invalid_file` / `file_too_large` / `missing_sheet` / `too_many_rows` | POST products/import | الملف ليس `.xlsx` سليماً، أو كبير، أو بلا ورقة «المنتجات»، أو أكثر من 2000 صف |
| 404 | `unknown_products` | POST bulk | بعض المنتجات غير موجودة (في `fields.ids`) |
| 400 | `insufficient_stock` | تحويل/تسوية | المتاح أقل من المطلوب؛ في `fields`: `sku` و`requested` و`available` |
| 400 | `not_tracked` / `variant_deleted` / `warehouse_inactive` | مخزون | الصنف لا يتتبع المخزون، أو محذوف، أو المخزن معطّل |
| 400 | `warehouse_required` | GET stock/count-sheet | لم يُحدَّد مخزن |
| 404 | `warehouse_not_found` | GET stock/count-sheet | uuid مخزن غير موجود |
| 400 | `warehouse_has_stock` | PATCH warehouses | لا يُعطَّل مخزن فيه كمية |
| 400 | `duplicate_line` / `no_lines` | تسوية/تحويل | سطر مكرر أو لا أسطر |
| 400 | `unit_cost_required` | تسوية افتتاحية | الزيادة في الرصيد الافتتاحي تحتاج `unit_cost` |
| 400 | `below_reserved` | تسوية | الكمية المعدودة أقل من المحجوز |
| 400 | `already_posted` | post adjustment | مرحّلة سابقاً |
| 400 | `same_warehouse` | POST transfers | المصدر والوجهة نفس المخزن |
| 400 | `invalid_transfer_state` | receive/cancel | ليس `in_transit` |
| 400 | `unbalanced_entry` | post | القيد غير متوازن؛ `fields`: `debit` `credit` `difference` |
| 400 | `entry_too_few_lines` | post | أقل من سطرين |
| 400 | `account_not_postable` / `account_inactive` | قيود/ربط | حساب تجميعي أو معطّل (مع رقم السطر في `fields.lines`) |
| 400 | `currency_mismatch` / `currency_inactive` | سطر قيد | عملة السطر لا تطابق قيد الحساب أو معطّلة |
| 400 | `invalid_amount` / `invalid_rate` | سطر قيد | مبلغ أو سعر صرف غير موجب |
| 400 | `rate_missing` | سطر قيد | لا يوجد سعر صرف ≤ تاريخ القيد؛ أدخله من العملات |
| 400 | `period_closed` / `period_not_found` | قيود | التاريخ في فترة مغلقة أو غير مُنشأة |
| 400 | `entry_not_draft` | تعديل/حذف | المرحَّل لا يُعدَّل ولا يُحذف؛ اعكسه |
| 400 | `entry_already_posted` / `entry_not_posted` | post/reverse | الحالة لا تسمح |
| 400 | `entry_already_reversed` / `cannot_reverse_reversal` | reverse | معكوس مسبقاً أو قيد عاكس |
| 400 | `reason_required` | reverse/reopen | السبب إلزامي |
| 400 | `lines_required` | PATCH entry | أعد إرسال الأسطر عند تغيير تاريخ قيد متعدد العملات |
| 403 | `cannot_post_own_entry` | post | maker–checker: أنت كاتب القيد |
| 403 | `not_entry_owner` | PATCH/DELETE entry | المسودة لكاتبها أو الأدمن |
| 400 | `code_taken` | accounts | كود حساب مستخدم |
| 400 | `parent_cycle` / `parent_type_mismatch` / `parent_has_entries` | accounts | أب غير صالح |
| 400 | `account_in_use` / `system_account_locked` / `account_has_children` / `account_mapped` | PATCH account | حقل مقفل أو حساب مربوط/له فروع |
| 400 | `year_exists` | generate | فترات هذه السنة موجودة |
| 400 | `period_has_drafts` / `trial_balance_unbalanced` / `period_already_closed` / `period_not_closed` | close/reopen | شروط الإغلاق وإعادة الفتح |
| 400 | `mapping_type_mismatch` / `unknown_mapping_key` | account-mappings | نوع الحساب لا يطابق المفتاح |
| 400 | `tax_account_type` | tax-rates | نوع حساب الضريبة خاطئ |
| 400 | `shift_required` / `shift_already_open` / `shift_not_open` | pos | لا وردية مفتوحة · وردية مفتوحة مسبقاً · الوردية ليست مفتوحة |
| 400 | `no_price` / `not_sellable` | pos/sales | صنف بلا سعر افتراضي، أو غير فعّال/غير منشور |
| 400 | `payment_insufficient` / `change_exceeds_cash` | pos/sales | الدفع لا يغطي الإجمالي (`fields`: `total` `paid`) · الباقي أكبر من النقد |
| 400 | `discount_too_large` / `empty_sale` | pos/sales | الخصم أكبر من السطر · إجمالي البيع ليس أكبر من صفر |
| 400 | `sale_already_refunded` | pos refund | البيع مسترجع مسبقاً |
| 400 | `mapping_missing` | pos/sales | حساب افتراضي غير مربوط (`cash` `bank` `sales` `cogs` `inventory`، و`receivable` للدين) |
| 400 | `invalid_payment_method` / `invalid_quantity` / `tax_inactive` | pos/sales | طريقة دفع غير معروفة · كمية غير صالحة · ضريبة معطّلة |
| 403 | `discount_not_allowed` | pos/sales | خصم بدون `sales.discount` |
| 403 | `credit_not_allowed` | pos/sales | بيع بالدين بدون `sales.credit` |
| 400 | `customer_required` | pos/sales | بيع بالدين بلا زبون |
| 400 | `sale_has_debt_payments` | pos refund | فاتورة دين سدّد الزبون جزءاً منها |
| 400 | `payment_exceeds_debt` / `no_debt` | customers payments | الدفعة أكبر من الدين (`fields`: `amount` `owed`) · لا دين على الزبون |
| 409 | `customer_exists` | customers | الرقم لزبون مسجَّل أصلاً |
| 404 | `customer_not_found` | pos/sales | زبون غير موجود |
| 400 | `range_too_large` | reports/sales | نطاق التقرير أكبر من 732 يوماً |
| 400 | `invalid_date` / `invalid_range` / `invalid_page` / `account_required` / `export_not_supported` | reports | معاملات تقرير غير صالحة |

---

## 13) سيناريوهات كاملة

### أ) زبون جديد
```
POST /auth/whatsapp/send-code   {"phone":"0591234567"}                       → 200
POST /auth/whatsapp/verify      {"phone":"0591234567","code":"483920"}       → 400 full_name_required
POST /auth/whatsapp/verify      {"phone":"0591234567","code":"483920","full_name":"Sara"} → 201 (created:true)
GET  /auth/me                                                                → 200
```

### ب) تحويل زبون إلى موظف وإعطاؤه صلاحيات
```
GET  /admin/customers/?search=sara                   → 200  (اختر uuid)
GET  /admin/roles                                    → 200  (اختر role ids)
POST /admin/customers/{uuid}/promote/  {"role_ids":[3]} → 200  (+ رسالة واتساب)
   ... لاحقاً لتغيير صلاحياته:
PUT  /admin/users/{uuid}/roles/        {"role_ids":[3,2]} → 200
GET  /admin/users/{uuid}/effective-permissions/      → 200  (مراجعة)
```
عند الموظف: أي `GET /auth/me` يعيد `user_type:"staff"` والصلاحيات الجديدة، بدون إعادة دخول.

### ج) إضافة موظف جديد برقمه مباشرة
```
POST /admin/users/  {"phone":"0592222222","full_name":"Wally","role_ids":[2]} → 201 + رسالة واتساب
الموظف: send-code → verify → user_type = "staff"
```

### د) إيقاف موظف
```
POST /admin/users/{uuid}/deactivate/   → 200   (تنتهي جلساته فوراً)
POST /admin/users/{uuid}/activate/     → 200   (يعود، يحتاج دخولاً جديداً)
```

### هـ) إنشاء أول أدمن (من الـ terminal وليس API)
```bash
python manage.py sync_permissions
python manage.py create_admin --phone 0591234567 --name "Admin"
```
ثم يدخل بواتساب كغيره. الأمر آمن للتكرار (إن وُجد الرقم يرقّيه لأدمن).

### و) التعامل مع انتهاء التوكن (في الواجهة)
```
طلب → 401 token_not_valid → POST /auth/refresh {"refresh":…}
    ├─ 200 → احفظ التوكنين الجديدين وأعد الطلب الأصلي مرة واحدة
    └─ 401 invalid_token → امسح التوكنات وافتح صفحة الدخول
```

### ز) تجربة سريعة بـ curl
```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/whatsapp/send-code \
  -H "Content-Type: application/json" -d '{"phone":"0591234567"}'

curl -X POST http://127.0.0.1:8000/api/v1/auth/whatsapp/verify \
  -H "Content-Type: application/json" -d '{"phone":"0591234567","code":"483920"}'

curl http://127.0.0.1:8000/api/v1/auth/me -H "Authorization: Bearer <access>"
```
في وضع التطوير `WHATSAPP_BACKEND=console` يظهر الرمز في نافذة `runserver` بدل واتساب.

### ح) إضافة منتج وإظهاره للزبائن
```
POST /admin/price-lists/             {"name":"Retail ILS","currency":"ILS","is_default":true}   → 200/201 (مرة واحدة)
POST /admin/products/                {name, variants:[{sku, prices:[{price_list, price}]}]}     → 201 (draft، مخفي)
POST /admin/warehouses/              {"code":"MAIN","name":"Main","is_default":true}            → 201 (مرة واحدة)
POST /admin/stock/adjustments/       {reason:"opening", post:true, lines:[{variant, counted_qty, unit_cost}]} → 201 (رصيد وتكلفة)
POST /admin/products/{uuid}/images/  (multipart)                                               → 201
POST /admin/products/{uuid}/publish/                                                           → 200  (status=active)
POST /admin/products/{uuid}/show/                                                              → 200  (يظهر للزبائن)
GET  /store/products/                                                                          → المنتج ظاهر
```
لإخفائه مؤقتاً: `POST /admin/products/{uuid}/hide/` (يختفي من `/store/…` فوراً). للإيقاف النهائي: `archive`.

### ط) جرد دوري
```
POST /admin/stock/adjustments/  {warehouse, reason:"count", lines:[{variant, counted_qty}]}   → 201 draft (راجعها)
POST /admin/stock/adjustments/{uuid}/post/                                                     → 200 (تُسجَّل فروقات العدّ فقط)
GET  /admin/stock/movements/?variant={uuid}                                                    → تدقيق الحركات
```

### ي) نقل بضاعة بين مخزنين
```
POST /admin/stock/transfers/                {from_warehouse, to_warehouse, lines}  → 201 (in_transit، خرجت من المصدر)
POST /admin/stock/transfers/{uuid}/receive/                                        → 200 (دخلت الوجهة)
   أو  POST .../cancel/                                                            → 200 (عادت للمصدر)
```

### ك) دورة قيد يدوي كاملة
```
POST /admin/journal-entries/        {date, memo, lines:[{account, side, amount, currency?}]}  → 201 draft (كاتب أ)
POST /admin/journal-entries/{uuid}/post/                                                   → 403 cannot_post_own_entry (أ)
POST /admin/journal-entries/{uuid}/post/                                                   → 200 posted (مستخدم آخر ب أو أدمن)
POST /admin/journal-entries/{uuid}/reverse/   {reason, date?}                              → 201 (قيد عاكس، والأصلي reversed)
```

### ل) إغلاق شهر
```
GET  /admin/fiscal-periods/{uuid}/close-checklist/     → can_close ؟ (لا مسودات، الميزان متوازن، الأسعار موجودة)
GET  /admin/reports/trial-balance/?from=&to=            → مراجعة الأرصدة ("balanced": true)
POST /admin/fiscal-periods/{uuid}/close/                → 200 closed (لا إنشاء/ترحيل بعدها)
POST /admin/fiscal-periods/{uuid}/reopen/ {reason}      → أدمن فقط عند الحاجة
```

### م) تهيئة المحاسبة لأول مرة (terminal)
```bash
python manage.py seed_accounting --year 2026    # الدليل + الربط + 12 فترة
python manage.py seed_currencies                # ILS/USD/JOD (إن لم تُهيَّأ)
```
ثم أدخل أسعار الصرف (`POST /admin/exchange-rates/`) وأضف نسب الضرائب (`POST /admin/tax-rates/`).

---

## 14) كيف توثّق endpoint جديداً

عند إضافة أي endpoint (مسار جديد أو طريقة HTTP جديدة على مسار موجود):

1. **أضف قسماً** في المكان المناسب من هذا الملف بعنوان من المستوى الرابع بالضبط:
   ```
   #### METHOD /path/relative/to/api/v1
   ```
   - الأفعال: `GET` `POST` `PUT` `PATCH` `DELETE`.
   - المسار نسبة إلى `/api/v1` ويُكتب المتغيّر بصيغة `{uuid}` مثل `/admin/users/{uuid}/deactivate/`، والشرطة المائلة في النهاية تطابق المسار الفعلي تماماً.
   - طريقتان على نفس المسار = عنوانان منفصلان (مثل `GET /auth/me` و`PATCH /auth/me`).
2. **استعمل هذا القالب** تحت العنوان:
   ```
   وصف: ماذا يفعل وما أثره (وهل يرسل واتساب / يكتب في سجل التدقيق).
   - التوثيق: مطلوب/غير مطلوب · الصلاحية: `module.action`
   Query / Request body: جدول (الحقل، النوع، مطلوب، ملاحظات) + مثال JSON.
   Response `200`: مثال JSON **حقيقي** (شغّل الـ endpoint وانسخ الرد).
   الأخطاء: جدول (الحالة، code، معناها).
   ```
3. **حدّث ما يتأثر:** كتالوج الأخطاء (القسم 12) إن أضفت `code` جديداً، وكتالوج الصلاحيات (القسم 2) إن استعملت صلاحية جديدة، والفهرس/السيناريوهات إن لزم.
4. **شغّل الاختبارات** `python -m pytest apps`. اختبار `test_api_docs` يقارن المسارات الفعلية بالعناوين `####` هنا ويفشل بقائمة المفقود/الزائد.
5. الـ endpoint الإداري يجب أن يعلن `required_permission` (وإلا يُرفض دائماً)، ويجب أن يغطيه اختبار صلاحيات.

---

## 15) ملحقات

### مخطط OpenAPI التفاعلي
مخطط آلي بصيغة OpenAPI 3: `GET http://127.0.0.1:8000/api/schema/` (للأدوات مثل Postman/Swagger عند استيراده). هو مولَّد من الكود ويُكمّل هذا الملف ولا يغني عنه (الوصف والأمثلة والسيناريوهات هنا).

### سجل التدقيق (Audit Log)
العمليات الحساسة تُسجَّل (من فعل، متى، IP) ولا يمكن تعديلها أو حذفها: `auth.login`, `auth.register`, `otp.sent`, `otp.failed`, `staff.created`, `staff.promoted`, `staff.deactivated`, `staff.activated`, `staff.roles_changed`, `staff.notified`, `staff.notify_failed`, `currency.rate_set`, `category.saved`, `category.deleted`, `price_list.saved`, `product.created`, `product.updated`, `product.published`, `product.archived`, `product.shown`, `product.hidden`, `product.deleted`, `product.image_added`, `product.image_deleted`, `variant.created`, `variant.updated`, `variant.deleted`, `variant.prices_set`, `warehouse.saved`, `stock.adjustment_created`, `stock.adjustment_posted`, `stock.transfer_created`, `stock.transfer_received`, `stock.transfer_cancelled`, `account.created`, `account.updated`, `mapping.updated`, `tax_rate.saved`, `period.generated`, `period.closed`, `period.reopened`, `journal.created`, `journal.updated`, `journal.deleted`, `journal.posted`, `journal.reversed`. لا يُسجَّل الرمز نفسه أبداً.

### الإعدادات المؤثرة (في `.env`)
| المتغير | معناه |
|---|---|
| `WHATSAPP_BACKEND` | `eksir` إرسال حقيقي · `console` يطبع الرمز في الـ terminal (تجربة) |
| `WHATSAPP_API_KEY` | مفتاح eksir (سري، لا يوضع في الكود) |
| `DEFAULT_COUNTRY_CODE` | مقدّمة الدولة للأرقام المحلية (افتراضي `970`) |

الأوامر: `python manage.py sync_permissions` (الصلاحيات والأدوار) · `python manage.py seed_currencies` (ILS/USD/JOD) · `python manage.py seed_accounting` (دليل الحسابات والربط والفترات) · `python manage.py create_admin --phone … --name …`.

### التخطيط مقابل الواقع
ملف [04-api-spec.md](04-api-spec.md) يصف **الخطة** الكاملة للنظام (بما فيها وحدات لم تُبنَ بعد). هذا الملف يصف **ما هو موجود وشغّال فعلاً**.
