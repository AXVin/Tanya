from __future__ import annotations

import asyncio
import collections
import dataclasses
from extensions.players import Player
import discord
from discord.ext import commands
from discord.mentions import AllowedMentions
import random
import typing

if typing.TYPE_CHECKING:
    from extensions import players


default_role_overwrites = discord.PermissionOverwrite(
    read_messages=False,
    send_messages=True,
    read_message_history=True,
    attach_files=False,
    add_reactions=False,
)
default_role_disabled_overwrites = discord.PermissionOverwrite(
    read_messages=False,
    send_messages=False,
    read_message_history=True,
    attach_files=False,
    add_reactions=False,
)
jail_overwrites = discord.PermissionOverwrite(
    read_messages=False,
    send_messages=True,
    read_message_history=False,
    attach_files=False,
    add_reactions=False,
)
bot_overwrites = discord.PermissionOverwrite(
    read_messages=True,
    send_messages=True,
    add_reactions=True,
)
user_overwrites = discord.PermissionOverwrite(read_messages=True)


@dataclasses.dataclass
class MafiaGameConfig:
    starting_mafia: int
    special_roles: typing.List[players.Player]
    ctx: commands.Context
    night_length: int = 45
    day_length: int = 90


class MafiaGame:
    def __init__(self, ctx: commands.Context, *, config: str):
        # The discord members, we'll produce our list of players later
        self._members: typing.List[discord.Member] = None
        # The actual players of the game
        self.players: typing.List[players.Player] = []

        self.ctx: commands.Context = ctx
        self.is_day: bool = True

        # Different chats needed
        self.chat: discord.TextChannel = None
        self.info: discord.TextChannel = None
        self.jail: discord.TextChannel = None
        self.jail_webhook: discord.Webhook = None
        self.mafia_chat: discord.TextChannel = None
        self.dead_chat: discord.TextChannel = None

        self._alive_game_role_name: str = "Alive Players"
        self._alive_game_role: discord.Role = None

        self._rand = random.SystemRandom()
        self._config: MafiaGameConfig = None
        # The preconfigured option that can be provided
        self._preconfigured_config: str = config
        self._day: int = 1
        self._day_notifications = collections.defaultdict(list)
        self._role_list: list = None

    @property
    def total_mafia(self) -> int:
        return sum(1 for player in self.players if player.is_mafia and not player.dead)

    @property
    def total_citizens(self) -> int:
        return sum(
            1 for player in self.players if player.is_citizen and not player.dead
        )

    @property
    def total_alive(self) -> int:
        return sum(1 for player in self.players if not player.dead)

    @property
    def total_players(self) -> int:
        return len(self.players)

    @property
    def godfather(self) -> players.Mafia:
        for player in self.players:
            if player.is_godfather and not player.dead:
                return player

    async def night_notification(self):
        embed = discord.Embed(
            title=f"Night {self._day - 1}",
            description="Check your private channels",
            colour=0x0A0A86,
        )
        embed.set_thumbnail(
            url="https://www.jing.fm/clipimg/full/132-1327252_half-moon-png-images-moon-clipart-png.png"
        )
        await self.info.send(content=self._alive_game_role.mention, embed=embed)

    def add_day_notification(self, *notifications: str):
        msg, current_notifications = self._day_notifications.get(self._day, (None, []))
        current_notifications.extend(notifications)

        self._day_notifications[self._day] = (msg, current_notifications)

    async def day_notification(self, *notifications: str):
        """Creates a notification embed with all of todays notifications"""
        msg, current_notifications = self._day_notifications.get(self._day, (None, []))
        current_notifications.extend(notifications)
        fmt = "Roles Alive:\n"
        # Get alive players to add to alive roles
        alive_players = {}
        for player in self.players:
            if player.dead:
                continue
            alive_players[str(player)] = alive_players.get(str(player), 0) + 1
        fmt += "\n".join(f"{key}: {count}" for key, count in alive_players.items())
        fmt += "\n\n"
        # If we're not on day one, notify that you can nominate
        if self._day > 1:
            fmt += f"**Type >>nominate member to nominate someone to be lynched**. Chat in {self.chat.mention}\n\n"
        else:
            fmt += f"Chat in {self.chat.mention}\n\n"
        # Add the recent actions
        fmt += "**Recent Actions**\n"
        fmt += "\n".join(current_notifications)

        embed = discord.Embed(
            title=f"Day {self._day}", description=fmt, colour=0xF6F823
        )
        embed.set_thumbnail(
            url="https://media.discordapp.net/attachments/840698427755069475/841841923936485416/Sw5vSWOjshUo40xEj-hWqfiRu8Ma2CtYjjh7prRsF6ADPk_z7znpEBf-E3i44U9Hukh3ZJOFhm9S43naa4dEA8pXX4dfAJeEv0bl.png"
        )
        if msg is None:
            msg = await self.info.send(
                content=self._alive_game_role.mention, embed=embed
            )
        else:
            await msg.edit(embed=embed)

        self._day_notifications[self._day] = (msg, current_notifications)

    async def update_role_list(self):
        msg = "\n".join(
            f"**{'Town' if role.is_citizen else 'Mafia'}** - {role}"
            for role in self.players
        )
        if self._role_list is None:
            self._role_list = await self.info.send(msg)
        else:
            await self._role_list.edit(content=msg)

    def check_winner(self) -> bool:
        """Loops through all the winners and checks their win conditions"""
        for player in self.players:
            if not player.win_is_multi and player.win_condition(self):
                return True

        return False

    def get_winners(self) -> typing.List[Player]:
        """Returns all winners of this game"""
        return [p for p in self.players if p.win_condition(self)]

    async def choose_godfather(self):
        godfather = self._rand.choice(
            [
                p
                for p in self.players
                # We don't want to choose special mafia
                if p.is_mafia and p.__class__ not in self.ctx.bot.__special_mafia__
            ]
        )
        godfather.is_godfather = True

        await godfather.channel.send("You are the godfather!")

    async def pick_players(self):
        # I'm paranoid
        for i in range(5):
            self._rand.shuffle(self._members)
        # Set special roles first
        for role in self._config.special_roles:
            # Get member that will have this role
            member = self._members.pop()
            self.players.append(role(member))
        # Then get the remaining normal mafia needed
        for i in range(self._config.starting_mafia - self.total_mafia):
            member = self._members.pop()
            self.players.append(self.ctx.bot.mafia_role(member))
        # The rest are citizens
        while self._members:
            member = self._members.pop()
            self.players.append(self.ctx.bot.citizen_role(member))

    async def setup_channels(self):
        # Get category, create if it doesn't exist yet
        category = await self.ctx.guild.create_category_channel("MAFIA GAME")
        channels_needed = collections.defaultdict(dict)
        # All of these channel overwrites are the same concept:
        # Everyone role has read_messages disabled, send_messages enabled (will swap based on day/night)
        # Bot has read_messages enabled, send_messages enabled
        # The person has read_messages enabled
        # This allows read messages to be overridden by the person, making sure only they can
        # see the channel, it will never be touched. We will change send_messages only on the
        # everyone role, allowing only one update for everyone in a single role.
        # We cannot use roles for this task, because everyone can see other people's roles

        # Info channel
        channels_needed["info"][
            self.ctx.guild.default_role
        ] = default_role_disabled_overwrites
        channels_needed["info"][self.ctx.guild.me] = bot_overwrites
        # Chat channel
        channels_needed["chat"][self.ctx.guild.default_role] = default_role_overwrites
        channels_needed["chat"][self.ctx.guild.me] = bot_overwrites
        # Mafia channel
        channels_needed["mafia_chat"][
            self.ctx.guild.default_role
        ] = default_role_overwrites
        channels_needed["mafia_chat"][self.ctx.guild.me] = bot_overwrites
        # Dead channel
        channels_needed["dead_chat"][
            self.ctx.guild.default_role
        ] = default_role_overwrites
        channels_needed["dead_chat"][self.ctx.guild.me] = bot_overwrites
        # Jail
        channels_needed["jail"][self.ctx.guild.default_role] = jail_overwrites
        channels_needed["jail"][self.ctx.guild.me] = bot_overwrites
        for player in self.players:
            # If mafia let them see mafia
            if player.is_mafia:
                channels_needed["mafia_chat"][player.member] = user_overwrites
            # Let everyone see the chat and info
            channels_needed["chat"][player.member] = user_overwrites
            channels_needed["info"][player.member] = user_overwrites
            # Their player channel
            channels_needed[player][
                self.ctx.guild.default_role
            ] = default_role_overwrites
            channels_needed[player][player.member] = user_overwrites
            channels_needed[player][self.ctx.guild.me] = bot_overwrites
        # Now simply set all channels and overwrites
        for player, overwrite in channels_needed.items():
            # Set the special channels
            if player in ["mafia_chat", "chat", "info", "dead_chat", "jail"]:
                current_channel = await category.create_text_channel(
                    player, overwrites=overwrite
                )
                setattr(self, player, current_channel)
                # Create the webhook we'll use for this
                if player == "jail":
                    b = await self.ctx.bot.user.avatar_url.read()
                    self.jail_webhook = await current_channel.create_webhook(
                        name="Jailor", avatar=b
                    )
            # All personal channels
            else:
                channel = player.member.display_name.lower()
                current_channel = await category.create_text_channel(
                    channel,
                    overwrites=overwrite,
                    topic=f"Your role is {player}",
                )
                player.set_channel(current_channel)
                msg = await current_channel.send(player.startup_channel_message(self))
                await msg.pin()

    async def lock_chat_channel(self):
        await self.chat.set_permissions(
            self.ctx.guild.default_role, overwrite=default_role_disabled_overwrites
        )

    async def unlock_chat_channel(self):
        await self.chat.set_permissions(
            self.ctx.guild.default_role, overwrite=default_role_overwrites
        )

    async def lock_mafia_channel(self):
        await self.mafia_chat.set_permissions(
            self.ctx.guild.default_role, overwrite=default_role_disabled_overwrites
        )

    async def unlock_mafia_channel(self):
        await self.mafia_chat.set_permissions(
            self.ctx.guild.default_role, overwrite=default_role_overwrites
        )

    async def play(self):
        """Handles the preparation and the playing of the game"""
        await self._prepare()
        await self._start()

    async def redo(self):
        await self.cleanup_channels()
        await self._start()

    async def _setup_amount_players(self) -> typing.Tuple(int, int):
        ctx = self.ctx
        minimum_players_needed = 3
        # Get max players
        await ctx.send(
            "Starting new game of Mafia! Please first select how many players "
            "you want to allow to play the game at maximum?"
        )
        answer = await ctx.bot.wait_for(
            "message", check=ctx.bot.min_max_check(ctx, minimum_players_needed, 25)
        )
        max_players = int(answer.content)
        # Min players
        await ctx.send("How many players at minimum?")
        answer = await ctx.bot.wait_for(
            "message",
            check=ctx.bot.min_max_check(ctx, minimum_players_needed, max_players),
        )
        min_players = int(answer.content)

        return min_players, max_players

    async def _setup_players(
        self, min_players: int, max_players: int
    ) -> typing.List[players.Player]:
        wait_length_for_players_to_join = 60
        ctx = self.ctx
        game_players = set()

        async def wait_for_players():
            nonlocal game_players
            game_players = set()
            # Now start waiting for the players to actually join
            embed = discord.Embed(
                title="Mafia game!",
                description=f"Press \N{WHITE HEAVY CHECK MARK} to join! Waiting till at least {min_players} join. "
                f"After that will wait for {wait_length_for_players_to_join} seconds for the rest of the players to join",
                thumbnail=ctx.guild.icon_url,
            )
            embed.set_footer(text=f"{len(game_players)}/{min_players} Needed to join")
            msg = await ctx.send(embed=embed)
            await msg.add_reaction("\N{WHITE HEAVY CHECK MARK}")

            timer_not_started = True
            # Start the event here so that the update can use it
            join_event = asyncio.Event()

            async def update_embed(start_timeout=False):
                if start_timeout:
                    nonlocal timer_not_started
                    timer_not_started = False
                    embed.description += f"\n\nMin players reached! Waiting {wait_length_for_players_to_join} seconds or till max players ({max_players}) reached"
                    embed.set_footer(
                        text=f"{len(game_players)}/{min_players} Needed to join"
                    )
                    await msg.edit(embed=embed)
                    await asyncio.sleep(wait_length_for_players_to_join)
                    join_event.set()
                else:
                    embed.set_footer(
                        text=f"{len(game_players)}/{min_players} Needed to join"
                    )
                    await msg.edit(embed=embed)

            def check(p):
                # First don't accept any reactions that aren't actually people joining/leaving
                if p.message_id != msg.id:
                    return False
                if str(p.emoji) != "\N{WHITE HEAVY CHECK MARK}":
                    return False
                if p.user_id == ctx.bot.user.id:
                    return False
                if p.event_type == "REACTION_ADD":
                    game_players.add(p.user_id)
                    # If we've hit the max, finish
                    if len(game_players) == max_players:
                        return True
                # Only allow people to leave if we haven't hit the min
                if p.event_type == "REACTION_REMOVE":
                    game_players.remove(p.user_id)
                ctx.create_task(
                    update_embed(
                        start_timeout=len(game_players) == min_players
                        and timer_not_started
                    )
                )

            done, pending = await asyncio.wait(
                [
                    self.ctx.create_task(
                        ctx.bot.wait_for("raw_reaction_add", check=check)
                    ),
                    self.ctx.create_task(
                        ctx.bot.wait_for("raw_reaction_remove", check=check)
                    ),
                    self.ctx.create_task(join_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=300,
            )

            for task in pending:
                task.cancel()

            # If nothing was done, then the timeout happened
            if not done:
                raise asyncio.TimeoutError()

            return len(game_players) >= min_players

        for _ in range(5):
            if await wait_for_players():
                break

        if len(game_players) < min_players:
            await ctx.send("Failed to get players too many times")
            raise Exception()

        # Get the member objects
        game_players = await ctx.guild.query_members(user_ids=list(game_players))
        admins = [p.mention for p in game_players if p.guild_permissions.administrator]
        if admins:
            await ctx.send(
                "There are admins in this game, which means I cannot hide the "
                f"game channels from them. I will DM you the role you have {','.join(admins)}"
                ". Please only check the corresponding channel and the chat channel. "
                "Don't chat during the night, only respond to prompts in your channel. **Please "
                "make sure your DMs are open on this server, the game WILL fail to start if I can't "
                "DM you.**"
            )

        return game_players

    async def _setup_amount_mafia(self, players: int) -> int:
        ctx = self.ctx
        # Get amount of Mafia
        await ctx.send(
            f"How many mafia members (including special mafia members; Between 1 and {int(players / 2)})?"
        )
        answer = await ctx.bot.wait_for(
            "message", check=ctx.bot.min_max_check(ctx, 1, int(players / 2))
        )
        amount_of_mafia = int(answer.content)

        return amount_of_mafia

    async def _setup_special_roles(
        self, players: int, mafia: int
    ) -> typing.List[typing.Tuple(players.Player, int)]:
        ctx = self.ctx
        amount_of_specials = [(k, 0) for k in ctx.bot.__special_roles__]
        menu = ctx.bot.MafiaMenu(source=ctx.bot.MafiaPages(amount_of_specials, ctx))

        menu.amount_of_players = players
        menu.amount_of_mafia = mafia
        await menu.start(ctx, wait=True)

        return amount_of_specials

    async def _prepare(self):
        """All the setup needed for the game to play"""
        ctx = self.ctx
        # Get/create the alive role
        self._alive_game_role = discord.utils.get(
            ctx.guild.roles, name=self._alive_game_role_name
        )
        if self._alive_game_role is None:
            self._alive_game_role = await ctx.guild.create_role(
                name=self._alive_game_role_name, hoist=True
            )

        # Config is already set
        if self._preconfigured_config:
            # Convert hex to the stuff we care about
            (
                amount_of_mafia,
                min_players,
                max_players,
                special_roles,
            ) = ctx.bot.hex_to_players(
                self._preconfigured_config, ctx.bot.__special_roles__
            )
            # The only setup we need to do is get the players who will player
            self._members = await self._setup_players(min_players, max_players)
            # Set the config
            self._config = ctx.bot.MafiaGameConfig(amount_of_mafia, special_roles, ctx)
        else:
            # Go through normal setup. Amount of players, letting players join, amount of mafia, special roles
            min_players, max_players = await self._setup_amount_players()
            self._members = await self._setup_players(min_players, max_players)
            amount_of_mafia = await self._setup_amount_mafia(len(self._members))
            special_roles = await self._setup_special_roles(
                len(self._members), amount_of_mafia
            )
            # Convert the tuple of player, amount to just a list of all roles
            special_roles = [role for (role, amt) in special_roles for i in range(amt)]
            # Get hex to allow them to use this setup in the future
            h = ctx.bot.players_to_hex(
                special_roles, amount_of_mafia, min_players, max_players
            )
            await self.ctx.send(
                "In the future you can provide this to the mafia start command "
                f"to use the exact same configuration:\n{h}"
            )
            # Now that the setup is done, create the configuration for the game
            self._config = ctx.bot.MafiaGameConfig(amount_of_mafia, special_roles, ctx)

        for member in self._members:
            await member.add_roles(self._alive_game_role)

    async def _cycle(self) -> bool:
        """Performs one cycle of day/night"""
        # Do day tasks and check for winner
        await self.pre_day()
        if self.check_winner():
            return True
        await self.day_tasks()
        if self.check_winner():
            return True
        self._day += 1
        # Do night tasks and check for winner
        await self.night_tasks()
        if self.check_winner():
            return True

        # Schedule all the post night tasks
        for player in self.players:
            self.ctx.create_task(player.post_night_task(self))

        return False

    async def _start(self):
        """Play the game"""
        # Sort out the players
        await self.pick_players()
        # Setup the categories and channels
        await self.setup_channels()
        # Now choose the godfather
        await self.choose_godfather()
        # Mafia channel must be locked
        await self.lock_mafia_channel()

        while True:
            if await self._cycle():
                break

        # The game is done, allow dead players to chat again
        for player in self.players:
            if player.dead:
                await self.chat.set_permissions(player.member, read_messages=True)

        # Send winners
        winners = self.get_winners()
        msg = "Winners are:\n{}".format(
            "\n".join(f"{winner.member.name} ({winner})" for winner in winners)
        )
        await self.chat.send(msg)
        # Send a message with everyone's roles
        msg = "\n".join(
            f"{player.member.mention} ({player})" for player in self.players
        )
        await self.ctx.send(msg, allowed_mentions=AllowedMentions(users=False))
        await asyncio.sleep(60)
        await self.cleanup_channels()

    def stop(self):
        if self._game_task:
            self._game_task.cancel()

    async def night_tasks(self):
        await self.night_notification()
        await self.lock_chat_channel()
        await self.unlock_mafia_channel()
        # Schedule tasks. Add the asyncio sleep to *ensure* we sleep that long
        # even if everyone finishes early
        async def night_sleep():
            await asyncio.sleep(self._config.night_length - 20)
            await self.mafia_chat.send("Night is about to end in 20 seconds")
            await asyncio.sleep(20)

        tasks = [self.ctx.create_task(night_sleep())]
        mapping = {
            count: player.member.name
            for count, player in enumerate(self.players)
            if not player.is_mafia and not player.dead
        }
        msg = "\n".join(f"{count}: {player}" for count, player in mapping.items())

        godfather = self.godfather
        if godfather.night_role_blocked:
            await self.mafia_chat.send("The godfather cannot kill tonight!")
        else:
            await self.mafia_chat.send(
                "**Godfather:** Type the number assigned to a member to kill someone. "
                f"Alive players are:\n{msg}"
            )

            async def mafia_check():
                msg = await self.ctx.bot.wait_for(
                    "message",
                    check=self.ctx.bot.mafia_kill_check(self, mapping),
                )
                player = mapping[int(msg.content)]
                player = self.ctx.bot.get_mafia_player(self, player)
                # They were protected during the day
                if (
                    player.protected_by
                    and player.protected_by.defense_type >= godfather.attack_type
                ):
                    await self.mafia_chat.send(
                        "That target has been protected for the night! Your attack failed!"
                    )
                else:
                    player.kill(godfather)
                    await self.mafia_chat.send("\N{THUMBS UP SIGN}")

            tasks.append(mafia_check())

        for p in self.players:
            if p.dead or p.night_role_blocked:
                p.night_role_blocked = False
                continue
            task = self.ctx.create_task(p.night_task(self))
            tasks.append(task)

        _, pending = await asyncio.wait(
            tasks, timeout=self._config.night_length, return_when=asyncio.ALL_COMPLETED
        )
        # Cancel pending tasks, times up
        for task in pending:
            task.cancel()

        await self.lock_mafia_channel()

    async def pre_day(self):
        notifs = []
        if self._day > 1:
            killed = []

            for player in self.players:
                # Already dead people
                if player.dead:
                    continue
                # If they were killed by someone
                if killer := player.killed_by:
                    # If protected, check the power of protection against attacking
                    if (
                        player.protected_by
                        and killer.attack_type > player.protected_by.defense_type
                    ):
                        if player.channel:
                            await player.channel.send(
                                f"You were killed last night, but {player.protected_by} saved you!"
                            )
                        if killer.is_mafia:
                            await self.mafia_chat.send(
                                "{player} was saved last night from your attack!"
                            )
                    else:
                        # If they were cleaned, let the cleaner know their role
                        if cleaner := player.cleaned_by:
                            await cleaner.channel.send(
                                f"You cleaned {player.member.name} up, their role was {player}"
                            )
                        # Only notify if the body wasn't cleaned
                        else:
                            notifs.append(
                                f"- {player.member.mention} ({player}) was killed by {killer}"
                            )
                            await self.chat.send(
                                f"- {player.member.mention} ({player}) was killed during the night!"
                            )
                            # Just to check if someone was killed
                            killed.append(player)

                        player.dead = True
                        await player.member.remove_roles(self._alive_game_role)
                        # This will permanently disable them from talking
                        await self.chat.set_permissions(
                            player.member, read_messages=True, send_messages=False
                        )
                        if player.channel:
                            await player.channel.set_permissions(
                                player.member, read_messages=True, send_messages=False
                            )
                        if player.is_mafia:
                            await self.mafia_chat.set_permissions(
                                player.member, read_messages=True, send_messages=False
                            )
                        await self.dead_chat.set_permissions(
                            player.member, read_messages=True
                        )
                        # Now if they were godfather, choose new godfather
                        if player.is_godfather:
                            await self.choose_godfather()
                            player.is_godfather = False

            if not killed:
                notifs.append("- No one was killed last night!")

            await self.day_notification(*notifs)
        else:
            await self.day_notification("- Game has started!")

        # Cleanup everyone's attrs
        for p in self.players:
            if not p.dead:
                p.cleanup_attrs()
        # Unlock the channel
        await self.unlock_chat_channel()

    async def day_tasks(self):
        day_length = (
            self._config.day_length if self._day > 1 else self._config.day_length / 2
        )

        # Ensure day takes this long no matter what
        async def day_sleep():
            await asyncio.sleep(day_length - 20)
            await self.chat.send("Day is about to end in 20 seconds")
            await asyncio.sleep(20)

        tasks = [self.ctx.create_task(day_sleep())]

        nominations = {}
        msg = None

        async def nominate_player():
            nonlocal msg
            await self.ctx.bot.wait_for(
                "message",
                check=self.ctx.bot.nomination_check(self, nominations, self.chat),
            )
            # If we've passed to here that's two nominations
            msg = await self.chat.send(
                f"{nominations['nomination'].member.mention} is nominated for hanging! React to vote "
                "By the end of the day, all the votes will be tallied. If majority voted yes, they "
                "will be hung"
            )
            await msg.add_reaction("\N{THUMBS UP SIGN}")
            await msg.add_reaction("\N{THUMBS DOWN SIGN}")

        for p in self.players:
            # Dead players can't do shit
            if p.dead:
                continue
            task = self.ctx.create_task(p.day_task(self))
            tasks.append(task)

        if self._day > 1:
            tasks.append(self.ctx.create_task(nominate_player()))
        _, pending = await asyncio.wait(
            tasks, timeout=day_length, return_when=asyncio.ALL_COMPLETED
        )
        # Cancel pending tasks, times up
        for task in pending:
            task.cancel()

        # Now check for msg, if it's here then there was a hanging vote
        if msg:
            # Reactions aren't updated in place, need to refetch
            msg = await msg.channel.fetch_message(msg.id)
            yes_votes = discord.utils.get(msg.reactions, emoji="\N{THUMBS UP SIGN}")
            no_votes = discord.utils.get(msg.reactions, emoji="\N{THUMBS DOWN SIGN}")
            yes_count = 0
            no_count = 0
            async for user in yes_votes.users():
                if [p for p in self.players if p.member == user and not p.dead]:
                    yes_count += 1
            async for user in no_votes.users():
                if [p for p in self.players if p.member == user and not p.dead]:
                    no_count += 1
            # The lynching happened
            if yes_count > no_count:
                player = nominations["nomination"]
                player.dead = True
                player.lynched = True
                await self.chat.set_permissions(
                    player.member, read_messages=True, send_messages=False
                )
                if player.channel:
                    await player.channel.set_permissions(
                        player.member, read_messages=True, send_messages=False
                    )
                if player.is_mafia:
                    await self.mafia_chat.set_permissions(
                        player.member, read_messages=True, send_messages=False
                    )
                    if player.is_godfather:
                        try:
                            await self.choose_godfather()
                        # If there's mafia, citizens win. Just return, the cycle will handle it
                        except IndexError:
                            return
                await self.day_notification(
                    f"- The town lynched **{player.member.mention}**({player})"
                )
                await player.member.remove_roles(self._alive_game_role)
                await self.dead_chat.set_permissions(player.member, read_messages=True)

        await self.lock_chat_channel()

    async def cleanup_channels(self):
        for player in self.players:
            await player.member.remove_roles(self._alive_game_role)

        try:
            category = self.chat.category
            for channel in category.channels:
                await channel.delete()
            await category.delete()
        except (AttributeError, discord.HTTPException):
            pass


def setup(bot):
    bot.MafiaGameConfig = MafiaGameConfig
    bot.MafiaGame = MafiaGame


def teardown(bot):
    del bot.MafiaGameConfig
    del bot.MafiaGame
