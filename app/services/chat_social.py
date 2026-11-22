"""Bounded social replies that cannot introduce knowledge outside project notes."""

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
        "Hi! I'm FIKRAH, your personal knowledge assistant. How can I help with your project notes?",
    ),
    (
        ("اهلا", "اهلا وسهلا", "مرحبا", "هاي", "هلا", "صباح الخير", "مساء الخير"),
        "أهلًا! أنا مساعد FIKRAH لمعرفتك الشخصية. أقدر أساعدك إزاي في نوتس المشروع؟",
    ),
    (
        ("السلام عليكم", "السلام عليكم ورحمة الله وبركاته"),
        "وعليكم السلام! أهلًا بيك في FIKRAH. أقدر أساعدك إزاي في نوتس المشروع؟",
    ),
    (
        ("how are you", "how are you doing", "how s it going", "hello how are you"),
        "I'm ready to help! What would you like to explore in your project notes?",
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
        "أنا جاهز أساعدك! تحب نراجع إيه في نوتس المشروع؟",
    ),
    (
        (
            "who are you",
            "what are you",
            "introduce yourself",
            "what can you do",
            "how can you help",
            "help",
        ),
        "I'm FIKRAH, your personal knowledge assistant. I can summarize your project notes and answer questions with note sources. If your notes don't support an answer, I'll say so.",
    ),
    (
        (
            "انت مين",
            "مين انت",
            "من انت",
            "عرفني بنفسك",
            "عرف نفسك",
            "تقدر تعمل ايه",
            "بتعمل ايه",
            "بتساعدني ازاي",
            "ممكن تساعدني في ايه",
            "مساعدة",
        ),
        "أنا مساعد FIKRAH لمعرفتك الشخصية. أقدر ألخّص نوتس المشروع وأجاوب على أسئلتك بمصادر منها. ولو النوتس مش بتدعم إجابة، هقولك بوضوح.",
    ),
    (
        ("thanks", "thank you", "thank you very much"),
        "You're welcome! I'm here to help with your project notes.",
    ),
    (
        ("شكرا", "شكرا لك", "تسلم", "متشكر"),
        "العفو! أنا موجود لو حابب ترجع لنوتس المشروع.",
    ),
    (
        ("bye", "goodbye", "see you"),
        "Goodbye! Your project notes will be here when you return.",
    ),
    (
        ("مع السلامة", "باي", "الي اللقاء"),
        "مع السلامة! تقدر ترجع لنوتس المشروع وقت ما تحب.",
    ),
)

_SOCIAL_REPLIES = {
    _normalize(phrase): reply for phrases, reply in _REPLY_GROUPS for phrase in phrases
}


def social_reply(message: str) -> str | None:
    """Return approved copy only for a complete, recognized social utterance."""
    return _SOCIAL_REPLIES.get(_normalize(message))
