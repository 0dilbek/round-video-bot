from round_video_handler import router
from aiogram  import Bot, Dispatcher
from asyncio import run
from config import BOT_TOKEN

dp = Dispatcher()

dp.include_router(router)

async def main():
    bot = Bot(
        token=BOT_TOKEN,
    )

    await dp.start_polling(bot)

run(
    main()
)