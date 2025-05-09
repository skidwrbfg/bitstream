import logging
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Конфигурация
STEAM_API_KEY = "56716E5D4FE456305205C86778E0824E"  # Замените на ваш ключ
TELEGRAM_BOT_TOKEN = "7749494231:AAHNhoQR5Wn-2MhCZg0rGF4aQQm87_jd5NA"  # Замените на ваш токен
CHECK_INTERVAL = 60  # Интервал проверки в секундах

# Глобальные переменные
user_tracking = {}
tasks = {}  # Для хранения фоновых задач


async def get_steam_user_summary(steam_id: str) -> dict:
    """Асинхронное получение информации о пользователе Steam"""
    url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={STEAM_API_KEY}&steamids={steam_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json()

                if not data.get('response', {}).get('players'):
                    logging.error("Пустой ответ от Steam API")
                    return None

                return data['response']['players'][0]
    except Exception as e:
        logging.error(f"Ошибка при запросе к Steam API: {e}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    try:
        await update.message.reply_text(
            "🚀 Бот для отслеживания статуса Steam аккаунтов\n\n"
            "Доступные команды:\n"
            "/track [SteamID] - начать отслеживание\n"
            "/stop [SteamID] - остановить отслеживание\n"
            "/list - список отслеживаемых аккаунтов"
        )
    except Exception as e:
        logging.error(f"Ошибка в start: {e}")


async def start_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинаем отслеживание пользователя Steam"""
    try:
        chat_id = update.effective_chat.id
        if not context.args:
            await update.message.reply_text("ℹ️ Укажите SteamID после команды\nПример: /track 76561197960287930")
            return

        steam_id = context.args[0]

        # Проверка формата Steam ID
        if not steam_id.isdigit() or len(steam_id) != 17:
            await update.message.reply_text(
                "❌ Неверный формат SteamID\nДолжен состоять из 17 цифр\nПример: 76561197960287930")
            return

        user_info = await get_steam_user_summary(steam_id)

        if not user_info:
            await update.message.reply_text(
                "⚠️ Не удалось получить данные\nПроверьте:\n1. Верный ли SteamID\n2. Работает ли Steam API")
            return

        user_name = user_info.get('personaname', 'Неизвестный пользователь')

        if chat_id not in user_tracking:
            user_tracking[chat_id] = {}

        if steam_id in user_tracking[chat_id]:
            await update.message.reply_text(f"ℹ️ Уже отслеживаю пользователя {user_name} (SteamID: {steam_id})")
        else:
            user_tracking[chat_id][steam_id] = {
                'last_status': user_info.get('personastate', 0),
                'last_check': datetime.now(),
                'status_start_time': datetime.now(),
                'name': user_name
            }

            # Запускаем фоновую задачу
            task = asyncio.create_task(check_user_status(chat_id, steam_id, context.application))
            tasks[(chat_id, steam_id)] = task
            await update.message.reply_text(
                f"✅ Начал отслеживание:\n"
                f"Имя: {user_name}\n"
                f"SteamID: {steam_id}\n"
                f"Текущий статус: {get_status_name(user_info.get('personastate', 0))}"
            )

    except Exception as e:
        logging.error(f"Ошибка в start_tracking: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")


def get_status_name(status_code: int) -> str:
    """Возвращает читаемое название статуса"""
    status_map = {
        0: "🔴 Оффлайн",
        1: "🟢 Онлайн",
        2: "🟡 Занят",
        3: "🟠 Отошёл",
        4: "💤 Спит",
        5: "💰 Хочет торговать",
        6: "🎮 Хочет играть"
    }
    return status_map.get(status_code, "❓ Неизвестно")


def format_time_delta(delta) -> str:
    """Форматирует timedelta в читаемый формат"""
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    time_parts = []
    if days > 0:
        time_parts.append(f"{days} дн")
    if hours > 0:
        time_parts.append(f"{hours} ч")
    if minutes > 0:
        time_parts.append(f"{minutes} мин")
    if seconds > 0 or not time_parts:
        time_parts.append(f"{seconds} сек")

    return " ".join(time_parts)


async def check_user_status(chat_id: int, steam_id: str, app: Application) -> None:
    """Фоновая задача для проверки статуса"""
    while True:
        try:
            if chat_id not in user_tracking or steam_id not in user_tracking[chat_id]:
                break

            user_data = user_tracking[chat_id][steam_id]
            user_info = await get_steam_user_summary(steam_id)

            if user_info:
                current_status = user_info.get('personastate', 0)
                last_status = user_data['last_status']

                if current_status != last_status:
                    time_in_status = datetime.now() - user_data['status_start_time']
                    time_str = format_time_delta(time_in_status)

                    message = (
                        f"🔄 Изменение статуса {user_data['name']}:\n"
                        f"Был: {get_status_name(last_status)}\n"
                        f"В статусе: {time_str}\n"
                        f"Стал: {get_status_name(current_status)}"
                    )

                    await app.bot.send_message(chat_id=chat_id, text=message)

                    # Обновляем данные
                    user_data['last_status'] = current_status
                    user_data['status_start_time'] = datetime.now()

                user_data['last_check'] = datetime.now()

            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logging.error(f"Ошибка в check_user_status: {e}")
            await asyncio.sleep(10)


async def stop_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Останавливаем отслеживание"""
    try:
        chat_id = update.effective_chat.id
        if not context.args:
            await update.message.reply_text("ℹ️ Укажите SteamID после команды\nПример: /stop 76561197960287930")
            return

        steam_id = context.args[0]

        if chat_id in user_tracking and steam_id in user_tracking[chat_id]:
            user_name = user_tracking[chat_id][steam_id]['name']

            # Отменяем фоновую задачу
            task = tasks.get((chat_id, steam_id))
            if task:
                task.cancel()
                del tasks[(chat_id, steam_id)]

            del user_tracking[chat_id][steam_id]
            if not user_tracking[chat_id]:
                del user_tracking[chat_id]

            await update.message.reply_text(f"⏹ Прекратил отслеживание:\nИмя: {user_name}\nSteamID: {steam_id}")
        else:
            await update.message.reply_text("ℹ️ Не отслеживаю указанного пользователя")

    except Exception as e:
        logging.error(f"Ошибка в stop_tracking: {e}")
        await update.message.reply_text("⚠️ Не удалось завершить отслеживание")


async def list_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показываем список отслеживаемых пользователей"""
    try:
        chat_id = update.effective_chat.id
        if chat_id in user_tracking and user_tracking[chat_id]:
            message = "📋 Отслеживаю:\n\n"
            for steam_id, data in user_tracking[chat_id].items():
                time_in_status = format_time_delta(datetime.now() - data['status_start_time'])
                message += (
                    f"👤 {data['name']}\n"
                    f"🆔 {steam_id}\n"
                    f"📊 {get_status_name(data['last_status'])}\n"
                    f"⏱ В статусе: {time_in_status}\n"
                    f"──────────────────\n"
                )
            await update.message.reply_text(message)
        else:
            await update.message.reply_text("ℹ️ Нет отслеживаемых аккаунтов")

    except Exception as e:
        logging.error(f"Ошибка в list_tracking: {e}")
        await update.message.reply_text("⚠️ Не удалось загрузить список")


def main() -> None:
    """Запуск бота"""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("track", start_tracking))
    application.add_handler(CommandHandler("stop", stop_tracking))
    application.add_handler(CommandHandler("list", list_tracking))

    # Запускаем бота
    application.run_polling()


if __name__ == '__main__':
    main()