import json
from datetime import datetime
from functools import wraps
from sys import stderr
from traceback import format_exc

import telebot
from typing import Callable

from db_connector import PrettyCursor
from logic import add_user, start_conversation, end_conversation, get_conversing, get_admins_ids
from config import bot_token
from callback_helpers import contract_callback_data_and_jdump, jload_and_decontract_callback_data, \
    seconds_since_local_epoch, datetime_from_local_epoch_secs


bot = telebot.TeleBot(bot_token)


def notify_admins(**kwargs) -> bool:
    """
    Send a text message to all the bot administrators. Any exceptions occurring inside are suppressed

    ::param kwargs: Keyword arguments to be forwarded to `bot.send_message` (shouldn't contain `chat_id`)
    :return: `True` if a message was successfully delivered to at least one admin (i. e. no exception occurred), `False`
        otherwise
    """
    try:
        admins = get_admins_ids()
    except Exception:
        print("Couldn't get admins ids inside of `notify_admins`:", file=stderr)
        print(format_exc(), file=stderr)
        return False

    sent = False

    try:
        for i in admins:
            try:
                bot.send_message(chat_id=i, **kwargs)
            except Exception:
                print("Couldn't send a message to an admin inside of `notify_admins`:", file=stderr)
                print(format_exc(), file=stderr)
            else:
                sent = True

    except Exception:
        print("Something went wrong while **iterating** throw `admins` inside of `notify_admins`:", file=stderr)
        print(format_exc(), file=stderr)

    return sent


def nonfalling_handler(func: Callable):
    @wraps(func)
    def ans(message: telebot.types.Message, *args, **kwargs):
        try:
            func(message, *args, **kwargs)
        except Exception:
            try:
                # For callback query handlers (we got a `telebot.types.CallbackQuery` object instead of a message)
                if hasattr(message, 'message'):
                    message = message.message

                s = "Произошла ошибка"
                if notify_admins(text=('```' + format_exc() + '```'), parse_mode="Markdown"):
                    s += ". Наши администраторы получили уведомление о ней"
                else:
                    s += ". Свяжитесь с администрацией бота для исправления"
                s += ". Технические детали:\n```" + format_exc() + "```"

                print(format_exc(), file=stderr)
                bot.send_message(message.chat.id, s, parse_mode="Markdown")
            except:
                print(format_exc(), file=stderr)

    return ans


class AnyContentType:
    def __contains__(self, item): return True


@bot.message_handler(commands=['start', 'help'])
@nonfalling_handler
def start_help_handler(message: telebot.types.Message):
    bot.reply_to(message, "Привет. /start_conversation, чтобы начать беседу, /end_conversation чтобы завершить")
    add_user(message.chat.id)

@bot.message_handler(commands=['start_conversation'])
@nonfalling_handler
def start_conversation_handler(message: telebot.types.Message):
    err, conversing = start_conversation(message.chat.id)
    if err:
        if err == 1:
            bot.reply_to(message, "Вы уже в беседе с оператором. Используйте /end_conversation чтобы прекратить")
        elif err == 2:
            bot.reply_to(message, "Операторы не могут запрашивать помощь, пока помогают кому-то\nОбратитесь к @kolayne "
                                  "для реализации такой возможности")
        elif err == 3:
            bot.reply_to(message, "Сейчас нет доступных операторов :(\nПопробуйте позже")
        else:
            raise NotImplementedError("Unknown error code returned by `start_conversation`")

        return

    (_, client_local), (operator_tg, _) = conversing

    bot.reply_to(message, "Началась беседа с оператором. Отправьте сообщение, и оператор его увидит. "
                          "Используйте /end_conversation чтобы прекратить")
    bot.send_message(operator_tg, f"Пользователь №{client_local} начал беседу с вами")

@bot.message_handler(commands=['end_conversation'])
@nonfalling_handler
def end_conversation_handler(message: telebot.types.Message):
    (_, client_local), (operator_tg, operator_local) = get_conversing(message.chat.id)

    if operator_tg == -1:
        bot.reply_to(message, "В данный момент вы ни с кем не беседуете. Используйте /start_conversation чтобы начать")
    elif operator_tg == message.chat.id:
        bot.reply_to(message, "Оператор не может прекратить беседу. Обратитесь к @kolayne для реализации такой "
                              "возможности")
    else:
        keyboard = telebot.types.InlineKeyboardMarkup()
        d = {'type': 'conversation_rate', 'operator_ids': [operator_tg, operator_local],
             'conversation_end_moment': seconds_since_local_epoch(datetime.now())}

        keyboard.add(
            telebot.types.InlineKeyboardButton("Лучше",
                                               callback_data=contract_callback_data_and_jdump({**d, 'mood': 'better'})),
            telebot.types.InlineKeyboardButton("Так же",
                                               callback_data=contract_callback_data_and_jdump({**d, 'mood': 'same'})),
            telebot.types.InlineKeyboardButton("Хуже",
                                               callback_data=contract_callback_data_and_jdump({**d, 'mood': 'worse'}))
        )
        keyboard.add(telebot.types.InlineKeyboardButton("Не хочу оценивать",
                                                        callback_data=contract_callback_data_and_jdump(d)))

        end_conversation(message.chat.id)
        bot.reply_to(message, "Беседа с оператором прекратилась. Хотите оценить свое самочувствие после нее? "
                              "Вы остаетесь анонимным", reply_markup=keyboard)
        bot.send_message(operator_tg, f"Пользователь №{client_local} прекратил беседу")

@bot.message_handler(content_types=['text'])
@nonfalling_handler
def text_message_handler(message: telebot.types.Message):
    (client_tg, _), (operator_tg, _) = get_conversing(message.chat.id)

    if client_tg == -1:
        bot.reply_to(message, "Чтобы начать общаться с оператором, нужно написать /start_conversation. Сейчас у вас "
                              "нет собеседника")
        return

    interlocutor_id = client_tg if message.chat.id != client_tg else operator_tg

    reply_to = None
    if message.reply_to_message is not None:
        with PrettyCursor() as cursor:
            cursor.execute("SELECT sender_message_id FROM reflected_messages WHERE sender_chat_id=%s AND "
                           "receiver_chat_id=%s AND receiver_message_id=%s",
                           (interlocutor_id, message.chat.id, message.reply_to_message.message_id))
            try:
                reply_to, = cursor.fetchone()
            except TypeError:
                bot.reply_to(message, "Эта беседа уже завершилась. Вы не можете ответить на это сообщение")
                return

    for entity in message.entities or []:
        if entity.type == 'mention':
            continue
        if entity.type == 'url' and message.text[entity.offset: entity.offset + entity.length] == entity.url:
            continue

        bot.reply_to(message, "Это сообщение содержит форматирование, которое сейчас не поддерживается. Оно будет "
                              "отправлено с потерей форматирования. Мы работаем над этим")
        break

    sent = bot.send_message(interlocutor_id, message.text, reply_to_message_id=reply_to)

    with PrettyCursor() as cursor:
        query = "INSERT INTO reflected_messages(sender_chat_id, sender_message_id, receiver_chat_id, " \
                "receiver_message_id) VALUES (%s, %s, %s, %s)"
        cursor.execute(query, (message.chat.id, message.message_id, sent.chat.id, sent.message_id))
        cursor.execute(query, (sent.chat.id, sent.message_id, message.chat.id, message.message_id))


@bot.message_handler(content_types=AnyContentType())
@nonfalling_handler
def another_content_type_handler(message: telebot.types.Message):
    bot.reply_to(message, "Сообщения этого типа не поддерживаются. Свяжитесь с @kolayne, чтобы добавить поддержку")


@bot.callback_query_handler(func=lambda call: True)
@nonfalling_handler
def callback_query(call: telebot.types.CallbackQuery):
    try:
        d = jload_and_decontract_callback_data(call.data)
        if d.get('type') != 'conversation_rate':
            raise json.JSONDecodeError
    except json.JSONDecodeError:
        bot.answer_callback_query(call.id, "Это действие не поддерживается")
        return

    mood = d.get('mood')
    if mood == 'worse':
        operator_tg, operator_local = d['operator_ids']
        conversation_end = datetime_from_local_epoch_secs(d['conversation_end_moment'])
        notification_text = "Клиент чувствует себя хуже после беседы с оператором {}, которая завершилась в {}".format(
            f"[{operator_local}](tg://user?id={operator_tg})", conversation_end
        )
        notify_admins(text=notification_text, parse_mode="Markdown")

    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

    if mood is None:
        bot.answer_callback_query(call.id)
    else:
        bot.answer_callback_query(call.id, "Спасибо за вашу оценку")


if __name__ == "__main__":
    bot.polling(none_stop=False)
