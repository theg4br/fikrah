"""Display-name policy shared by HTTP input and verified identity claims."""

DISPLAY_NAME_MAX_LENGTH = 120


def normalize_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Name must be a string")  # noqa: TRY004 -- validation errors, not programmer errors
    name = value.strip()
    if "\x00" in name:
        raise ValueError("Name cannot contain null characters")
    if not 1 <= len(name) <= DISPLAY_NAME_MAX_LENGTH:
        raise ValueError("Name must contain 1 to 120 characters")
    return name
