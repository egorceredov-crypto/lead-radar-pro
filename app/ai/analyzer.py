from typing import Dict

DEFAULT_KEYWORDS = ["ищу", "нужн", "нужен", "посоветуйте", "где", "купить", "цена", "сколько", "заказать", "хочу"]


async def analyze_message(text: str) -> Dict:
    if not text:
        return {"score": 0.0, "type": "NOT_LEAD", "matched": [], "description": "Empty text"}

    text_l = text.lower()
    matched = [kw for kw in DEFAULT_KEYWORDS if kw in text_l]

    if any(k in text_l for k in ["ищу", "нужн", "нужен", "посоветуйте", "хочу"]):
        score = 0.95
        lead_type = "HOT"
    elif any(k in text_l for k in ["сколько", "цена", "сколько стоит", "сколько будет"]):
        score = 0.7
        lead_type = "WARM"
    elif matched:
        score = 0.5
        lead_type = "COLD"
    else:
        score = 0.0
        lead_type = "NOT_LEAD"

    return {"score": score, "type": lead_type, "matched": matched, "description": f"Matched: {matched}"}
