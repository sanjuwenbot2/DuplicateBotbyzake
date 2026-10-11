

start = """<b>Gretting(s)</b>:

Welcome 🤗"""

BASIC_HELP_DICT = {
    "main": start,
}


def get_bot_commands():
    static_commands = {
        "Start": "•Start the bot",
        "Stats": "•Get bot statistics",
        "Help": "•Detailed help usage",
        "Ping": "•Ping Bot to test Response Speed",
        "Users": "•Get bot users count(s)",
        "Authorize": "•Authorize a user",
        "UnAuthorize": "•UnAuthorize a user",
        "AddSudo": "•Add a sudo user",
        "RmSudo": "•Remove a sudo user",
        "BlackList": "•Blacklist a user",
        "RmBlackList": "•Remove a user from the blacklist",
        "Broadcast": "•Broadcast a message to users",
        "Log": "•Get recent bot logs",
        "BotSet": "•Configure bot settings",
        "Restart": "•Restart the bot",
        "Scan": "•Scan one or more chats and save duplicate message IDs without deleting them",
        "DeleteDuplicates": "•Delete saved duplicates; without an ID, select chats from a menu",
        "ListScans": "•List your saved DeDup scan records",
        "CancelScan": "•Cancel the active DeDup scan",
        "ClearDedup": "•Clear saved DeDup data for a chat, all your chats, or selected chats",
    }
    return static_commands.copy()


BOT_COMMANDS = get_bot_commands()


def get_help_string():
    from ..telegram_helper.bot_commands import BotCommands

    help_lines = ["NOTE: Try each command without any argument to see more details."]

    commands = BotCommands.get_commands()

    for key, cmds in commands.items():
        cmd_attr = getattr(BotCommands, f"{key}Command", None)
        if not cmd_attr:
            continue

        if isinstance(cmd_attr, list):
            cmd_str = f"/{' or /'.join(cmd_attr)}"
        else:
            cmd_str = f"/{cmd_attr}"

        if key in BOT_COMMANDS:
            help_lines.append(f"{cmd_str}: {BOT_COMMANDS[key]}")

    return "\n".join(help_lines)


help_string = get_help_string()
