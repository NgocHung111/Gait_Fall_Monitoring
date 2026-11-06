import requests


class TelegramError(RuntimeError):
    pass


def send_emergency_message(
    *,
    bot_token: str,
    chat_id: str,
    latitude: float | None = None,
    longitude: float | None = None,
    steps: int | None = None,
) -> None:
    if not bot_token or not chat_id:
        raise TelegramError("Telegram bot token and chat ID must be configured.")

    message = "CẢNH BÁO: Phát hiện té ngã."
    if steps is not None:
        message += f"\nSố bước: {steps}"
    if latitude is not None and longitude is not None:
        message += f"\nVị trí: https://maps.google.com/?q={latitude},{longitude}"

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        response.raise_for_status()
        if response.json().get("ok") is not True:
            raise TelegramError("Telegram rejected the emergency message.")
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError("Telegram message delivery failed.") from exc
