import re

from lingua import Language, LanguageDetectorBuilder


# Languages to detect using Lingua.
LANGUAGES = [
    Language.ENGLISH,
    Language.HINDI,
    Language.BENGALI,
    Language.JAPANESE,
    Language.FRENCH,
    Language.SPANISH,
    Language.GERMAN,
]

detector = (
    LanguageDetectorBuilder
    .from_languages(*LANGUAGES)
    .with_preloaded_language_models()
    .build()
)


# Common Romanized Hindi words and spelling variations.
ROMANIZED_HINDI_WORDS = {
    "aap", "ap", "tum", "tumhe", "tumhara", "tumhari",
    "kaise", "kaisa", "kaisi", "kese", "kaisey",
    "hai", "hain", "ho", "hu", "hun",
    "tha", "thi", "the", "raha", "rahi", "rahe",
    "kya", "kyu", "kyun", "kyunki", "kaun", "kahan",
    "kab", "aisa", "waisa", "accha", "acha", "achha",
    "bahut", "bohot", "nahi", "nhi", "nahin",
    "haan", "han", "ji", "mera", "meri", "mere",
    "tera", "teri", "tere", "hamara", "humara",
    "mujhe", "mujhse", "tujhe", "tumse", "usse",
    "isse", "aaj", "kal", "abhi", "phir", "fir",
    "kar", "karo", "kare", "karen", "karta", "karte",
    "karna", "karni", "bol", "bolo", "batao", "btao",
    "samajh", "samjha", "samjhi", "pata", "chahiye",
    "liye", "gaya", "gayi", "gaye", "milte", "milna",
    "baat", "dost", "yaar", "bhai", "behen", "ladka",
    "ladki", "acchi", "achhi", "theek", "thik", "mast",
    "sahi", "apna", "apni", "apne", "kuch", "sab",
    "sabhi", "mat", "bhi", "toh", "aur", "lekin",
    "magar", "agar", "jab", "tab", "wala", "wali",
    "wale", "mein", "kahan", "ka", "ki", "ke", "ko",
    "se", "par", "pe", "n", "hn", "kr", "krna",
}

# Common English words, useful for identifying mixed messages.
ENGLISH_WORDS = {
    "the", "is", "are", "was", "were", "you", "your",
    "what", "when", "where", "why", "how", "who",
    "hello", "hey", "thanks", "thank", "good", "morning",
    "night", "today", "tomorrow", "because", "have", "has",
    "this", "that", "with", "from", "for", "and", "but",
    "not", "doing", "going", "come", "coming", "want",
    "need", "about", "fine", "okay", "ok", "friend",
}


def tokenize(text: str) -> list[str]:
    """Extract lowercase Latin-script words."""
    return re.findall(r"[a-z]+", text.lower())


def detect_language(text: str) -> tuple[str, float, str]:
    """Detect supported scripts, Romanized Hindi, and mixed messages."""

    text = text.strip()

    if not text:
        return "Unknown", 0.0, "Empty message"

    # Identify script-based languages BEFORE extracting Latin words.
    if re.search(r"[\u0900-\u097F]", text):
        return "Hindi", 0.99, "Devanagari script"

    if re.search(r"[\u0980-\u09FF]", text):
        return "Bengali", 0.99, "Bengali script"

    if re.search(r"[\u3040-\u30FF\u4E00-\u9FFF]", text):
        return "Japanese", 0.99, "Japanese script"

    words = tokenize(text)

    if not words:
        return "Unknown", 0.0, "No recognizable words"

    word_set = set(words)
    hindi_matches = word_set & ROMANIZED_HINDI_WORDS
    english_matches = word_set & ENGLISH_WORDS

    strong_hindi = hindi_matches - {
        "ka", "ki", "ke", "ko", "se", "par", "pe",
        "bhi", "toh", "aur", "n", "hn",
    }

    # Recognize mixed Hindi-English messages.
    if strong_hindi and len(english_matches) >= 2:
        return (
            "Mixed Hindi-English",
            0.75,
            f"Hindi: {sorted(strong_hindi)}; "
            f"English: {sorted(english_matches)}",
        )

    # Romanized Hindi needs multiple indicators for stronger confidence.
    if len(strong_hindi) >= 2:
        confidence = min(0.95, 0.65 + 0.07 * len(strong_hindi))
        return (
            "Hindi (Romanized)",
            confidence,
            f"Matched: {sorted(strong_hindi)}",
        )

    if len(strong_hindi) == 1 and len(words) <= 4:
        return (
            "Hindi (Romanized)",
            0.65,
            f"Single indicator: {sorted(strong_hindi)}",
        )

    # Avoid assigning a language to very short Latin-script messages.
    if len(words) == 1:
        return "Unknown", 0.0, "Too little text to identify reliably"

    language = detector.detect_language_of(text)
    confidence_values = detector.compute_language_confidence_values(text)

    if language is None:
        return "Unknown", 0.0, "Language could not be determined"

    confidence = next(
        (
            item.value
            for item in confidence_values
            if item.language == language
        ),
        0.0,
    )

    # A low-confidence result is not reliable enough for auto-translation.
    if confidence < 0.45:
        return (
            "Unknown",
            confidence,
            f"Low-confidence Lingua result: {language.name}",
        )

    return language.name.title(), confidence, "Lingua detection"


if __name__ == "__main__":
    samples = [
        "How are you doing today?",
        "आप कैसे हैं?",
        "aap kese hai",
        "aap kaise hai",
        "aap kaise ho",
        "mujhe tumse baat karni hai",
        "kya kar rahe ho",
        "kal milte hain",
        "main fine hu, what about you?",
        "aap good ho bro",
        "I am fine, how are you?",
        "আপনি কেমন আছেন?",
        "今日は元気ですか？",
        "Comment allez-vous ?",
        "¿Cómo estás?",
        "lol",
        "😂",
    ]

    for sample in samples:
        language, confidence, reason = detect_language(sample)

        print(f"\nText:       {sample}")
        print(f"Language:   {language}")
        print(f"Confidence: {confidence:.2f}")
        print(f"Reason:     {reason}")
