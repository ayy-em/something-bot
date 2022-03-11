from . import commands as cmds
from . import conversation as cnv
from . import escape_shit as esc
from . import messages as msgs
from . import update_handler as uh


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
    test_message_text = 'I got sent this in a group: ' + update.text_message_text
    print(test_message_text)
    # msgs.send_test_message(txt=esc.escape_shit(test_message_text))


def respond_to_direct_message(update):
    message_text_received = update.text_message_text
    if message_text_received[0] == '/':
        reply_msg = cmds.process_command(message_text_received)
    else:
        reply_msg = cnv.process_text(message_text_received)
    msgs.send_message(text=esc.escape_shit(reply_msg), chat_id=update.message_chat_from)
    try:
        test_message_text = 'I saw a {} message\nIt came from here: {}\nAnd it looks like this:\n\n{}'.format(
            update.message_destination, update.message_chat, reply_msg)
    except:
        try:
            test_message_text = 'Exception caught!\nI saw a {} message\nIt came from here: {}\nAnd it looks like this:\n\n{}'.format(
                update.message_destination, update.message_chat_from, reply_msg)
        except:
            test_message_text = 'Double exception!\nBut I just saw this message:\n\n' + reply_msg
    msgs.send_test_message(txt=esc.escape_shit(test_message_text))
