import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

from classes.embed import Embed, ErrorEmbed
from utils import checks, tools

log = logging.getLogger(__name__)

DURATION_RE = re.compile(r"^(\d+)([hdw])$")


def parse_duration(value):
    match = DURATION_RE.match(value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    return None


class ScheduledFollowups(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._task = bot.loop.create_task(self._followup_loop())

    def cog_unload(self):
        self._task.cancel()

    async def _is_enabled(self, guild_id):
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled FROM followup_settings WHERE guild_id=$1",
                guild_id,
            )
        return row is not None and row["enabled"]

    def _format_message(self, template, user, channel_id, guild_name):
        return (
            template
            .replace("{user}", user.mention)
            .replace("{ticket_id}", str(channel_id))
            .replace("{server}", guild_name)
        )

    async def _followup_loop(self):
        await asyncio.sleep(10)
        while True:
            try:
                await self._process_followups()
            except Exception as e:
                log.error(f"Follow-up loop error: {e}")
            await asyncio.sleep(60)

    async def _process_followups(self):
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM follow_ups WHERE triggered=FALSE AND due_at <= NOW()"
            )
            for row in rows:
                try:
                    user = await self.bot.fetch_user(row["user_id"])
                    guild = await self.bot.get_guild(row["guild_id"])
                    guild_name = guild.name if guild else "the server"
                    settings = await conn.fetchrow(
                        "SELECT followup_message FROM followup_settings WHERE guild_id=$1",
                        row["guild_id"],
                    )
                    if settings and settings["followup_message"]:
                        message = self._format_message(
                            settings["followup_message"],
                            user,
                            row["channel_id"],
                            guild_name,
                        )
                    else:
                        message = (
                            f"Hi {user.mention}, this is a follow-up from **{guild_name}**. "
                            "Feel free to reply if you need further assistance."
                        )
                    try:
                        await user.send(message)
                    except discord.Forbidden:
                        pass
                except Exception as e:
                    log.error(f"Failed to process follow-up {row['id']}: {e}")
                finally:
                    await conn.execute(
                        "UPDATE follow_ups SET triggered=TRUE WHERE id=$1",
                        row["id"],
                    )

    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @commands.group(
        name="followup",
        description="Manage scheduled follow-ups.",
        usage="followup <enable|disable|cancel|message|<duration>>",
        invoke_without_command=True,
    )
    async def followup(self, ctx, duration: str = None):
        if duration is None:
            enabled = await self._is_enabled(ctx.guild.id)
            status = "enabled" if enabled else "disabled"
            await ctx.send(Embed(f"Scheduled follow-ups are currently **{status}**."))
            return
        if not await self._is_enabled(ctx.guild.id):
            await ctx.send(ErrorEmbed("Scheduled follow-ups are not enabled on this server."))
            return
        if not tools.is_modmail_channel(ctx.channel):
            await ctx.send(ErrorEmbed("This command can only be used in a ticket channel."))
            return
        delta = parse_duration(duration)
        if delta is None:
            await ctx.send(ErrorEmbed("Invalid duration. Use formats like `1h`, `3d`, or `2w`."))
            return
        user_id = tools.get_modmail_user(ctx.channel).id
        due_at = datetime.now(timezone.utc) + delta
        async with self.bot.pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM follow_ups WHERE channel_id=$1 AND triggered=FALSE",
                ctx.channel.id,
            )
            if existing:
                await ctx.send(
                    ErrorEmbed(
                        f"This ticket already has a pending follow-up. "
                        f"Use `{ctx.prefix}followup cancel` first."
                    )
                )
                return
            await conn.execute(
                "INSERT INTO follow_ups (channel_id, guild_id, user_id, due_at) "
                "VALUES ($1, $2, $3, $4)",
                ctx.channel.id,
                ctx.guild.id,
                user_id,
                due_at,
            )
        await ctx.send(Embed(f"Follow-up scheduled in **{duration}**. Close the ticket when ready."))

    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @followup.command(name="enable", description="Enable scheduled follow-ups.")
    async def followup_enable(self, ctx):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO followup_settings (guild_id, enabled) VALUES ($1, TRUE) "
                "ON CONFLICT (guild_id) DO UPDATE SET enabled=TRUE",
                ctx.guild.id,
            )
        await ctx.send(Embed("Scheduled follow-ups enabled."))

    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @followup.command(name="disable", description="Disable scheduled follow-ups.")
    async def followup_disable(self, ctx):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO followup_settings (guild_id, enabled) VALUES ($1, FALSE) "
                "ON CONFLICT (guild_id) DO UPDATE SET enabled=FALSE",
                ctx.guild.id,
            )
        await ctx.send(Embed("Scheduled follow-ups disabled."))

    @checks.is_modmail_channel()
    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @followup.command(name="cancel", description="Cancel the pending follow-up on this ticket.")
    async def followup_cancel(self, ctx):
        if not await self._is_enabled(ctx.guild.id):
            await ctx.send(ErrorEmbed("Scheduled follow-ups are not enabled on this server."))
            return
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM follow_ups WHERE channel_id=$1 AND triggered=FALSE",
                ctx.channel.id,
            )
            if not row:
                await ctx.send(Embed("No pending follow-up found for this ticket."))
                return
            await conn.execute(
                "UPDATE follow_ups SET triggered=TRUE WHERE id=$1",
                row["id"],
            )
        await ctx.send(Embed("Follow-up cancelled."))

    @checks.is_premium()
    @checks.in_database()
    @checks.is_mod()
    @commands.guild_only()
    @followup.command(
        name="message",
        description="Set the follow-up DM template. Supports {user}, {ticket_id}, {server}.",
        usage="message <text>",
    )
    async def followup_message(self, ctx, *, text: str):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO followup_settings (guild_id, enabled, followup_message) "
                "VALUES ($1, FALSE, $2) "
                "ON CONFLICT (guild_id) DO UPDATE SET followup_message=$2",
                ctx.guild.id,
                text,
            )
        await ctx.send(Embed("Follow-up message template updated."))


def setup(bot):
    bot.add_cog(ScheduledFollowups(bot))