import asyncio
import json
import math
import os
import secrets
import shutil
import tempfile
import time
from typing import Dict, TypedDict

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)


router = Router()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKGROUND_PREVIEW_PATH = os.path.join(BASE_DIR, "video_backgrounds_preview.png")
SESSION_TTL = 24 * 60 * 60


class VideoSession(TypedDict):
    file_id: str
    created_at: float


# Productionda Redis ishlatsangiz bundan ham yaxshi bo'ladi.
# Bu variant bitta bot process uchun yetarli.
VIDEO_SESSIONS: Dict[str, VideoSession] = {}


def cleanup_sessions() -> None:
    now = time.time()
    expired = [
        token
        for token, data in VIDEO_SESSIONS.items()
        if now - data["created_at"] > SESSION_TTL
    ]
    for token in expired:
        VIDEO_SESSIONS.pop(token, None)


def choose_background_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Fon 1", callback_data=f"rv:bg:{token}:1"),
                InlineKeyboardButton(text="Fon 2", callback_data=f"rv:bg:{token}:2"),
                InlineKeyboardButton(text="Fon 3", callback_data=f"rv:bg:{token}:3"),
            ],
            [
                InlineKeyboardButton(text="Fon 4", callback_data=f"rv:bg:{token}:4"),
                InlineKeyboardButton(text="Fon 5", callback_data=f"rv:bg:{token}:5"),
                InlineKeyboardButton(text="Fon 6", callback_data=f"rv:bg:{token}:6"),
            ],
        ]
    )


def main_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎨 Fon tanlash",
                    callback_data=f"rv:choose:{token}",
                )
            ]
        ]
    )


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
        raise RuntimeError(stderr.decode("utf-8", errors="ignore"))

    data = json.loads(stdout.decode())
    return float(data["format"]["duration"])


def _rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _mix_expr(a: int, b: int, factor: str) -> str:
    return f"({a}+({b}-{a})*({factor}))"


def background_rgb_expressions(background_id: int) -> tuple[str, str, str]:
    """
    6 ta fon previewdagi ko'rinishga yaqin qilib FFmpeg GEQ bilan chiziladi.
    X/Y/W/H o'zgaruvchilari geq ichida ishlaydi.
    """

    # Normalized coordinates
    nx = "X/W"
    ny = "Y/H"
    radial = "min(1,hypot(X-W/2,Y-H/2)/(0.72*min(W,H)))"
    vertical = ny
    diagonal = "min(1,(X/W+Y/H)/2)"

    if background_id == 1:
        # Oq / juda yengil kulrang radial
        center = _rgb("#FFFFFF")
        edge = _rgb("#EEEEEE")
        factor = f"pow({radial},1.4)"
        return tuple(_mix_expr(center[i], edge[i], factor) for i in range(3))

    if background_id == 2:
        # Iliq cream gradient
        top = _rgb("#FFF8E9")
        bottom = _rgb("#EEE4D0")
        return tuple(_mix_expr(top[i], bottom[i], vertical) for i in range(3))

    if background_id == 3:
        # Light blue
        top = _rgb("#DCEEFF")
        bottom = _rgb("#9FC7F0")
        return tuple(_mix_expr(top[i], bottom[i], vertical) for i in range(3))

    if background_id == 4:
        # Dark charcoal radial
        center = _rgb("#34383D")
        edge = _rgb("#1E2227")
        factor = f"pow({radial},1.25)"
        return tuple(_mix_expr(center[i], edge[i], factor) for i in range(3))

    if background_id == 5:
        # Pastel blue -> lavender/pink
        left = _rgb("#DDEBFF")
        right = _rgb("#E8C8F1")
        return tuple(_mix_expr(left[i], right[i], nx) for i in range(3))

    # Fon 6: oq-kulrang bazada bir nechta yumshoq pastel spot
    # exp() orqali gaussian bulutlar hosil qilinadi.
    base = _rgb("#EEF0F3")
    peach = _rgb("#F4D9D2")
    mint = _rgb("#DCEDE2")
    lilac = _rgb("#E5DFF1")
    blue = _rgb("#DBE6F4")

    g1 = "exp(-((X/W-0.72)^2+(Y/H-0.26)^2)/0.035)"
    g2 = "exp(-((X/W-0.36)^2+(Y/H-0.74)^2)/0.045)"
    g3 = "exp(-((X/W-0.78)^2+(Y/H-0.78)^2)/0.050)"
    g4 = "exp(-((X/W-0.23)^2+(Y/H-0.31)^2)/0.055)"

    expressions = []
    for i in range(3):
        expr = (
            f"clip({base[i]}"
            f"+({peach[i]}-{base[i]})*0.72*{g1}"
            f"+({mint[i]}-{base[i]})*0.68*{g2}"
            f"+({lilac[i]}-{base[i]})*0.65*{g3}"
            f"+({blue[i]}-{base[i]})*0.55*{g4},0,255)"
        )
        expressions.append(expr)

    return tuple(expressions)


async def convert_round_video(
    input_path: str,
    output_path: str,
    duration: float,
    background_id: int = 1,
):
    """
    Telegram video_note ichidagi doira qismi o'z holicha qoladi.
    Doira tashqarisi tanlangan fon bilan to'ldiriladi.
    """

    radius = "min(W,H)*0.45"
    edge_softness = 2

    bg_r, bg_g, bg_b = background_rgb_expressions(background_id)
    dist = "hypot(X-W/2,Y-H/2)"
    blend = f"({dist}-({radius}-{edge_softness}))/{edge_softness}"

    video_filter = (
        "format=rgb24,"
        "geq="
        f"r='if(lt({dist},{radius}-{edge_softness}),r(X,Y),"
        f"if(lt({dist},{radius}),"
        f"r(X,Y)*(1-({blend}))+({bg_r})*({blend}),({bg_r})))':"
        f"g='if(lt({dist},{radius}-{edge_softness}),g(X,Y),"
        f"if(lt({dist},{radius}),"
        f"g(X,Y)*(1-({blend}))+({bg_g})*({blend}),({bg_g})))':"
        f"b='if(lt({dist},{radius}-{edge_softness}),b(X,Y),"
        f"if(lt({dist},{radius}),"
        f"b(X,Y)*(1-({blend}))+({bg_b})*({blend}),({bg_b})))',"
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

    # stdoutni to'liq o'qib boramiz, aks holda progress pipe to'lib qolishi mumkin.
    while True:
        line = await process.stdout.readline()
        if not line:
            break

    await process.wait()

    if process.returncode != 0:
        stderr = await process.stderr.read()
        raise RuntimeError(stderr.decode("utf-8", errors="ignore"))


@router.message(F.video_note)
async def round_video_handler(message: Message, bot: Bot):
    """
    1) User video_note yuboradi.
    2) Bot standard oq fonli videoni yuboradi.
    3) Shu BOT xabarining tagida "Fon tanlash" tugmasi bo'ladi.
    4) Keyingi barcha amallar aynan shu bitta bot xabarini edit qilish orqali ketadi.
    """

    cleanup_sessions()

    temp_dir = tempfile.mkdtemp(prefix="round_video_")
    input_path = os.path.join(temp_dir, "input.mp4")
    output_path = os.path.join(temp_dir, "output.mp4")
    msg = await message.answer("Video tayyorlanmoqda... ⏳")
    try:
        await bot.download(message.video_note, destination=input_path)
        duration = await get_duration(input_path)

        # Standard hozirgi video = Fon 1
        await convert_round_video(
            input_path=input_path,
            output_path=output_path,
            duration=duration,
            background_id=1,
        )

        token = secrets.token_urlsafe(8)
        VIDEO_SESSIONS[token] = {
            "file_id": message.video_note.file_id,
            "created_at": time.time(),
        }

        await msg.edit_media(
            media=InputMediaVideo(
                media=FSInputFile(output_path, filename="video.mp4"),
                supports_streaming=True,
                reply_markup=main_keyboard(token),
            )
        )

    except Exception as error:
        print("ROUND VIDEO ERROR:", error)
        await message.answer("Xatolik yuz berdi")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@router.callback_query(F.data.startswith("rv:choose:"))
async def open_background_picker(callback: CallbackQuery):
    await callback.answer()

    if not callback.message:
        return

    token = callback.data.split(":", 2)[2]
    session = VIDEO_SESSIONS.get(token)

    if not session:
        await callback.answer(
            "Video sessiyasi tugagan. Videoni qayta yuboring.",
            show_alert=True,
        )
        return

    if not os.path.exists(BACKGROUND_PREVIEW_PATH):
        await callback.answer(
            "video_backgrounds_preview.png topilmadi.",
            show_alert=True,
        )
        return

    # MUHIM: yangi xabar yubormaymiz.
    # Video turgan xabarning o'zini rasmga aylantiramiz.
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=FSInputFile(BACKGROUND_PREVIEW_PATH),
            caption="Fonlardan birini tanlang 👇",
        ),
        reply_markup=choose_background_keyboard(token),
    )


@router.callback_query(F.data.startswith("rv:bg:"))
async def apply_background(callback: CallbackQuery, bot: Bot):
    if not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Noto'g'ri so'rov", show_alert=True)
        return

    _, _, token, bg_raw = parts
    session = VIDEO_SESSIONS.get(token)

    if not session:
        await callback.answer(
            "Video sessiyasi tugagan. Videoni qayta yuboring.",
            show_alert=True,
        )
        return

    try:
        background_id = int(bg_raw)
    except ValueError:
        await callback.answer("Noto'g'ri fon", show_alert=True)
        return

    if background_id not in {1, 2, 3, 4, 5, 6}:
        await callback.answer("Noto'g'ri fon", show_alert=True)
        return

    # Callback spinnerini darhol yopamiz.
    await callback.answer(f"Fon {background_id} tayyorlanmoqda...")

    # Xuddi shu preview xabarining captionini o'zgartiramiz.
    try:
        await callback.message.edit_caption(
            caption=f"Fon {background_id} tayyorlanmoqda... ⏳",
            reply_markup=None,
        )
    except Exception:
        pass

    temp_dir = tempfile.mkdtemp(prefix="round_video_bg_")
    input_path = os.path.join(temp_dir, "input.mp4")
    output_path = os.path.join(temp_dir, "output.mp4")

    try:
        # Original video_note Telegram file_id orqali qayta olinadi.
        # Diskda doimiy video saqlash shart emas.
        await bot.download(session["file_id"], destination=input_path)
        duration = await get_duration(input_path)

        await convert_round_video(
            input_path=input_path,
            output_path=output_path,
            duration=duration,
            background_id=background_id,
        )

        # MUHIM: yangi video yubormaymiz.
        # Preview rasm turgan XUDDI SHU xabarni videoga edit qilamiz.
        await callback.message.edit_media(
            media=InputMediaVideo(
                media=FSInputFile(output_path, filename="video.mp4"),
                supports_streaming=True,
                caption=f"Fon {background_id}",
            ),
            reply_markup=main_keyboard(token),
        )

        # Sessiyani yana 24 soatga uzaytiramiz.
        session["created_at"] = time.time()

    except Exception as error:
        print("BACKGROUND APPLY ERROR:", error)

        try:
            await callback.message.edit_caption(
                caption="Xatolik yuz berdi. Qayta urinib ko'ring.",
                reply_markup=choose_background_keyboard(token),
            )
        except Exception:
            pass

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
