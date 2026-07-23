import re

class InputValidator:
    @staticmethod
    def sanitize_text(text: str) -> str:
        if not text:
            return ""
        # Strip dangerous HTML/Script tags
        clean = re.sub(r"<script.*?>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"<.*?>", "", clean)
        return clean.strip()

    @staticmethod
    def is_valid_phone(phone: str) -> bool:
        if not phone:
            return False
        pattern = r"^\+?[1-9]\d{1,14}$"
        return bool(re.match(pattern, phone.replace(" ", "").replace("-", "")))
