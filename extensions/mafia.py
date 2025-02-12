import asyncio
from datetime import datetime
import typing

import discord
from discord.ext import commands, menus
from extensions.players import Player


def stop_check():
    def predicate(ctx):
        game = ctx.bot.get_cog("Mafia").games.get(ctx.guild.id)

        if game and (
            ctx.author.guild_permissions.manage_channels
            or ctx.author == game[1].ctx.author
        ):
            return True
        return False

    return commands.check(predicate)


class RolesSource(menus.ListPageSource):
    def __init__(self, data: typing.List[Player]):
        super().__init__(data, per_page=10)

    async def format_page(self, menu: menus.Menu, entries: typing.List[Player]):
        embed = discord.Embed(
            title="Roles",
            color=0xFF0000,
            timestamp=datetime.utcnow(),
        )
        embed.set_author(
            name="Dev Server",
            url="https://discord.gg/B6qJ4NKGvp",
            icon_url=menu.ctx.bot.user.avatar_url,
        )
        for role in entries:
            embed.add_field(
                name=role.__name__, value=role.short_description, inline=False
            )

        embed.set_footer(text=f"Page {menu.current_page + 1}/{self._max_pages}")
        return embed


class Mafia(commands.Cog):
    games = {}
    # Useful for restarting a game, or getting info on the last game
    previous_games = {}

    @commands.group(invoke_without_command=True)
    async def mafia(self, ctx):
        """The parent command to handle mafia games"""
        await ctx.bot.get_command("help")(ctx)

    @mafia.command(name="start")
    @commands.guild_only()
    @commands.max_concurrency(1, per=commands.BucketType.guild)
    async def mafia_start(self, ctx, config: str = None):
        """Start a game of mafia. Note that currently only one game can run at a time
        per server, this limit may be upped in the future"""
        # This can happen if we're redoing a game
        game = ctx.bot.MafiaGame(ctx, config=config)
        # Store task so it can be cancelled later
        task = ctx.bot.loop.create_task(game.play())
        self.games[ctx.guild.id] = (task, game)
        try:
            await task
        except asyncio.TimeoutError:
            task.cancel()
            await ctx.send("Timed out waiting for players to join")
        # Remove game once it's done
        self.previous_games[ctx.guild.id] = game
        del self.games[ctx.guild.id]

    @mafia.command(name="redo")
    @commands.guild_only()
    @commands.max_concurrency(1, per=commands.BucketType.guild)
    async def mafia_redo(self, ctx):
        """Starts another game with the same configuration as the last"""
        game = self.previous_games.get(ctx.guild.id)
        if game:
            await self.mafia_start(ctx, game._preconfigured_config)
        else:
            await ctx.send("No previous game detected")

    @mafia.command(name="cleanup")
    @commands.has_permissions(manage_channels=True)
    @commands.guild_only()
    async def mafia_cleanup(self, ctx):
        """Cleans up all mafia channels. This usually shouldn't be needed, unless something
        went wrong with the bot's auto cleanup which happens a minute after a game finishes"""
        for category in ctx.guild.categories:
            if category.name == "MAFIA GAME":
                for channel in category.channels:
                    await channel.delete()
                await category.delete()

        await ctx.send("\N{THUMBS UP SIGN}")

    @mafia.command(name="stop", aliases=["cancel"])
    @stop_check()
    @commands.guild_only()
    async def mafia_stop(self, ctx):
        """Stops an ongoing game of Mafia"""
        await ctx.send("\N{THUMBS UP SIGN}")

        game = self.games.get(ctx.guild.id)
        if game is not None:
            del self.games[ctx.guild.id]
            task, game = game
            task.cancel()
            await game.cleanup_channels()

    @mafia.command(name="roles")
    async def mafia_roles(self, ctx):
        """Displays the available custom roles"""
        menu = menus.MenuPages(
            source=RolesSource(ctx.bot.__special_roles__), clear_reactions_after=True
        )
        await menu.start(ctx)

    @mafia.command(name="role")
    async def mafia_role(self, ctx, role: Player):
        """Displays the information for the provided role"""
        embed = discord.Embed(
            title=role,
            description=role.description,
            color=0xFF0000,
            timestamp=datetime.utcnow(),
        )
        embed.set_author(
            name="Dev Server",
            url="https://discord.gg/B6qJ4NKGvp",
            icon_url=ctx.bot.user.avatar_url,
        )
        await ctx.send(embed=embed)

    @mafia.command(name="rules", aliases=["tutorial", "guide"])
    async def mafia_rules(self, ctx):
        """Displays the rules for this game of mafia"""
        await self.guide(ctx)

    @mafia_start.error
    async def clean_mafia_games(self, ctx, error):
        if isinstance(
            error, (commands.MaxConcurrencyReached, commands.CommandOnCooldown)
        ):
            return

        game = self.games.get(ctx.guild.id)
        if game is not None:
            await ctx.send("Encountered an error, cleaning up channels...")
            task, game = game
            task.cancel()
            del self.games[ctx.guild.id]
            await game.cleanup_channels()

    @commands.command(aliases=["tutorial"])
    async def guide(self, ctx):
        """Displays the rules for this game of mafia"""
        desc = """
The rules of mafia are prety straight forward:
- There are day and night cycles
- During the day Citizens try to figure out who the Mafia is, and lynch them
- During the night Mafia kill one player
- Citizens win if all Mafia are dead
- Mafia win if a majority of the town ar Mafia
- Independent roles each have their own separate win condition, this will be described in your private chat

Main chats during the game:
- chat: The main chat, this is where you'll talk during the day to figure out your information
- mafia-chat: The chat for the mafia, this is where Mafia will talk during the night and where the Godfather will choose who dies
- dead-chat: The chat anyone who is dead can talk in
- your-name: This is your private chat, the role you have will be explained here. If you have special tasks to complete, they'll be handled here
        """
        embed = discord.Embed(
            title="Mafia rules",
            description=desc,
            color=0xFF0000,
            timestamp=datetime.utcnow(),
        )
        embed.set_author(
            name="Dev Server",
            url="https://discord.gg/B6qJ4NKGvp",
            icon_url=ctx.bot.user.avatar_url,
        )
        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Mafia())
