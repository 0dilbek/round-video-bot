# round_video_handler.py

import asyncio
import json
import os
import shutil
import tempfile
import time

from aiogram import Bot, F, Router
from aiogram.types import FSInputFile, Message


router = Router()


async def edit_progress(message: Message, percent: int):
    try:
        await message.edit_text(f"{percent}% ish bitdi")
    except Exception:
        pass


async def get_duration(video_path: str) -> float:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(
            stderr.decode("utf-8", errors="ignore")
        )

    data = json.loads(stdout.decode())

    return float(data["format"]["duration"])


async def convert_round_video(
    input_path: str,
    output_path: str,
    duration: float,
    progress_message: Message,
):
    """
    Telegram video_note frame'ini olib tashlaydi.

    Siz yuborgan namuna:
        600 x 600

    Haqiqiy video radiusi taxminan:
        270px

    270 / 600 = 0.45

    Shu sabab radius:
        min(W,H) * 0.45
    """

    radius = "min(W,H)*0.45"

    # Doira ichidagi pixel o'z holicha qoladi.
    # Doira tashqarisidagi barcha pixel WHITE = 255,255,255.
    #
    # 2 px yumshoq chegara ham beramiz.
    video_filter = (
        "format=rgb24,"
        "geq="
        f"r='if(lt(hypot(X-W/2,Y-H/2),{radius}-2),r(X,Y),"
        f"if(lt(hypot(X-W/2,Y-H/2),{radius}),"
        f"r(X,Y)*(1-(hypot(X-W/2,Y-H/2)-({radius}-2))/2)"
        f"+255*((hypot(X-W/2,Y-H/2)-({radius}-2))/2),255))':"
        
        f"g='if(lt(hypot(X-W/2,Y-H/2),{radius}-2),g(X,Y),"
        f"if(lt(hypot(X-W/2,Y-H/2),{radius}),"
        f"g(X,Y)*(1-(hypot(X-W/2,Y-H/2)-({radius}-2))/2)"
        f"+255*((hypot(X-W/2,Y-H/2)-({radius}-2))/2),255))':"
        
        f"b='if(lt(hypot(X-W/2,Y-H/2),{radius}-2),b(X,Y),"
        f"if(lt(hypot(X-W/2,Y-H/2),{radius}),"
        f"b(X,Y)*(1-(hypot(X-W/2,Y-H/2)-({radius}-2))/2)"
        f"+255*((hypot(X-W/2,Y-H/2)-({radius}-2))/2),255))',"
        
        "format=yuv420p"
    )

    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",

        "-i",
        input_path,

        "-vf",
        video_filter,

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "18",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-movflags",
        "+faststart",

        "-progress",
        "pipe:1",

        "-nostats",

        output_path,

        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    last_percent = 20
    last_update = 0

    while True:
        line = await process.stdout.readline()

        if not line:
            break

        line = line.decode(
            "utf-8",
            errors="ignore"
        ).strip()

        if line.startswith("out_time_ms="):
            try:
                microseconds = int(
                    line.split("=", 1)[1]
                )

                current_seconds = (
                    microseconds / 1_000_000
                )

                encode_percent = (
                    current_seconds / max(duration, 0.1)
                ) * 100

                encode_percent = max(
                    0,
                    min(encode_percent, 100)
                )

                # Processing:
                # 20% -> 90%
                percent = int(
                    20 + encode_percent * 0.70
                )

                percent = (
                    percent // 5
                ) * 5

                now = time.monotonic()

                if (
                    percent > last_percent
                    and now - last_update >= 0.5
                ):
                    await edit_progress(
                        progress_message,
                        percent,
                    )

                    last_percent = percent
                    last_update = now

            except Exception:
                pass

    await process.wait()

    if process.returncode != 0:
        stderr = await process.stderr.read()

        raise RuntimeError(
            stderr.decode(
                "utf-8",
                errors="ignore"
            )
        )


@router.message(F.video_note)
async def round_video_handler(
    message: Message,
    bot: Bot,
):
    progress_message = await message.answer(
        "0% ish bitdi"
    )

    temp_dir = tempfile.mkdtemp(
        prefix="round_video_"
    )

    input_path = os.path.join(
        temp_dir,
        "input.mp4"
    )

    output_path = os.path.join(
        temp_dir,
        "output.mp4"
    )

    try:

        # ==============================
        # DOWNLOAD
        # ==============================

        await bot.download(
            message.video_note,
            destination=input_path,
        )

        await edit_progress(
            progress_message,
            10,
        )

        # ==============================
        # VIDEO INFO
        # ==============================

        duration = await get_duration(
            input_path
        )

        await edit_progress(
            progress_message,
            20,
        )

        # ==============================
        # REMOVE TELEGRAM FRAME
        # ==============================

        await convert_round_video(
            input_path=input_path,
            output_path=output_path,
            duration=duration,
            progress_message=progress_message,
        )

        await edit_progress(
            progress_message,
            90,
        )

        # ==============================
        # SEND
        # ==============================

        video = FSInputFile(
            output_path,
            filename="video.mp4",
        )

        await edit_progress(
            progress_message,
            95,
        )

        await message.answer_video(
            video=video,
            supports_streaming=True,
        )

        await edit_progress(
            progress_message,
            100,
        )

        await asyncio.sleep(1)

        try:
            await progress_message.delete()
        except Exception:
            pass

    except Exception as error:

        print(
            "ROUND VIDEO ERROR:",
            error
        )

        try:
            await progress_message.edit_text(
                "Xatolik yuz berdi"
            )
        except Exception:
            pass

    finally:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )