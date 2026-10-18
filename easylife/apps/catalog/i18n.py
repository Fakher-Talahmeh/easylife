"""Per-language catalog text: Arabic, English, Hebrew with the default `name` / `description` as the fallback."""

LANGS = ("ar", "en", "he")
SUFFIXES = tuple(f"_{lang}" for lang in LANGS)


def translated(obj, field, lang):
    """`field` of `obj` in `lang` (e.g. name_he), falling back to the default value when that translation is empty."""
    if lang in LANGS:
        value = getattr(obj, f"{field}_{lang}", "")
        if value:
            return value
    return getattr(obj, field)


def requested_lang(request):
    """`?lang=ar|en|he`; anything else (or nothing) means "the default text"."""
    lang = (request.query_params.get("lang") or "").lower()
    return lang if lang in LANGS else None
