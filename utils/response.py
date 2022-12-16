from . import commands as cmds
from . import conversation as cnv
from . import escape_shit as esc
from . import messages as msgs
from . import update_handler as uh
from utils import ai_response
import os


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
    print(test_message_text)
    # msgs.send_test_message(txt=esc.escape_shit(test_message_text))


def respond_to_group(update):
    text_received = update.text_message_text
    if text_received[:19] == '@SomethingReallyBot':
        response_from_ai = ai_response.get_ai_response(text_received)
        response_string = response_from_ai[0]
        response_type = response_from_ai[1]
        if response_type == 'image_url':
            msgs.send_photo(caption=None, photo=response_string, chat_id=update.message_chat_from)
        else:
            msgs.send_message(text=esc.escape_shit(response_string), chat_id=update.message_chat_from, parse_mode='MarkdownV2', disable_notification=True)
    else:
        if not util_check_if_im_present_in_chat(update):
            msgs.send_test_message(txt=esc.escape_shit('Got this Groupchat msg: ' + update.text_message_text))


def respond_to_direct_message(update):
    message_text_received = update.text_message_text
    if message_text_received[0] == '/':
        reply_msg = cmds.process_command(message_text_received)
    else:
        reply_msg = cnv.process_text(message_text_received)
        if type(reply_msg) == str:
            msgs.send_message(text=esc.escape_shit(reply_msg), chat_id=update.message_chat_from)
        else:
            response_string = reply_msg[0]
            response_type = reply_msg[1]
            if response_type == 'image_url':
                msgs.send_photo(caption=None, photo=response_string, chat_id=update.message_chat_from)
            else:
                msgs.send_message(text=esc.escape_shit(response_string), chat_id=update.message_chat_from,
                                  parse_mode='MarkdownV2', disable_notification=True)
    try:
        test_message_text = 'I saw a {} message\nIt came from here: {}\nAnd it looks like this:\n\n{}'.format(update.message_destination, update.message_chat, reply_msg)
    except:
        try:
            test_message_text = 'Exception caught!\nI saw a {} message\nIt came from here: {}\nAnd it looks like this:\n\n{}'.format(
                update.message_destination, update.message_chat_from, reply_msg)
        except:
            test_message_text = 'Double exception!\nBut I just saw this message:\n\n' + reply_msg
    msgs.send_test_message(txt=esc.escape_shit(test_message_text))


def util_check_if_im_present_in_chat(update):
    if update.message_chat_from != os.getenv('FC_GROUP_CHAT_ID'):
        if update.message_chat['title'] != 'Vibing':
            if update.message_chat_from != os.getenv('PSDLK_TG_CHAT_ID'):
                if update.message_chat_from != os.getenv('FC_GROUP_CHAT_ID'):
                    return True
    return False