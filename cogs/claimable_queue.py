import logging

import discord
from discord.ext import commands

from classes.embed import Embed, ErrorEmbed
from utils import checks, tools

log = logging.getLogger(__name__)


class ClaimableQueue(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._old_before_invoke = bot._before_invoke
        bot.before_invoke(self._warn_if_not_claimer)

    def cog_unload(self):
        self.bot._before_invoke = self._old_before_invoke

    async def _is_enabled(self, guild_id):
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled FROM claimable_queue_settings WHERE guild_id=$1",
                guild_id,
            )
        return row is not None and row["enabled"]

    async def _warn_if_not_claimer(self, ctx):
        if not ctx.guild:
            return
        if ctx.command is None:
            return
        if ctx.command.name not in ("reply", "areply", "aireply"):
            return
        if not tools.is_modmail_channel(ctx.channel):
            return
        if not await self._is_enabled(ctx.guild.id):
            return
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT claimed_by FROM ticket_claims WHERE channel_id=$1",
                ctx.channel.id,
            )
        if row is None or row["claimed_by"] is None:
            return
        if row["claimed_by"] == ctx.author.id:
            return
        try:
            claimer = await ctx.guild.fetch_member(row["claimed_by"])
            claimer_name = str(claimer)
        except discord.NotFound:
            claimer_name = f"<@{row['claimed_by']}>"
        await ctx.send(
            Embed(
                f"This ticket is claimed by **{claimer_name}**. "
                f"Use `{ctx.prefix}claim --force` to take over."
            )
        )

    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @commands.group(
        name="claimqueue",
        description="Manage the claimable queue feature.",
        usage="claimqueue <enable|disable>",
        invoke_without_command=True,
    )
    async def claimqueue(self, ctx):
        enabled = await self._is_enabled(ctx.guild.id)
        status = "enabled" if enabled else "disabled"
        await ctx.send(Embed(f"Claimable queue is currently **{status}**."))

    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @claimqueue.command(name="enable", description="Enable the claimable queue feature.")
    async def claimqueue_enable(self, ctx):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO claimable_queue_settings (guild_id, enabled) VALUES ($1, TRUE) "
                "ON CONFLICT (guild_id) DO UPDATE SET enabled=TRUE",
                ctx.guild.id,
            )
        await ctx.send(Embed(f"Claimable queue enabled. Staff must use `{ctx.prefix}claim` before replying."))

    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @claimqueue.command(name="disable", description="Disable the claimable queue feature.")
    async def claimqueue_disable(self, ctx):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO claimable_queue_settings (guild_id, enabled) VALUES ($1, FALSE) "
                "ON CONFLICT (guild_id) DO UPDATE SET enabled=FALSE",
                ctx.guild.id,
            )
        await ctx.send(Embed("Claimable queue disabled."))

    @checks.is_modmail_channel()
    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @commands.command(
        name="claim",
        description="Claim the current ticket.",
        usage="claim [--force]",
    )
    async def claim(self, ctx, flag: str = None):
        if not await self._is_enabled(ctx.guild.id):
            await ctx.send(ErrorEmbed("Claimable queue is not enabled on this server."))
            return
        force = flag == "--force"
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT claimed_by FROM ticket_claims WHERE channel_id=$1",
                ctx.channel.id,
            )
            if row and row["claimed_by"] is not None:
                if row["claimed_by"] == ctx.author.id:
                    await ctx.send(Embed("You already have this ticket claimed."))
                    return
                if not force:
                    try:
                        claimer = await ctx.guild.fetch_member(row["claimed_by"])
                        claimer_name = str(claimer)
                    except discord.NotFound:
                        claimer_name = f"<@{row['claimed_by']}>"
                    await ctx.send(
                        ErrorEmbed(
                            f"This ticket is already claimed by **{claimer_name}**. "
                            f"Use `{ctx.prefix}claim --force` to take over."
                        )
                    )
                    return
            await conn.execute(
                "INSERT INTO ticket_claims (channel_id, guild_id, claimed_by, claimed_at) "
                "VALUES ($1, $2, $3, NOW()) "
                "ON CONFLICT (channel_id) DO UPDATE SET claimed_by=$3, claimed_at=NOW()",
                ctx.channel.id,
                ctx.guild.id,
                ctx.author.id,
            )
        await ctx.send(Embed(f"Ticket claimed by **{ctx.author}**."))

    @checks.is_modmail_channel()
    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @commands.command(
        name="unclaim",
        description="Release your claim on the current ticket.",
        usage="unclaim",
    )
    async def unclaim(self, ctx):
        if not await self._is_enabled(ctx.guild.id):
            await ctx.send(ErrorEmbed("Claimable queue is not enabled on this server."))
            return
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT claimed_by FROM ticket_claims WHERE channel_id=$1",
                ctx.channel.id,
            )
            if row is None or row["claimed_by"] is None:
                await ctx.send(Embed("This ticket is not currently claimed."))
                return
            if row["claimed_by"] != ctx.author.id:
                await ctx.send(ErrorEmbed("You cannot unclaim a ticket that isn't yours."))
                return
            await conn.execute(
                "UPDATE ticket_claims SET claimed_by=NULL, claimed_at=NULL WHERE channel_id=$1",
                ctx.channel.id,
            )
        await ctx.send(Embed("Ticket unclaimed."))


def setup(bot):
    bot.add_cog(ClaimableQueue(bot))