from . import strings as strings
from . import update_handler as uh
from . import messages as msgs
from . import conversation as cnv
from . import commands as cmds
from . import escape_shit as esc


def respond_to(update):
    if isinstance(update, uh.TextMessageUpdate):
        if update.message_destination == 'direct':
            respond_to_direct_message(update)
        elif update.message_destination == 'groupchat':
            respond_to_group(update)
        elif update.message_destination == 'channel':
            respond_to_channel(update)


def respond_to_channel(update):
    test_message_text = 'I got sent this in a channel: ' + update.text_message_text
    msgs.send_test_message(txt=esc.escape_shit(test_message_text))


def respond_to_group(update):
    test_message_text = 'I got sent this in a group: ' + update.text_message_text
    msgs.send_test_message(txt=esc.escape_shit(test_message_text))


def respond_to_direct_message(update):
    message_text_received = update.text_message_text
    if message_text_received[0] == '/':
        reply_msg = cmds.process_command(message_text_received)
    else:
        reply_msg = cnv.process_text(message_text_received)
    msgs.send_message(text=esc.escape_shit(reply_msg), chat_id=update.message_chat_from)
