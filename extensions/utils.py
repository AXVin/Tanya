from __future__ import annotations
import asyncio

from extensions.players import Mafia
import discord
from discord.ext import commands
import re
import traceback
from fuzzywuzzy import process
import typing

if typing.TYPE_CHECKING:
    from extensions.game import MafiaGame
    from extensions.players import Player


class CustomContext(commands.Context):
    async def create_task(self, *args, **kwargs):
        """A shortcut to creating a task with a callback of logging the error"""
        task = self.bot.loop.create_task(*args, **kwargs)
        task.add_done_callback(self._log_future_error)

        return task

    def _log_future_error(self, future: asyncio.Future):
        if exc := future.exception():
            self.bot.loop.create_task(self.bot.log_error(exc, self.bot, self))


def get_mafia_player(game: MafiaGame, arg: str) -> Player:
    if not game:
        raise commands.BadArgument(
            "No game playing for this guild, cannot grab players"
        )

    result = None
    players = game.players
    match = re.match(r"([0-9]{15,20})$", arg) or re.match(r"<@!?([0-9]{15,20})>$", arg)
    if match is None:
        choices = {player: player.member.name for player in game.players}
        best = process.extractBests(arg, choices, score_cutoff=80, limit=1)

        if best:
            result = best[0][2]
    else:
        user_id = int(match.group(1))
        result = discord.utils.get(players, member__id=user_id)

    if not result:
        raise commands.MemberNotFound(arg)

    return result


def to_keycap(i: typing.Any[str, int]) -> str:
    return f"{i}\N{variation selector-16}\N{combining enclosing keycap}"


def min_max_check(ctx: commands.Context, min: int, max: int) -> typing.Callable:
    def check(m: discord.Message) -> bool:
        if m.channel != ctx.channel:
            return False
        if m.author != ctx.author:
            return False
        try:
            amt = int(m.content)
        except ValueError:
            return False
        else:
            return min <= amt <= max

    return check


def nomination_check(
    game: MafiaGame,
    nominations: dict,
    channel: discord.TextChannel,
    mafia: bool = False,
) -> typing.Callable:
    if mafia:
        noms_needed = 1 if game.total_mafia else 2
    else:
        noms_needed = 2

    def check(m: discord.Message) -> bool:
        # Ignore if not in channel we want
        if m.channel != channel:
            return False
        # Ignore if not player of game (admins, bots)
        if m.author not in [p.member for p in game.players]:
            return False
        # Ignore if not the right type of message
        if not m.content.startswith(">>nominate "):
            return False
        # Try to get the player
        try:
            content = m.content.split(">>nominate ")[1]
            player = game.ctx.bot.get_mafia_player(game, content)
            nominator = discord.utils.get(game.players, member=m.author)
        except commands.MemberNotFound:
            return False
        else:
            # Don't let them nominate themselves
            if nominator == player:
                return False
            # Check if dead
            if player.dead:
                return False
            # Don't let mafia get nominated during mafia nomination
            if mafia and player.is_mafia:
                return False
            # Increment their nomination
            nominations[player] = nominations.get(player, 0) + 1
            # If their nomination meets what's needed, set the player
            if nominations[player] == noms_needed:
                nominations["nomination"] = player
                return True
            # Otherwise mention need one more
            else:
                game.ctx.create_task(
                    m.channel.send(
                        f"{player.member.display_name} nominated, need one more"
                    )
                )

    return check


def private_channel_check(
    game: MafiaGame, player: Player, can_choose_self: bool = False
) -> typing.Callable:
    def check(m: discord.Message) -> bool:
        # Only care about messages from the author in their channel
        if m.channel != player.channel:
            return False
        elif m.author != player.member:
            return False
        # Set the player for use after
        try:
            p = game.ctx.bot.get_mafia_player(game, m.content)
        except commands.MemberNotFound:
            return False
        # Check the choosing self
        if not can_choose_self and player == p:
            game.ctx.create_task(p.channel.send("You cannot chooose yourself"))
        elif p is not None:
            return True

    return check


def mafia_kill_check(game: MafiaGame) -> typing.Callable:
    def check(m: discord.Message) -> bool:
        # Only care about messages from the author in their channel
        if m.channel != game.mafia_chat:
            return False
        elif m.author != game.godfather.member:
            return False
        # Set the player for use after
        try:
            p = game.ctx.bot.get_mafia_player(game, m.content)
        except commands.MemberNotFound:
            return False
        else:
            if p.is_mafia:
                return False
            else:
                return True

    return check


async def log_error(error: Exception, bot: commands.Bot, ctx: commands.Context = None):
    # Format the error message
    fmt = f"""```
{''.join(traceback.format_tb(error.__traceback__)).strip()}
{error.__class__.__name__}: {error}```"""
    # Add the command if ctx is given
    if ctx is not None:
        fmt = f"Command = {discord.utils.escape_markdown(ctx.message.clean_content).strip()}\n{fmt}"
    # If the channel has been set, use it
    if isinstance(bot.error_channel, discord.TextChannel):
        await bot.error_channel.send(fmt)
    # Otherwise if it hasn't been set yet, try to set it
    if isinstance(bot.error_channel, int):
        channel = bot.get_channel(bot.error_channel)
        if channel is not None:
            bot.error_channel = channel
            await bot.error_channel.send(fmt)
        # If we can't find the channel yet (before ready) just send to file
        else:
            fmt = fmt.strip("`")
            with open("error_log", "a") as f:
                print(fmt, file=f)
    # Otherwise just send to file
    else:
        fmt = fmt.strip("`")
        with open("error_log", "a") as f:
            print(fmt, file=f)


def setup(bot):
    bot.log_error = log_error
    bot.min_max_check = min_max_check
    bot.to_keycap = to_keycap
    bot.get_mafia_player = get_mafia_player
    bot.nomination_check = nomination_check
    bot.private_channel_check = private_channel_check
    bot.mafia_kill_check = mafia_kill_check
    bot.custom_context = CustomContext


def teardown(bot):
    del bot.log_error
    del bot.min_max_check
    del bot.to_keycap
    del bot.get_mafia_player
    del bot.nomination_check
    del bot.private_channel_check
    del bot.mafia_kill_check
    del bot.custom_context
