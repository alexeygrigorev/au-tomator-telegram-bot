#!/usr/bin/env python
# coding: utf-8

import os
import re
import signal
import textwrap

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


# Slack allows up to 3000 characters of text per section block
SECTION_TEXT_LIMIT = 3000

# Anything longer than this is continued in the thread of the first message
MESSAGE_TEXT_LIMIT = 3000

CODE_BLOCK_RE = re.compile(r'(`{3,})\n.*?\n\1', re.S)


def utf16_len(text):
    # Telegram entity offsets are in UTF-16 code units, so emoji count as 2
    return len(text.encode('utf-16-le')) // 2


def utf16_slice(text, offset, length):
    raw = text.encode('utf-16-le')
    return raw[offset * 2:(offset + length) * 2].decode('utf-16-le')


def escape_mrkdwn(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def code_fragment(text, always_fenced=False):
    ticks = '```' if always_fenced else '`'
    while ticks in text:
        ticks += '`'

    if always_fenced or '\n' in text:
        return f'{ticks}\n{text}\n{ticks}'

    pad = ' ' if text.startswith('`') or text.endswith('`') else ''
    return f'{ticks}{pad}{text}{pad}{ticks}'


def render_range(text, entities, start, end):
    """Render a slice of the message as Slack mrkdwn, escaping everything
    that Telegram did not mark up and translating its entities."""
    parts = []
    pos = start

    for entity in entities:
        if entity.offset < pos or entity.offset + entity.length > end:
            continue

        parts.append(escape_mrkdwn(utf16_slice(text, pos, entity.offset - pos)))

        fragment = utf16_slice(text, entity.offset, entity.length)
        escaped = escape_mrkdwn(fragment)
        stop = entity.offset + entity.length

        if entity.type == 'text_link':
            inner = [
                other for other in entities
                if other is not entity
                and other.offset >= entity.offset
                and other.offset + other.length <= stop
            ]
            label = render_range(text, inner, entity.offset, stop)
            parts.append(f'<{entity.url}|{label}>')
        elif entity.type == 'bold':
            parts.append(f'*{escaped}*')
        elif entity.type == 'italic':
            parts.append(f'_{escaped}_')
        elif entity.type in ('strikethrough', 'underline'):
            parts.append(f'~{escaped}~')
        elif entity.type == 'code':
            parts.append(code_fragment(escaped))
        elif entity.type == 'pre':
            parts.append(code_fragment(escaped, always_fenced=True))
        elif entity.type == 'url':
            parts.append(f'<{escaped}>')
        else:
            parts.append(escaped)

        pos = stop

    parts.append(escape_mrkdwn(utf16_slice(text, pos, end - pos)))

    return ''.join(parts)


def to_mrkdwn(text, entities):
    if not text:
        return ''

    entities = sorted(entities or [], key=lambda entity: entity.offset)

    return render_range(text, entities, 0, utf16_len(text))


def split_code_blocks(mrkdwn):
    parts = []
    pos = 0
    after_code = False

    for match in CODE_BLOCK_RE.finditer(mrkdwn):
        if match.start() > pos:
            prose = mrkdwn[pos:match.start()]
            parts.append((False, prose.lstrip('\n') if after_code else prose))

        parts.append((True, match.group()))
        pos = match.end()
        after_code = True

    if pos < len(mrkdwn):
        prose = mrkdwn[pos:]
        parts.append((False, prose.lstrip('\n') if after_code else prose))

    return [part for part in parts if part[1]]


def render_sections(text, entities=None, limit=SECTION_TEXT_LIMIT):
    """Render the message into Slack sections: prose as a quote block, code
    blocks kept as real code blocks."""
    sections = []
    current = []

    def flush():
        if current:
            sections.append('\n'.join(current))
            current.clear()

    def add_line(line):
        quoted = f'>{line}'.rstrip()
        if current and len('\n'.join(current)) + 1 + len(quoted) > limit:
            flush()
        current.append(quoted)

    for is_code, chunk in split_code_blocks(to_mrkdwn(text, entities)):
        if is_code:
            flush()
            if len(chunk) > limit:
                chunk = chunk[:limit - len('\n```')] + '\n```'
            sections.append(chunk)
            continue

        for line in chunk.splitlines():
            indent = line[:len(line) - len(line.lstrip())]
            width = limit - len('> ') - len(indent)
            pieces = textwrap.wrap(
                line.strip(),
                width=width,
                subsequent_indent=indent,
                break_on_hyphens=False,
            ) or ['']
            pieces[0] = indent + pieces[0]

            for piece in pieces:
                add_line(piece)

    flush()

    return sections


def group_sections(sections, limit=MESSAGE_TEXT_LIMIT):
    """Group sections into Slack messages. Only the first one goes to the
    channel, the rest continue in its thread."""
    groups = []
    current = []
    size = 0

    for section in sections:
        if current and size + 1 + len(section) > limit:
            groups.append(current)
            current = []
            size = 0

        current.append(section)
        size += 1 + len(section)

    if current:
        groups.append(current)

    return groups


def build_messages(text, entities=None, limit=MESSAGE_TEXT_LIMIT):
    """The message content, split into as many Slack messages as it takes."""
    messages = [
        [
            {"type": "section", "text": {"type": "mrkdwn", "text": section}}
            for section in group
        ]
        for group in group_sections(render_sections(text, entities), limit)
    ]

    return messages or [[]]


def link_blocks(link):
    return [{"type": "section", "text": {"type": "mrkdwn", "text": link}}]


def send_blocks(blocks, channel, thread_ts=None):
    message = {
      "channel": channel,
      "unfurl_links": False,
      "blocks": blocks,
    }

    if thread_ts is not None:
        message["thread_ts"] = thread_ts

    return requests.post(url, json=message, headers=headers).json()


def post(text, link, channel, entities=None):
    """Post the message to the channel, continue in the thread when it does not
    fit in one post, and always close the thread with the link to the original."""
    messages = build_messages(text, entities)
    first = send_blocks(messages[0], channel)
    responses = [first]

    if not first.get('ok'):
        return responses

    thread_ts = first['ts']

    for blocks in messages[1:]:
        responses.append(send_blocks(blocks, channel, thread_ts))

    responses.append(send_blocks(link_blocks(link), channel, thread_ts))

    return responses




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
    -1002136268305: {
        'channel': 'course-llm-zoomcamp',
        'link': 'https://t.me/llm_zoomcamp'
    },
    -1002261309914: {
        'channel': 'course-open-source-llm',
        'link': 'https://t.me/oss_llm_zoomcamp'
    },
    -1003188609266: {
        'channel': 'course-ai-dev-tools-zoomcamp',
        'link': 'https://t.me/aidevtoolszoomcamp'
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

    text = message.text or message.caption
    entities = message.entities or message.caption_entities

    if not text:
        text = 'no text in this post, see Telegram for the attachment'

    response = post(text, link, send_info['channel'], entities)
    print(response)



updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

dispatcher = updater.dispatcher

capture_text_handler = MessageHandler(Filters.text & (~Filters.command), capture)
dispatcher.add_handler(capture_text_handler)


print('starting listening...')
updater.start_polling()
updater.idle([signal.SIGINT])




