"""
Guardrails.

1. Input validation    questions are trimmed, control characters removed, length bounded.
2. Prompt injection    retrieved passages are untrusted data. Defence in layers:
                         a. passages that look like instructions to the model are dropped
                            from the LLM context (and reported in the response warnings);
                         b. every passage is wrapped in <document> tags with any closing
                            tag inside the text neutralised, so it cannot "break out";
                         c. the system prompt states that documents are data, never
                            instructions, and that only the documents may be used.
                       The question itself is also checked; a flagged question is answered
                       normally but the flag is logged.
3. PII masking         emails and phone numbers are masked in every log line (logging
                       filter) and in SQL result rows returned to the caller.
"""

from __future__ import annotations

import logging
import re
import unicodedata

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# international and local formats: +91 9812345678, (555) 123-4567, 020 7946 0958 ...
PHONE_RE = re.compile(r"(?<![\w\-])(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,5}\)?[\s.\-]?)?\d{3,5}[\s.\-]?\d{3,5}(?![\w\-])")

INJECTION_PATTERNS = {
    "ignore_instructions": re.compile(r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|earlier|system)\b.{0,20}\b(instructions?|prompts?|rules?|context)\b", re.I | re.S),
    "role_reassignment": re.compile(r"\byou are (now|no longer)\b|\bact as (an?|the)\b.{0,30}\b(admin|developer|system|jailbr)", re.I),
    "system_prompt_probe": re.compile(r"\b(reveal|print|show|repeat|output)\b.{0,30}\b(system prompt|hidden instructions|your instructions)\b", re.I),
    "fake_role_tag": re.compile(r"(^|\n)\s*(system|assistant)\s*:|<\s*/?\s*(system|instructions?)\s*>", re.I),
    # an exfiltration instruction names a destination; "send the username and password" in auth docs does not
    "exfiltration": re.compile(r"\b(send|post|upload|exfiltrate|forward|email)\b.{0,60}\b(api[_ ]?keys?|secrets?|credentials|passwords?|tokens?)\b.{0,40}\b(to|at)\s+(https?://|www\.|\S+@\S+\.\w)", re.I),
}


class ValidationError(ValueError):
    pass


def validate_question(question: str, max_chars: int = 1000) -> str:
    if not isinstance(question, str):
        raise ValidationError("question must be a string")
    cleaned = "".join(ch for ch in question if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 3:
        raise ValidationError("question is too short")
    if len(cleaned) > max_chars:
        raise ValidationError(f"question is longer than {max_chars} characters")
    return cleaned


def detect_injection(text: str) -> list[str]:
    """Names of the injection patterns found in the text (empty list = clean)."""
    return [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(text)]


def wrap_document(doc_id: int, source: str, section: str, text: str) -> str:
    safe = re.sub(r"<\s*/?\s*document[^>]*>", "[tag removed]", text, flags=re.I)
    return f'<document id="{doc_id}" source="{source}" section="{section}">\n{safe}\n</document>'


def mask_pii(text: str) -> str:
    if not text:
        return text
    text = EMAIL_RE.sub("[EMAIL]", text)
    return PHONE_RE.sub(lambda m: "[PHONE]" if sum(ch.isdigit() for ch in m.group()) >= 8 else m.group(), text)


def mask_value(value):
    return mask_pii(value) if isinstance(value, str) else value


class PIIMaskingFilter(logging.Filter):
    """Masks PII in the fully formatted message, so arguments are covered too."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        record.msg = mask_pii(message)
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Root logging with PII masking on every handler."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root.setLevel(level)
    for handler in root.handlers:
        if not any(isinstance(f, PIIMaskingFilter) for f in handler.filters):
            handler.addFilter(PIIMaskingFilter())
