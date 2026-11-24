"""Page languages: the visitor picks one at the top right, and every text on the page comes from messages.py.

The language is set once per script run (app.main) and kept per thread, because Streamlit runs each visitor's
script run in its own thread; the pure helpers in theme.py, portions.py and explain.py read it through t(), so their
signatures stay the same. Nothing here imports Streamlit. Traditional Chinese stays the default, so the tests and
the CLI summary, which never set a language, keep their Chinese text.
"""
import threading

from .messages import MESSAGES

LANGUAGES = {'zh-TW': '繁體中文', 'en': 'English', 'ja': '日本語', 'ko': '한국어', 'id': 'Bahasa Indonesia',
             'vi': 'Tiếng Việt'}  # each name in its own language, as the picker shows it
DEFAULT = 'zh-TW'
FALLBACK = 'en'  # for texts written only in Chinese and English (developer details, rare diagnostic reasons)
NOTES_LANGUAGE = {'zh-TW': 'Traditional Chinese', 'en': 'English', 'ja': 'Japanese', 'ko': 'Korean',
                  'id': 'Indonesian', 'vi': 'Vietnamese'}  # how Gemini is asked to write its notes

_state = threading.local()


def set_language(code):
    """Use code for this script run (an unknown code means the default); returns the language in use."""
    _state.code = code if code in LANGUAGES else DEFAULT
    return _state.code


def language():
    return getattr(_state, 'code', DEFAULT)


def from_locale(locale):
    """Browser language ('ja-JP', 'zh-Hant-TW', 'en-US') -> a page language, or None when none of them fits."""
    if not isinstance(locale, str) or not locale.strip():
        return None
    base = locale.strip().replace('_', '-').split('-')[0].lower()
    if base == 'zh':
        return 'zh-TW'  # Simplified Chinese readers read Traditional Chinese better than English
    if base == 'in':  # the old code for Indonesian, still sent by some Android browsers
        base = 'id'
    return base if base in LANGUAGES else None


def known(key):
    return key in MESSAGES


def t(key, default=None, **values):
    """Text for key in the current language, with its {placeholders} filled from values.

    Order: the current language, then English, then Chinese. default is the Chinese text that an existing table
    keeps (food names, ingredients, notes, reasons) when messages.py holds only the other languages; an unknown key
    shows itself, so a missing text is visible instead of blank.
    """
    entry = MESSAGES.get(key, {})
    code = language()
    if code == DEFAULT:
        text = entry.get(DEFAULT, default)
    else:
        text = entry.get(code) or entry.get(FALLBACK) or entry.get(DEFAULT) or default
    if text is None:
        return key
    return text.format(**values) if values else text


def join(names):
    """Names joined with this language's list separator (、 in Chinese and Japanese, a comma elsewhere)."""
    return t('sep.list').join(names)
