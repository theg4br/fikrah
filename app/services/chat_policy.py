"""Deterministic social replies and progressive note-presentation policy."""

import re
import unicodedata


def _normalize(message: str) -> str:
    text = unicodedata.normalize("NFKC", message).casefold()
    text = "".join(
        character
        for character in text
        if unicodedata.category(character) != "Mn" and character != "ـ"
    )
    text = text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


# Match the entire message, never a greeting prefix or an LLM-provided label.
# Unknown, mixed, and knowledge-bearing requests keep the grounded chat path.
_REPLY_GROUPS = (
    (
        ("hi", "hello", "hey", "good morning", "good evening"),
        "Hi! How can I help?",
    ),
    (
        ("اهلا", "اهلا بيك", "اهلا وسهلا", "مرحبا", "هاي", "هلا"),
        "أهلًا بيك!",
    ),
    (
        ("صباح الخير", "صباح النور", "صباح الورد", "صباح الفل"),
        "صباح النور!",
    ),
    (
        ("مساء الخير", "مساء النور", "مساء الورد", "مساء الفل"),
        "مساء النور!",
    ),
    (
        ("السلام عليكم", "السلام عليكم ورحمة الله وبركاته"),
        "وعليكم السلام! أهلًا بيك.",
    ),
    (
        ("how are you", "how are you doing", "how s it going", "hello how are you"),
        "I'm here and ready to help! How are you?",
    ),
    (
        (
            "عامل ايه",
            "انت عامل ايه",
            "ازيك",
            "اخبارك ايه",
            "كيف حالك",
            "كيفك",
            "اهلا عامل ايه",
        ),
        "أنا جاهز أساعدك! إنت أخبارك إيه؟",
    ),
    (
        (
            "who are you",
            "what are you",
            "introduce yourself",
        ),
        "I'm FIKRAH, your personal knowledge assistant.",
    ),
    (
        (
            "what can you do",
            "how can you help",
            "help",
        ),
        "I can summarize your project notes and answer questions using their sources. If they don't support an answer, I'll say so.",
    ),
    (
        (
            "انت مين",
            "مين انت",
            "من انت",
            "عرفني بنفسك",
            "عرف نفسك",
        ),
        "أنا FIKRAH، مساعدك الشخصي لتنظيم المعرفة والرجوع لها.",
    ),
    (
        (
            "تقدر تعمل ايه",
            "بتعمل ايه",
            "بتساعدني ازاي",
            "ممكن تساعدني في ايه",
            "مساعدة",
        ),
        "أقدر ألخّص نوتس المشروع وأجاوب على أسئلتك بمصادر منها. ولو مش بتدعم إجابة، هقولك بوضوح.",
    ),
    (
        ("thanks", "thank you", "thank you very much"),
        "You're welcome!",
    ),
    (
        ("شكرا", "شكرا لك", "تسلم", "متشكر"),
        "العفو!",
    ),
    (
        ("bye", "goodbye", "see you"),
        "Goodbye!",
    ),
    (
        ("مع السلامة", "باي", "الي اللقاء"),
        "مع السلامة!",
    ),
)

_SOCIAL_REPLIES = {
    _normalize(phrase): reply for phrases, reply in _REPLY_GROUPS for phrase in phrases
}


def social_reply(message: str) -> str | None:
    """Return approved copy only for a complete, recognized social utterance."""
    return _SOCIAL_REPLIES.get(_normalize(message))


def is_continuation(message: str) -> bool:
    return _normalize(message) in {
        "كمل",
        "كمللي",
        "كمل الباقي",
        "كمل النوتس",
        "تابع",
        "الباقي",
        "المزيد",
        "continue",
        "more",
        "show more",
        "next",
        "next page",
        "go on",
    }


def is_note_overview(message: str) -> bool:
    """Limit presentation requests, without granting permission to answer them."""
    words = set(_normalize(message).split())
    return bool(
        words
        & {
            "لخص",
            "لخصلي",
            "تلخيص",
            "ملخص",
            "اعرض",
            "وريني",
            "summarize",
            "summarise",
            "summary",
            "overview",
            "list",
        }
    )
