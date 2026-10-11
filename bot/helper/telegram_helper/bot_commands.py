from ...core.config_manager import Config


class BotCommands:
    StartCommand = "start"

    _static_commands = {
        "Ping": ["ping"],
        "Help": ["help", "h"],
        "Stats": ["stats", "st"],
        "Log": ["log"],
        "Users": ["users"],
        "Authorize": ["authorize", "a"],
        "UnAuthorize": ["unauthorize", "ua"],
        "AddSudo": ["addsudo", "as"],
        "RmSudo": ["rmsudo", "rs"],
        "BlackList": ["blacklist", "bl"],
        "RmBlackList": ["rmblacklist", "rbl"],
        "Broadcast": ["broadcast"],
        "BotSet": ["bset", "bs"],
        "Restart": ["restart", "r", "restartall"],
        "RestartSessions": ["restartses", "rses"],
        "Scan": ["scan", "sc"],
        "DeleteDuplicates": ["delete", "del"],
        "ListScans": ["list", "ls"],
        "CancelScan": ["cancel", "can"],
        "ClearDedup": ["clear", "cl"],
    }

    @classmethod
    def get_commands(cls):
        return cls._static_commands.copy()

    @classmethod
    def _build_command_vars(cls):
        commands = cls.get_commands()
        for key, cmds in commands.items():
            setattr(
                cls,
                f"{key}Command",
                [f"{cmd}{Config.CMD_SUFFIX}" for cmd in cmds],
            )


BotCommands._build_command_vars()
