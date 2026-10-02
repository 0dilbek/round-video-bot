from round_video_handler import router
from aiogram  import Bot, Dispatcher
from asyncio import run
from config import BOT_TOKEN
from logging import basicConfig, INFO
from aiogram.filters import CommandStart
from aiogram.types import Message

basicConfig(
    level=INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
dp = Dispatcher()

dp.include_router(router)

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Salom! Men sizning dumaloq videolaringizni standart videoga aylantirib beraman."
    )

async def main():
    bot = Bot(
        token=BOT_TOKEN,
    )

    await dp.start_polling(bot)

run(
    main()
)