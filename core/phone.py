def normalize_il_phone(value: str) -> str:
    """
    Israeli phone as digits only in local form ("05XXXXXXXX"). WhatsApp customer lookup
    matches Profile.phone by this exact string, so "+972-50-123-4567" must become
    "0501234567". Returns "" for an empty value.
    """
    digits = "".join(filter(str.isdigit, value or ""))
    if digits.startswith("972"):
        digits = "0" + digits[3:]
    return digits
