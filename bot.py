import os
import logging
import cv2
import numpy as np
import asyncio
import gspread
import json
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    ContentType, FSInputFile
)

# --- Load sensitive values from environment variables ---
TOKEN = os.getenv("BOT_TOKEN")
CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_STANDARD_ID = os.getenv("SHEET_STANDARD_ID")
SHEET_KIA_ID = os.getenv("SHEET_KIA_ID")
SHEET_LEXUS_ID = os.getenv("SHEET_LEXUS_ID")
OWNER_ID = None

bot = Bot(token=TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

keyboard_main = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🚀 Старт")]],
    resize_keyboard=True,
    one_time_keyboard=True
)

keyboard_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎨 Подобрать цвет")],
        [KeyboardButton(text="📜 История запросов")]
    ],
    resize_keyboard=True
)

keyboard_model = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="KIA")],
        [KeyboardButton(text="Lexus")],
        [KeyboardButton(text="Стандартная модель")]
    ],
    resize_keyboard=True
)

keyboard_cancel = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Отмена")]],
    resize_keyboard=True
)

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials_dict = json.loads(CREDENTIALS_JSON.replace("\\n", "\n"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, SCOPE)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_STANDARD_ID).sheet1
sheet_kia = client.open_by_key(SHEET_KIA_ID).sheet1
sheet_lexus = client.open_by_key(SHEET_LEXUS_ID).sheet1

user_state = {}


def set_owner_if_needed(user: types.User):
    global OWNER_ID
    if OWNER_ID is None:
        try:
            with open("owner_id.txt", "w") as f:
                OWNER_ID = user.id
                f.write(str(OWNER_ID))
        except Exception as e:
            print("Ошибка сохранения OWNER_ID:", e)
    else:
        try:
            with open("owner_id.txt", "r") as f:
                OWNER_ID = int(f.read().strip())
        except:
            pass

def log_user_request(user: types.User, data: dict):
    set_owner_if_needed(user)
    username = user.username or f"user_{user.id}"
    try:
        worksheet = client.open("UserLogs").worksheet(username)
    except:
        worksheet = client.open("UserLogs").add_worksheet(title=username, rows="1000", cols="20")
        worksheet.append_row(["Date", "UserID", "Username", "Full Name", "Phone", "RGB", "Result"])

    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user.id,
        username,
        user.full_name,
        data.get("phone", "-"),
        str(data.get("rgb")),
        data.get("result")
    ]
    worksheet.append_row(row)

def get_dominant_color(image_path):
    image = cv2.imread(image_path)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = cv2.resize(image, (100, 100))
    pixels = np.float32(image.reshape(-1, 3))
    _, labels, palette = cv2.kmeans(
        pixels, 3, None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0), 10,
        cv2.KMEANS_RANDOM_CENTERS
    )
    _, counts = np.unique(labels, return_counts=True)
    dominant_color = palette[np.argmax(counts)]
    return tuple(map(int, dominant_color))

def find_closest_color(rgb):
    def cie76(c1, c2):
        return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5
    closest_color_name = None
    min_distance = float("inf")
    rows = sheet.get_all_records()
    for row in rows:
        try:
            color_name = row["name"]
            color_rgb = list(map(int, row["rgb"].split(",")))
            dist = cie76(rgb, color_rgb)
            if dist < min_distance:
                min_distance = dist
                closest_color_name = color_name
        except ValueError:
            continue
    return closest_color_name

def find_model_link(rgb, year, sheet_model):
    def cie76(c1, c2):
        return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5
    rows = sheet_model.get_all_records()
    closest_link = None
    min_distance = float("inf")
    for row in rows:
        try:
            years_range = row["years"]
            color_rgb = list(map(int, row["rgb"].split(",")))
            start_year, end_year = map(int, years_range.split(" - "))
            if start_year <= year <= end_year:
                dist = cie76(rgb, color_rgb)
                if dist < min_distance:
                    min_distance = dist
                    closest_link = row["links"]
        except (ValueError, KeyError):
            continue
    return closest_link

def generate_color_image(rgb, output_path="color_image.jpg"):
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[:, :] = rgb
    cv2.imwrite(output_path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    return output_path

@dp.message(Command("history"))
async def get_history(message: types.Message):
    user = message.from_user
    username = user.username or f"user_{user.id}"
    try:
        worksheet = client.open("UserLogs").worksheet(username)
        rows = worksheet.get_all_values()
        if len(rows) > 1:
            await message.answer("\n".join([" | ".join(row) for row in rows[1:]]))
        else:
            await message.answer("История пуста.")
    except:
        await message.answer("История не найдена.")

@dp.message(lambda message: message.text == "🚀 Старт")
async def welcome(message: types.Message):
    set_owner_if_needed(message.from_user)
    await message.answer("Добро пожаловать в бот!", reply_markup=keyboard_menu)

@dp.message(lambda message: message.text == "🎨 Подобрать цвет")
async def start_color_process(message: types.Message):
    user_state[message.from_user.id] = {"state": "start"}
    await message.answer("Выберите модель:", reply_markup=keyboard_model)

@dp.message(lambda message: message.text == "📜 История запросов")
async def history_request(message: types.Message):
    await get_history(message)

@dp.message(lambda message: message.text in ["KIA", "Lexus", "Стандартная модель"])
async def handle_model_choice(message: types.Message):
    user_id = message.from_user.id
    choice = message.text
    if choice in ["KIA", "Lexus"]:
        user_state[user_id] = {"state": f"{choice.lower()}_waiting_year"}
        await message.answer("Введите год выпуска модели.", reply_markup=keyboard_cancel)
    else:
        user_state[user_id] = {"state": "standard_waiting_photo"}
        await message.answer("Отправьте фото для RGB-анализа.", reply_markup=keyboard_cancel)

@dp.message(lambda message: message.text and message.text.isdigit())
async def handle_year_input(message: types.Message):
    user_id = message.from_user.id
    state = user_state.get(user_id, {}).get("state")
    if state and state.endswith("_waiting_year"):
        model = state.split("_")[0]
        user_state[user_id] = {"state": f"{model}_waiting_photo", "year": int(message.text)}
        await message.answer("Отправьте фото для RGB-анализа.", reply_markup=keyboard_cancel)

@dp.message(lambda message: message.content_type == ContentType.PHOTO)
async def handle_photo(message: types.Message):
    user_id = message.from_user.id
    user = message.from_user
    state = user_state.get(user_id, {}).get("state")
    if not state or not state.endswith("_waiting_photo"):
        await message.answer("Нажмите '🎨 Подобрать цвет'.")
        return

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_name = os.path.join(DOWNLOAD_DIR, f"{file.file_id}.jpg")
    await bot.download(file, file_name)
    dominant_color = get_dominant_color(file_name)

    model = state.split("_")[0]
    result_text = ""

    if model in ["kia", "lexus"]:
        year = user_state[user_id].get("year")
        model_sheet = sheet_kia if model == "kia" else sheet_lexus
        link = find_model_link(dominant_color, year, model_sheet)
        result_text = link if link else "Подходящие ссылки не найдены."
        await message.answer(f"Найдена ссылка: {result_text}", reply_markup=keyboard_menu)
    else:
        color_name = find_closest_color(dominant_color)
        result_text = color_name
        await message.answer(f"Определённый цвет: RGB{dominant_color}\nБлижайший цвет из таблицы: {color_name}", reply_markup=keyboard_menu)

    photo_file = FSInputFile(generate_color_image(dominant_color))
    await message.answer_photo(photo=photo_file, caption=f"Определённый RGB: {dominant_color}")
    log_user_request(user, {"rgb": dominant_color, "result": result_text})
    user_state.pop(user_id, None)

@dp.message()
async def default_start(message: types.Message):
    set_owner_if_needed(message.from_user)
    await message.answer("Добро пожаловать! Нажмите '🚀 Старт' чтобы начать.", reply_markup=keyboard_main)

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())