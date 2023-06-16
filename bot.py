import logging
import requests
import random
import json
import os
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Updater, CommandHandler, CallbackContext, CallbackQueryHandler, MessageHandler, Filters
import sqlite3
from contextlib import closing

TELEGRAM_TOKEN = "*****"
UNSPLASH_ACCESS_KEY = "******"
DEEPL_API_KEY = "******"
LINGUA_ROBOT_API_KEY = "******"
DATABASE = 'words.db'

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

conn = sqlite3.connect(DATABASE, check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS words
                  (chat_id text, word text, definition text, image_url text, translated_word text, transcription text)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS skipped_words
                  (chat_id text, word text)''')


def get_random_word():
    word = None
    definition = None
    transcription = None
    audio_url = None
    while word is None or definition is None or transcription is None or audio_url is None:
        try:
            url = "https://api.datamuse.com/words?ml=love"
            response = requests.get(url).json()
            random_word = random.choice(response)
            word = random_word['word']

            cursor.execute("SELECT * FROM words WHERE word=?", (word,))
            if cursor.fetchone() is not None:
                word = None
                definition = None
                transcription = None
                continue

            cursor.execute("SELECT * FROM skipped_words WHERE word=?", (word,))
            if cursor.fetchone() is not None:
                word = None
                definition = None
                transcription = None
                continue

            url = f"https://lingua-robot.p.rapidapi.com/language/v1/entries/en/{word}"
            headers = {
                "X-RapidAPI-Key": LINGUA_ROBOT_API_KEY,
                "X-RapidAPI-Host": "lingua-robot.p.rapidapi.com"
            }
            response = requests.get(url, headers=headers).json()
            definition = response['entries'][0]['lexemes'][0]['senses'][0]['definition']
            transcription = response['entries'][0]['pronunciations'][0]['transcriptions'][0]['transcription']
            audio_url = response['entries'][0]['pronunciations'][1]['audio']['url']
            transcription = transcription.replace('/', '')

        except (KeyError, IndexError):
            word = None
            definition = None
            transcription = None
            audio_url = None
    return word, definition, transcription, audio_url


def translate_word(word):
    url = "https://api-free.deepl.com/v2/translate"
    headers = {
        "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}",
    }
    data = {
        "text": word,
        "target_lang": "UK",
    }
    response = requests.post(url, headers=headers, data=data)
    response_json = json.loads(response.text)
    translated_text = response_json["translations"][0]["text"]
    return translated_text


def get_image(word):
    url = f"https://api.unsplash.com/search/photos?query={word}&client_id={UNSPLASH_ACCESS_KEY}"
    response = requests.get(url).json()
    image_url = response['results'][0]['urls']['small']
    return image_url


def start(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [KeyboardButton("📘 Learn"),
         KeyboardButton("🔄 Repeat"),
         KeyboardButton("🗑️ Delete the words")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    context.bot.send_message(chat_id=update.effective_chat.id, text="Choose an option:", reply_markup=reply_markup)


def handle_messages(update: Update, context: CallbackContext) -> None:
    text = update.message.text
    if text == "📘 Learn":
        learn(update, context)
    elif text == "🔄 Repeat":
        review(update, context)
    elif text == "🗑️ Delete the words":
        clear(update, context)


def learn(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    loading_message = context.bot.send_message(chat_id=chat_id, text="Wait for the words...")
    word, definition, transcription, audio_url = get_random_word()
    image_url = get_image(word)
    translated_word = translate_word(word)
    context.chat_data['word_data'] = (chat_id, word, definition, image_url, translated_word, transcription)

    valid_filename = "".join(c for c in word if c.isalpha() or c.isspace()).rstrip()

    # Fetch audio file from audio_url and save it locally
    response = requests.get(audio_url)
    with open(f'{valid_filename}.mp3', 'wb') as audio_file:
        audio_file.write(response.content)

    keyboard = [
        [InlineKeyboardButton("Remember", callback_data=f'remembered_{word}'),
         InlineKeyboardButton("Skip", callback_data=f'skip_{word}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    sent_message = context.bot.send_photo(chat_id=chat_id, photo=image_url,
                                          caption=f"{word} [{transcription}] - {translated_word}\n\n{definition}",
                                          reply_markup=reply_markup)

    with closing(open(f'{valid_filename}.mp3', 'rb')) as audio_file:
        audio_message = context.bot.send_audio(chat_id=chat_id, audio=audio_file)

    context.bot.delete_message(chat_id=chat_id, message_id=loading_message.message_id)

    # Save the IDs of the sent message and audio file to delete later
    context.chat_data['last_message'] = sent_message.message_id
    context.chat_data['last_audio'] = audio_message.message_id

    os.remove(f"{valid_filename}.mp3")


def review(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id

    # Delete the previous message and audio file before sending a new one
    if 'last_message' in context.chat_data:
        try:
            context.bot.delete_message(chat_id=chat_id, message_id=context.chat_data['last_message'])
            del context.chat_data['last_message']
        except Exception as e:
            print(f'Error deleting last message: {e}')
    if 'last_audio' in context.chat_data:
        try:
            context.bot.delete_message(chat_id=chat_id, message_id=context.chat_data['last_audio'])
            del context.chat_data['last_audio']
        except Exception as e:
            print(f'Error deleting last audio: {e}')

    if 'review_index' not in context.chat_data or context.chat_data['review_index'] == 0:
        context.chat_data['review_index'] = 0
        word = cursor.execute("SELECT * FROM words WHERE chat_id=? LIMIT 1 OFFSET ?",
                              (chat_id, context.chat_data['review_index'])).fetchone()
    else:
        word = cursor.execute("SELECT * FROM words WHERE chat_id=? LIMIT 1 OFFSET ?",
                              (chat_id, context.chat_data['review_index'])).fetchone()

    if word:
        keyboard = [
            [InlineKeyboardButton("Next", callback_data=f'next_{word[1]}'),
             InlineKeyboardButton("End repetition", callback_data='end_review')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        url = f"https://lingua-robot.p.rapidapi.com/language/v1/entries/en/{word[1]}"
        headers = {
            "X-RapidAPI-Key": LINGUA_ROBOT_API_KEY,
            "X-RapidAPI-Host": "lingua-robot.p.rapidapi.com"
        }
        response = requests.get(url, headers=headers).json()
        audio_url = response['entries'][0]['pronunciations'][1]['audio']['url']

        valid_filename = "".join(c for c in word[1] if c.isalpha() or c.isspace()).rstrip()

        # Fetch audio file from audio_url and save it locally
        response = requests.get(audio_url)
        audio_filename = f'{valid_filename}.mp3'
        with open(audio_filename, 'wb') as audio_file:
            audio_file.write(response.content)

        # Send image if exists
        if word[3]:  # Assuming word[3] is the column where you store the image URL
            sent_message = context.bot.send_photo(chat_id=chat_id, photo=word[3],
                                                  caption=f'{word[1]} [{word[5]}] - {word[4]}\n{word[2]}',
                                                  reply_markup=reply_markup)
        else:
            sent_message = context.bot.send_message(chat_id=chat_id,
                                                    text=f'{word[1]} [{word[5]}] - {word[4]}\n{word[2]}',
                                                    reply_markup=reply_markup)

        with open(audio_filename, 'rb') as audio_file:
            audio_message = context.bot.send_audio(chat_id=chat_id, audio=audio_file)

        context.chat_data['review_index'] += 1

        # Save the IDs of the sent message and audio file to delete later
        context.chat_data['last_message'] = sent_message.message_id
        context.chat_data['last_audio'] = audio_message.message_id

        os.remove(audio_filename)  # delete the local audio file after sending it
    else:
        context.bot.send_message(chat_id=chat_id, text="All learned words are repeated.")
        context.chat_data['review_index'] = 0  # Reset the review index when all words have been reviewed


def next_word(update: Update, context: CallbackContext) -> None:
    review(update, context)


def clear(update: Update, context: CallbackContext) -> None:
    query = update
    chat_id = query.message.chat_id
    cursor.execute("DELETE FROM words WHERE chat_id=?", (chat_id,))
    cursor.execute("DELETE FROM skipped_words WHERE chat_id=?", (chat_id,))
    conn.commit()
    context.bot.send_message(chat_id=chat_id, text="All learned words are deleted.")
    start(query, context)


def button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    # Delete the previous message and audio file before doing anything else
    if 'last_message' in context.chat_data:
        try:
            context.bot.delete_message(chat_id=query.message.chat_id, message_id=context.chat_data['last_message'])
            del context.chat_data['last_message']
        except Exception as e:
            print(f'Error deleting last message: {e}')
    if 'last_audio' in context.chat_data:
        try:
            context.bot.delete_message(chat_id=query.message.chat_id, message_id=context.chat_data['last_audio'])
            del context.chat_data['last_audio']
        except Exception as e:
            print(f'Error deleting last audio: {e}')

    if query.data == 'learn':
        learn(query, context)
    elif query.data == 'review':
        review(query, context)
    elif query.data == 'clear':
        clear(query, context)
    elif query.data.startswith('remembered_'):
        cursor.execute("INSERT INTO words VALUES (?, ?, ?, ?, ?, ?)", context.chat_data['word_data'])
        conn.commit()
        learn(query, context)
    elif query.data.startswith('skip_'):
        skipped_word = query.data.split('_')[1]
        cursor.execute("INSERT INTO skipped_words VALUES (?, ?)", (query.message.chat_id, skipped_word))
        conn.commit()
        learn(query, context)
    elif query.data.startswith('next_'):
        review(query, context)
    elif query.data == 'end_review':
        context.bot.send_message(chat_id=query.message.chat_id, text="Repetition is over.")
        context.chat_data['review_index'] = 0
        start(update, context)


def main() -> None:
    updater = Updater(token=TELEGRAM_TOKEN)
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_messages))
    dispatcher.add_handler(CallbackQueryHandler(button))
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
