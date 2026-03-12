"""Whisper language constants shared by transcriber and preferences_dialog."""
from __future__ import annotations

DEFAULT_LANGUAGE = "en"

# All languages supported by Whisper, as (display_name, language_code) pairs.
WHISPER_LANGUAGES: list[tuple[str, str]] = sorted([
    ("Afrikaans", "af"), ("Albanian", "sq"), ("Amharic", "am"),
    ("Arabic", "ar"), ("Armenian", "hy"), ("Assamese", "as"),
    ("Azerbaijani", "az"), ("Bashkir", "ba"), ("Basque", "eu"),
    ("Belarusian", "be"), ("Bengali", "bn"), ("Bosnian", "bs"),
    ("Breton", "br"), ("Bulgarian", "bg"), ("Burmese", "my"),
    ("Catalan", "ca"), ("Chinese", "zh"), ("Croatian", "hr"),
    ("Czech", "cs"), ("Danish", "da"), ("Dutch", "nl"),
    ("English", "en"), ("Estonian", "et"), ("Faroese", "fo"),
    ("Finnish", "fi"), ("French", "fr"), ("Galician", "gl"),
    ("Georgian", "ka"), ("German", "de"), ("Greek", "el"),
    ("Gujarati", "gu"), ("Haitian Creole", "ht"), ("Hausa", "ha"),
    ("Hawaiian", "haw"), ("Hebrew", "he"), ("Hindi", "hi"),
    ("Hungarian", "hu"), ("Icelandic", "is"), ("Indonesian", "id"),
    ("Italian", "it"), ("Japanese", "ja"), ("Javanese", "jw"),
    ("Kannada", "kn"), ("Kazakh", "kk"), ("Khmer", "km"),
    ("Korean", "ko"), ("Lao", "lo"), ("Latin", "la"),
    ("Latvian", "lv"), ("Lingala", "ln"), ("Lithuanian", "lt"),
    ("Luxembourgish", "lb"), ("Macedonian", "mk"), ("Malagasy", "mg"),
    ("Malay", "ms"), ("Malayalam", "ml"), ("Maltese", "mt"),
    ("Maori", "mi"), ("Marathi", "mr"), ("Mongolian", "mn"),
    ("Nepali", "ne"), ("Norwegian", "no"), ("Nynorsk", "nn"),
    ("Occitan", "oc"), ("Pashto", "ps"), ("Persian", "fa"),
    ("Polish", "pl"), ("Portuguese", "pt"), ("Punjabi", "pa"),
    ("Romanian", "ro"), ("Russian", "ru"), ("Sanskrit", "sa"),
    ("Serbian", "sr"), ("Shona", "sn"), ("Sindhi", "sd"),
    ("Sinhala", "si"), ("Slovak", "sk"), ("Slovenian", "sl"),
    ("Somali", "so"), ("Spanish", "es"), ("Sundanese", "su"),
    ("Swahili", "sw"), ("Swedish", "sv"), ("Tagalog", "tl"),
    ("Tajik", "tg"), ("Tamil", "ta"), ("Tatar", "tt"),
    ("Telugu", "te"), ("Thai", "th"), ("Tibetan", "bo"),
    ("Turkish", "tr"), ("Turkmen", "tk"), ("Ukrainian", "uk"),
    ("Urdu", "ur"), ("Uzbek", "uz"), ("Vietnamese", "vi"),
    ("Welsh", "cy"), ("Yiddish", "yi"), ("Yoruba", "yo"),
], key=lambda x: x[0])
