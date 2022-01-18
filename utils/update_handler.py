from . import messages as msgs

message_options = ['message', 'edited_message', 'channel_post', 'edited_channel_post']


# find out what kind of update it is and return an instance of that class
def process_update(update):
    is_message = False
    message_type = ''
    for msg_arg in message_options:
        if msg_arg in update:
            is_message = True
            message_type = msg_arg
    if 'my_chat_member' in update or 'chat_member' in update:
        update_class_instance = ChatMemberUpdate(update)
    elif 'chat_join_request' in update:
        update_class_instance = ChatJoinRequest(update)
    elif is_message:
        if 'poll' in update[message_type]:
            update_class_instance = PollUpdate(update, message_type)
        elif 'poll_answer' in update[message_type]:
            update_class_instance = PollAnswerUpdate(update, message_type)
        elif 'text' in update[message_type]:
            update_class_instance = TextMessageUpdate(update, message_type)
        else:
            # ToDo: make it say if its edited or not, channel or not
            update_class_instance = MessageUpdate(update, message_type)
    else:
        update_class_instance = None
        # types of message updates labeled "else": inline_query, chosen_inline_result,
        # callback_query, shipping_query, pre_checkout_query
        print('@@@ Bullshit update received: ' + str(update))
    return update_class_instance


# basic parent update class
class TelegramWebhookUpdate:
    def __init__(self, update):
        self.update_id = update['update_id']

    def __str__(self):
        return self.update_id


class MessageUpdate(TelegramWebhookUpdate):
    def __init__(self, update, message_type):
        TelegramWebhookUpdate.__init__(self, update)
        self.message_type = message_type
        message_content = update[message_type]
        self.message_id = message_content['message_id']
        self.message_date = message_content['date']
        self.message_chat = message_content['chat']
        if self.message_chat['type'] == 'private':
            self.message_destination = 'direct'
        elif self.message_chat['type'] == 'group' or self.message_chat['type'] == 'supergroup':
            self.message_destination = 'groupchat'
        else:
            self.message_destination = 'channel'
        self.message_chat_from = message_content['chat']['id']

    def __str__(self):
        return 'Weird message update: ' + str(self.update_id)


class TextMessageUpdate(MessageUpdate):
    def __init__(self, update, message_type):
        MessageUpdate.__init__(self, update, message_type)
        text_message_content = update[message_type]
        self.text_message_text = text_message_content['text']

    def __str__(self):
        return self.text_message_text


"""
class LocationMessage(MessageUpdate):
    def __init__(self, update):
        MessageUpdate.__init__(self, update)
"""


class PollUpdate(MessageUpdate):
    def __init__(self, update, message_type):
        MessageUpdate.__init__(self, update, message_type)
        poll_object = ''
        for opt in message_options:
            if opt in update:
                poll_object = [opt]['poll']
                break
            else:
                pass
        self.poll_id = poll_object['id']
        self.poll_question = poll_object['question']
        self.poll_is_closed = poll_object['is_closed']
        self.poll_is_anonymous = poll_object['is_anonymous']
        self.poll_allows_multiple_answers = poll_object['allows_multiple_answers']
        self.poll_voter_count = poll_object['total_voter_count']
        self.poll_type = poll_object['type']
        # each array is filled with PollOption objects with these args:
        # PollOption.text - string - option text, 1-100 chars
        # PollOption.voter_count - integer - number of users who voted for that option
        self.poll_options_array = poll_object['options']

    def __str__(self):
        return self.poll_question


class PollAnswerUpdate(MessageUpdate):
    def __init__(self, update, message_type):
        MessageUpdate.__init__(self, update, message_type)
        poll_answer_object = ''
        for opt in message_options:
            if opt in update:
                poll_answer_object = [opt]['poll_answer']
                break
            else:
                pass
        self.poll_id = poll_answer_object['id']
        # returns User object
        self.poll_user = poll_answer_object['user']
        # options_id is an array of integers
        if 'option_id' in poll_answer_object:
            self.poll_option_id = poll_answer_object['option_id']
        else:
            self.poll_option_id = 999

    def __str__(self):
        return self.poll_id


class ChatJoinRequest(TelegramWebhookUpdate):
    def __init__(self, update):
        TelegramWebhookUpdate.__init__(self, update)
        chat_join_update_object = update['chat_join_request']
        self.chat_join_request_chat = chat_join_update_object['chat']
        self.chat_join_request_user = chat_join_update_object['from']
        self.chat_join_request_date = chat_join_update_object['date']

    def __str__(self):
        return self.chat_join_request_chat


class ChatMemberUpdate(TelegramWebhookUpdate):
    def __init__(self, update):
        TelegramWebhookUpdate.__init__(self, update)
        if 'chat_member' in update:
            chat_member_update_object = update['chat_member']
        else:
            chat_member_update_object = update['my_chat_member']
        self.chat_member_update_chat = chat_member_update_object['chat']
        self.chat_member_update_from = chat_member_update_object['from']
        self.chat_member_update_date = chat_member_update_object['date']
        self.chat_member_update_old_member = chat_member_update_object['old_chat_member']
        self.chat_member_update_new_member = chat_member_update_object['new_chat_member']
        if 'invite_link' in chat_member_update_object:
            self.chat_member_update_invite_link = chat_member_update_object['invite_link']
        else:
            self.chat_member_update_invite_link = 'No invite link'

    def __str__(self):
        return self.chat_member_update_chat
