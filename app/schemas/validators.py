"""Small shared HTTP value normalizers; field constraints remain on schemas."""


def normalize_optional_title(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None
