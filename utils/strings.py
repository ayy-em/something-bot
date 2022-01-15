import random
import string


# lots of text here, functions at the bottom
hi_msg = [
    "hi",
    "hello",
    "yo",
    "wassup",
    "what's up",
    "how you doin'?",
    "bonjour",
    "greetings",
    "wassup yoo",
    "good morning",
    "hey",
    "heya",
    "sup",
    "wazzup",
    "yoo",
    "yooo"
]

# reply to the start message, given by get_start_msg()
reply_start = "Hello there! I can help you do a lot of things.\n\n/weather - shows up-to-date weather in Amsterdam.\n/fact - get a random fun fact!\n/batavia - get Cafe Batavia menu.\nYou can also just talk to me, but i'm pretty stupid.\n\nMake sure to check out the channels i run: @vice_news (English), @adam24live (Russian), @ayy_maps (Russian)\n\nAnd definitely visit somethingreally.fun (permanent work in progress)! \nCheers."

# given by get_reply_string() if it's something a bot does not recognize
reply_unknown = [
    "I have no idea what you mean.",
    "What?",
    "I don't get it",
    "I must be stupid because I don't understand you",
    "I'm sorry, what?",
    "wut",
    "You're confusing me.. What am I supposed to do?"
]

# a list of fun facts
reply_fun_fact = [
    "Most elephants weigh less than a blue whale's tongue!",
    "Pineapples used to be so expensive that people would rent them as a centrepiece for their party.",
    "Scotland's national animal is a unicorn.",
    "A single strand of spaghetti is called a spaghetto.",
    "At birth, a baby panda is smaller than a mouse.",
    "Violin bows are usually made from horse hair.",
    "The colour red doesn't make bulls angry; they are colourblind.",
    "It snows metal on planet Venus.",
    "Bees tell their friends about good nearby flowers by dancing.",
    "Kangaroos can't walk backwards.",
    "In Switzerland, it's illegal to own just one guinea pig; if you have any, you have to have at least two. They get lonely!",
    "Otters have skin pockets for their favorite rocks.",
    "When a bee is chosen to be the new queen, they are given a special type of honey that completely changes their bodies. Kind of like how a Pokemon evolves.",
    "Butterflies smell with their feet.",
    "There are more stars than there are grains of sand on all the beaches in the world!",
    "Cows can walk up stairs, but they can't walk down.",
    "The surface of Mars is covered in rust, making the planet appear red.",
    "Cows have best friends and get stressed when separated.",
    "It takes a little over 8 minutes for the light from the Sun to get to earth.",
    "Hippopotamus milk is pink.",
    "Don't eat too many carrots or your skin will turn orange.",
    "Humans are bioluminescent and glow in the dark, but the light that we emit is 1,000 times weaker than our human eyes are able to pick up.",
    "Owls cannot be choked.",
    "The filling in a Kit Kat is broken up Kit Kat's.",
    "Giraffe tongues are black.",
    "Dogs can tell when you're coming home by how much of your scent is left in the house if you have a daily routine.",
    "Making pennies cost more than their actual value.",
    "Lobsters were considered disgusting and low-class food, to the point that feeding them to prisoners too often was considered cruel and unusual punishment.",
    "There are more ways to arrange a deck of cards than there are stars in our galaxy!",
    "If you keep a goldfish in a dark room it will turn white.",
    "There were wooly mammoths on the planet when the Pyramids were being built.",
    "J.K. Rowling is richer than the Queen.",
    "There is only one string in a tennis racquet.",
    "A blue whale's heart is as big as a Volkswagen Beetle.",
    "Oxford University is older than the Aztec empire."
]


# Process incoming message (from reply.py) and return the reply string
def get_reply_string(reply_to_check):
    is_it_hi = check_contains_hi(reply_to_check)
    if is_it_hi:
        reply_string = get_hi()
    else:
        reply_string = random.choice(reply_unknown)
    return reply_string


# when commanded, returns a string with a random fact
def get_reply_fact():
    reply_msg_text = random.choice(reply_fun_fact)
    return reply_msg_text


# gets this bot's start message
def get_start_msg():
    reply_msg_start = str(reply_start)
    return reply_msg_start


# check if the message contains a greeting, return T/F
def check_contains_hi(msg):
    msg_check_str = msg.strip(string.punctuation)
    msg_check_str_low = msg_check_str.lower()
    if msg_check_str_low in hi_msg:
        return True
    else:
        return False


# not really needed as an additional function...
def get_hi():
    reply_msg_text_sm = random.choice(hi_msg)
    reply_msg_text = reply_msg_text_sm.capitalize()
    return reply_msg_text
