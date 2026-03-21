import asyncio

from . import pymax_patches

pymax_patches.apply()

from pymax import SocketMaxClient
from pymax.payloads import UserAgentPayload


async def register_account():
    print("=== Регистрация нового аккаунта Max ===")

    phone = input("Введите номер телефона (в формате +79001234567): ").strip()
    first_name = input("Введите имя: ").strip()
    last_name = input("Введите фамилию (нажмите Enter если нет): ").strip() or None

    if not phone or not first_name:
        print("Ошибка: номер телефона и имя обязательны!")
        return

    try:
        client = SocketMaxClient(
            work_dir=".cache/knopochka",
            phone=phone,
            registration=True,
            first_name=first_name,
            last_name=last_name,
            headers=UserAgentPayload(device_type="DESKTOP"),
        )

        print("\nОтправка запроса на регистрацию...")
        print("SMS с кодом подтверждения будет отправлен на указанный номер.")

        await client.start()

        print("\n✅ Регистрация успешно завершена!")
        print(f"📱 Токен: {client.token}")
        print("\n⚠️  Важно: используйте этот токен только с device_type='DESKTOP'")
        print("    Этот токен нельзя использовать в веб-клиентах")

        save_token = input("\nСохранить токен в файл? (y/n): ").strip().lower()
        if save_token == "y":
            filename = f"token_{phone.replace('+', '')}.txt"
            with open(filename, "w") as f:
                f.write(client.token)
            print(f"Токен сохранен в файл: {filename}")

    except Exception as e:
        print(f"❌ Ошибка при регистрации: {e}")
    finally:
        if "client" in locals():
            await client.close()


if __name__ == "__main__":
    asyncio.run(register_account())
