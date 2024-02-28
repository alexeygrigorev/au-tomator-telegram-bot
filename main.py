#!/usr/bin/env python
# coding: utf-8

import os
import signal

import requests

from telegram import Update

from telegram.ext import Updater
from telegram.ext import CallbackContext
from telegram.ext import CommandHandler
from telegram.ext import MessageHandler
from telegram.ext import Filters


TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
SLACK_TOKEN = os.getenv('SLACK_TOKEN')


headers = {
    'Authorization': f'Bearer {SLACK_TOKEN}'
}

url = 'https://slack.com/api/chat.postMessage'


# mrkdwn_template = """
# > {text}

# {link}
# """.strip()

mrkdwn_template = '{link}'


def post(text, link, channel):
    mrkdwn = mrkdwn_template.format(text=text, link=link)

    message = {
      "channel": channel,
      "blocks": [
        {"type": "section", "text": {"type": "mrkdwn", "text": mrkdwn}},
      ]
    }

    response = requests.post(url, json=message, headers=headers).json()
    return response




channel_mapping = {
    -1001730331343: {
        'channel': 'course-mlops-zoomcamp',
        'link': 'https://t.me/dtc_courses'
    },
    -1001435532197: {
        'channel': 'course-ml-zoomcamp',
        'link': 'https://t.me/mlzoomcamp'
    },
    -1001708295427: {
        'channel': 'course-data-engineering',
        'link': 'https://t.me/dezoomcamp'
    },
    -1001707092787: {
        'channel': 'integration_test',
        'link': 'https://t.me/dtc_test'
    },
    -1002070983780: {
        'channel': 'course-stocks-analytics-zoomcamp',
        'link': 'https://t.me/stockanalyticszoomcamp'
    },
}



def capture(update: Update, context: CallbackContext):
    print('processing another message...')
    print(update)

    if update.channel_post is None:
        print('not a channel post')
        return

    message = update.channel_post
    chat_id = message.chat.id
    
    if chat_id not in channel_mapping:
        print(f'unknown chat_id = {chat_id}')
        return

    
    send_info = channel_mapping[chat_id]

    url_prefix = send_info['link']
    message_id = message.message_id

    link = f"{url_prefix}/{message_id}"

    response = post(message.text, link, send_info['channel'])
    print(response)



updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

dispatcher = updater.dispatcher

capture_text_handler = MessageHandler(Filters.text & (~Filters.command), capture)
dispatcher.add_handler(capture_text_handler)


print('starting listening...')
updater.start_polling()
updater.idle([signal.SIGINT])




