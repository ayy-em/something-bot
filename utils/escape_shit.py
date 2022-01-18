def escape_shit(text):
    chars = "_*[]()~`>#+-=|.!"
    # does not escape curly braces so lets hope we don't encounter them
    weird_stuff = '\\'
    for c in chars:
        text = text.replace(c, weird_stuff[0] + c)
    return text
