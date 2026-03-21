import asyncio
import datetime
from pathlib import Path

from . import pymax_patches

pymax_patches.apply()

from pymax import Message, SocketMaxClient
from pymax.payloads import UserAgentPayload


def load_token() -> str | None:
    token_file = Path("token.txt")
    if token_file.exists():
        return token_file.read_text().strip() or None
    return None


def get_phone() -> str:
    phone_file = Path("phone.txt")
    if phone_file.exists():
        phone = phone_file.read_text().strip()
        if phone:
            return phone
    phone = input("Введите номер телефона (например +79001234567): ").strip()
    phone_file.write_text(phone)
    return phone


async def main():
    token = load_token()
    phone = get_phone()

    client = SocketMaxClient(
        work_dir=".cache/knopochka",
        phone=phone,
        token=token,
        headers=UserAgentPayload(device_type="DESKTOP"),
    )

    @client.on_message()
    async def handle_message(message: Message):
        ts = datetime.datetime.fromtimestamp(message.time / 1000).strftime("%H:%M:%S")
        sender = message.sender or "?"
        text = message.text or ""
        attaches = ""
        if message.attaches:
            types = [type(a).__name__ for a in message.attaches]
            attaches = f" [{', '.join(types)}]"
        print(f"[{ts}] chat={message.chat_id} from={sender}: {text}{attaches}")

    print("Подключение...")
    await client.start()


if __name__ == "__main__":
    asyncio.run(main())
