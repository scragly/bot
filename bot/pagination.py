from __future__ import annotations

import asyncio
import logging
import typing as t
from contextlib import suppress
from collections import defaultdict
import abc
import textwrap

import discord
from discord.abc import User
from discord.ext.commands import Context, Paginator
from more_itertools import chunked

from bot import constants
from bot.bot import Bot

FIRST_EMOJI = "\u23EE"   # [:track_previous:]
LEFT_EMOJI = "\u2B05"    # [:arrow_left:]
RIGHT_EMOJI = "\u27A1"   # [:arrow_right:]
LAST_EMOJI = "\u23ED"    # [:track_next:]
DELETE_EMOJI = constants.Emojis.trashcan  # [:trashcan:]

PAGINATION_EMOJI = (FIRST_EMOJI, LEFT_EMOJI, RIGHT_EMOJI, LAST_EMOJI, DELETE_EMOJI)

log = logging.getLogger(__name__)


class EmptyPaginatorEmbed(Exception):
    """Raised when attempting to paginate with empty contents."""

    pass


class LinePaginator(Paginator):
    """
    A class that aids in paginating code blocks for Discord messages.

    Available attributes include:
    * prefix: `str`
        The prefix inserted to every page. e.g. three backticks.
    * suffix: `str`
        The suffix appended at the end of every page. e.g. three backticks.
    * max_size: `int`
        The maximum amount of codepoints allowed in a page.
    * scale_to_size: `int`
        The maximum amount of characters a single line can scale up to.
    * max_lines: `int`
        The maximum amount of lines allowed in a page.
    """

    def __init__(
        self,
        prefix: str = '```',
        suffix: str = '```',
        max_size: int = 2000,
        scale_to_size: int = 2000,
        max_lines: t.Optional[int] = None
    ) -> None:
        """
        This function overrides the Paginator.__init__ from inside discord.ext.commands.

        It overrides in order to allow us to configure the maximum number of lines per page.
        """
        self.prefix = prefix
        self.suffix = suffix

        # Embeds that exceed 2048 characters will result in an HTTPException
        # (Discord API limit), so we've set a limit of 2000
        if max_size > 2000:
            raise ValueError(f"max_size must be <= 2,000 characters. ({max_size} > 2000)")

        self.max_size = max_size - len(suffix)

        if scale_to_size < max_size:
            raise ValueError(f"scale_to_size must be >= max_size. ({scale_to_size} < {max_size})")

        if scale_to_size > 2000:
            raise ValueError(f"scale_to_size must be <= 2,000 characters. ({scale_to_size} > 2000)")

        self.scale_to_size = scale_to_size - len(suffix)
        self.max_lines = max_lines
        self._current_page = [prefix]
        self._linecount = 0
        self._count = len(prefix) + 1  # prefix + newline
        self._pages = []

    def add_line(self, line: str = '', *, empty: bool = False) -> None:
        """
        Adds a line to the current page.

        If a line on a page exceeds `max_size` characters, then `max_size` will go up to
        `scale_to_size` for a single line before creating a new page for the overflow words. If it
        is still exceeded, the excess characters are stored and placed on the next pages unti
        there are none remaining (by word boundary). The line is truncated if `scale_to_size` is
        still exceeded after attempting to continue onto the next page.

        In the case that the page already contains one or more lines and the new lines would cause
        `max_size` to be exceeded, a new page is created. This is done in order to make a best
        effort to avoid breaking up single lines across pages, while keeping the total length of the
        page at a reasonable size.

        This function overrides the `Paginator.add_line` from inside `discord.ext.commands`.

        It overrides in order to allow us to configure the maximum number of lines per page.
        """
        remaining_words = None
        if len(line) > (max_chars := self.max_size - len(self.prefix) - 2):
            if len(line) > self.scale_to_size:
                line, remaining_words = self._split_remaining_words(line, max_chars)
                if len(line) > self.scale_to_size:
                    log.debug("Could not continue to next page, truncating line.")
                    line = line[:self.scale_to_size]

        # Check if we should start a new page or continue the line on the current one
        if self.max_lines is not None and self._linecount >= self.max_lines:
            log.debug("max_lines exceeded, creating new page.")
            self._new_page()
        elif self._count + len(line) + 1 > self.max_size and self._linecount > 0:
            log.debug("max_size exceeded on page with lines, creating new page.")
            self._new_page()

        self._linecount += 1

        self._count += len(line) + 1
        self._current_page.append(line)

        if empty:
            self._current_page.append('')
            self._count += 1

        # Start a new page if there were any overflow words
        if remaining_words:
            self._new_page()
            self.add_line(remaining_words)

    def _new_page(self) -> None:
        """
        Internal: start a new page for the paginator.

        This closes the current page and resets the counters for the new page's line count and
        character count.
        """
        self._linecount = 0
        self._count = len(self.prefix) + 1
        self.close_page()

    def _split_remaining_words(self, line: str, max_chars: int) -> t.Tuple[str, t.Optional[str]]:
        """
        Internal: split a line into two strings -- reduced_words and remaining_words.

        reduced_words: the remaining words in `line`, after attempting to remove all words that
            exceed `max_chars` (rounding down to the nearest word boundary).

        remaining_words: the words in `line` which exceed `max_chars`. This value is None if
            no words could be split from `line`.

        If there are any remaining_words, an ellipses is appended to reduced_words and a
        continuation header is inserted before remaining_words to visually communicate the line
        continuation.

        Return a tuple in the format (reduced_words, remaining_words).
        """
        reduced_words = []
        remaining_words = []

        # "(Continued)" is used on a line by itself to indicate the continuation of last page
        continuation_header = "(Continued)\n-----------\n"
        reduced_char_count = 0
        is_full = False

        for word in line.split(" "):
            if not is_full:
                if len(word) + reduced_char_count <= max_chars:
                    reduced_words.append(word)
                    reduced_char_count += len(word) + 1
                else:
                    # If reduced_words is empty, we were unable to split the words across pages
                    if not reduced_words:
                        return line, None
                    is_full = True
                    remaining_words.append(word)
            else:
                remaining_words.append(word)

        return (
            " ".join(reduced_words) + "..." if remaining_words else "",
            continuation_header + " ".join(remaining_words) if remaining_words else None
        )

    @classmethod
    async def paginate(
        cls,
        lines: t.List[str],
        ctx: Context,
        embed: discord.Embed,
        prefix: str = "",
        suffix: str = "",
        max_lines: t.Optional[int] = None,
        max_size: int = 500,
        scale_to_size: int = 2000,
        empty: bool = True,
        restrict_to_user: User = None,
        timeout: int = 300,
        footer_text: str = None,
        url: str = None,
        exception_on_empty_embed: bool = False,
    ) -> t.Optional[discord.Message]:
        """
        Use a paginator and set of reactions to provide pagination over a set of lines.

        The reactions are used to switch page, or to finish with pagination.

        When used, this will send a message using `ctx.send()` and apply a set of reactions to it. These reactions may
        be used to change page, or to remove pagination from the message.

        Pagination will also be removed automatically if no reaction is added for five minutes (300 seconds).

        Example:
        >>> embed = discord.Embed()
        >>> embed.set_author(name="Some Operation", url=url, icon_url=icon)
        >>> await LinePaginator.paginate([line for line in lines], ctx, embed)
        """
        def event_check(reaction_: discord.Reaction, user_: discord.Member) -> bool:
            """Make sure that this reaction is what we want to operate on."""
            no_restrictions = (
                # Pagination is not restricted
                not restrict_to_user
                # The reaction was by a whitelisted user
                or user_.id == restrict_to_user.id
            )

            return (
                # Conditions for a successful pagination:
                all((
                    # Reaction is on this message
                    reaction_.message.id == message.id,
                    # Reaction is one of the pagination emotes
                    str(reaction_.emoji) in PAGINATION_EMOJI,
                    # Reaction was not made by the Bot
                    user_.id != ctx.bot.user.id,
                    # There were no restrictions
                    no_restrictions
                ))
            )

        paginator = cls(prefix=prefix, suffix=suffix, max_size=max_size, max_lines=max_lines,
                        scale_to_size=scale_to_size)
        current_page = 0

        if not lines:
            if exception_on_empty_embed:
                log.exception("Pagination asked for empty lines iterable")
                raise EmptyPaginatorEmbed("No lines to paginate")

            log.debug("No lines to add to paginator, adding '(nothing to display)' message")
            lines.append("(nothing to display)")

        for line in lines:
            try:
                paginator.add_line(line, empty=empty)
            except Exception:
                log.exception(f"Failed to add line to paginator: '{line}'")
                raise  # Should propagate
            else:
                log.trace(f"Added line to paginator: '{line}'")

        log.debug(f"Paginator created with {len(paginator.pages)} pages")

        embed.description = paginator.pages[current_page]

        if len(paginator.pages) <= 1:
            if footer_text:
                embed.set_footer(text=footer_text)
                log.trace(f"Setting embed footer to '{footer_text}'")

            if url:
                embed.url = url
                log.trace(f"Setting embed url to '{url}'")

            log.debug("There's less than two pages, so we won't paginate - sending single page on its own")
            return await ctx.send(embed=embed)
        else:
            if footer_text:
                embed.set_footer(text=f"{footer_text} (Page {current_page + 1}/{len(paginator.pages)})")
            else:
                embed.set_footer(text=f"Page {current_page + 1}/{len(paginator.pages)}")
            log.trace(f"Setting embed footer to '{embed.footer.text}'")

            if url:
                embed.url = url
                log.trace(f"Setting embed url to '{url}'")

            log.debug("Sending first page to channel...")
            message = await ctx.send(embed=embed)

        log.debug("Adding emoji reactions to message...")

        for emoji in PAGINATION_EMOJI:
            # Add all the applicable emoji to the message
            log.trace(f"Adding reaction: {repr(emoji)}")
            await message.add_reaction(emoji)

        while True:
            try:
                reaction, user = await ctx.bot.wait_for("reaction_add", timeout=timeout, check=event_check)
                log.trace(f"Got reaction: {reaction}")
            except asyncio.TimeoutError:
                log.debug("Timed out waiting for a reaction")
                break  # We're done, no reactions for the last 5 minutes

            if str(reaction.emoji) == DELETE_EMOJI:
                log.debug("Got delete reaction")
                return await message.delete()

            if reaction.emoji == FIRST_EMOJI:
                await message.remove_reaction(reaction.emoji, user)
                current_page = 0

                log.debug(f"Got first page reaction - changing to page 1/{len(paginator.pages)}")

                embed.description = paginator.pages[current_page]
                if footer_text:
                    embed.set_footer(text=f"{footer_text} (Page {current_page + 1}/{len(paginator.pages)})")
                else:
                    embed.set_footer(text=f"Page {current_page + 1}/{len(paginator.pages)}")
                await message.edit(embed=embed)

            if reaction.emoji == LAST_EMOJI:
                await message.remove_reaction(reaction.emoji, user)
                current_page = len(paginator.pages) - 1

                log.debug(f"Got last page reaction - changing to page {current_page + 1}/{len(paginator.pages)}")

                embed.description = paginator.pages[current_page]
                if footer_text:
                    embed.set_footer(text=f"{footer_text} (Page {current_page + 1}/{len(paginator.pages)})")
                else:
                    embed.set_footer(text=f"Page {current_page + 1}/{len(paginator.pages)}")
                await message.edit(embed=embed)

            if reaction.emoji == LEFT_EMOJI:
                await message.remove_reaction(reaction.emoji, user)

                if current_page <= 0:
                    log.debug("Got previous page reaction, but we're on the first page - ignoring")
                    continue

                current_page -= 1
                log.debug(f"Got previous page reaction - changing to page {current_page + 1}/{len(paginator.pages)}")

                embed.description = paginator.pages[current_page]

                if footer_text:
                    embed.set_footer(text=f"{footer_text} (Page {current_page + 1}/{len(paginator.pages)})")
                else:
                    embed.set_footer(text=f"Page {current_page + 1}/{len(paginator.pages)}")

                await message.edit(embed=embed)

            if reaction.emoji == RIGHT_EMOJI:
                await message.remove_reaction(reaction.emoji, user)

                if current_page >= len(paginator.pages) - 1:
                    log.debug("Got next page reaction, but we're on the last page - ignoring")
                    continue

                current_page += 1
                log.debug(f"Got next page reaction - changing to page {current_page + 1}/{len(paginator.pages)}")

                embed.description = paginator.pages[current_page]

                if footer_text:
                    embed.set_footer(text=f"{footer_text} (Page {current_page + 1}/{len(paginator.pages)})")
                else:
                    embed.set_footer(text=f"Page {current_page + 1}/{len(paginator.pages)}")

                await message.edit(embed=embed)

        log.debug("Ending pagination and clearing reactions.")
        with suppress(discord.NotFound):
            await message.clear_reactions()


class BasePaginator(abc.ABC):
    """
    Message pagination session state and logic for user pagination sessions.

    Attributes
    ------------------
    public: bool
        If the paginator is able to be controlled by any user or just by the controller.
        Mods+ will always be able to control paginators.
    timeout: int
        Seconds after last interaction when the pagination session will stop automatically.
    delete_on_timeout:
        If the paginator should delete the message on timeout.
    current_page: int
        The page index this session should be currently on based on user interactions
    message: discord.Message
        A reference to the Message that we're paginating content on.
    remove_user_reactions: bool
        A class level attribute that defines if the paginator will remove user reactions after handling them.
        If set to False, the paginator will act on both adding and removing a reaction to avoid needing to double-click.
    use_skip_nav: bool
        A class level attribute that defines if the "first" and "last" nav reactions are to be used or always disabled.
        When True, default behaviour is to only add them when 3 or more pages are being paginated.

    _pages:
        The collection of pages generated after processing content.
    _rendered_page:
        The page index this session last actually applied to the Discord message and should be what
        the user is seeing.
    _started:
        If the pagination session has started and if future added handlers should be applied immediately.
    _ended:
        If the pagination session has been marked to end.
    _to_delete:
        If the pagination message has been marked for deletion.
    _reactions:
        A dict of all registered reactions to be added to the pagination session and the callables
        to be run when used.
    _events:
        A defaultdict of all event listeners registered by this paginator.
    _bot:
        A reference to our Bot instance.
    _user_id:
        The User ID of the main pagination controller, for limiting pagination actions.
    _channel:
        A reference to the Channel that the message should be sent to if it doesn't exist.
    _loop:
        A reference to the asyncio event loop, used for it's internal monotonic time.
    _timeout_due:
        Time reference to when the pagination session ends by lack of activity.
    _timeout_task:
        Task object for the running timeout task.
    """

    remove_user_reactions: bool = True
    use_skip_nav: bool = True

    _nav_reactions: t.Dict[str, t.Union[discord.Emoji, str]] = {
        "first": FIRST_EMOJI,
        "back": LEFT_EMOJI,
        "next": RIGHT_EMOJI,
        "last": LAST_EMOJI,
    }

    def __init__(self, *, public: bool = False, timeout: int = 300, delete_on_timeout: bool = False):
        # Settings
        self.public: bool = public
        self.timeout: int = timeout
        self.delete_on_timeout: bool = delete_on_timeout

        # Session state
        self.current_page: int = 1
        self.message: t.Optional[discord.Message] = None

        # Private state
        self._pages: t.List = []
        self._rendered_page: t.Optional[int] = None
        self._started: bool = False
        self._ended: bool = False
        self._to_delete: bool = False
        self._events: t.DefaultDict[str, t.List[t.Callable[[...], t.Coroutine]]] = defaultdict(list)
        self._reactions: t.Dict[t.Union[discord.Emoji, str], t.Callable] = {}

        for action, reaction in self._nav_reactions.items():
            self.add_reaction(reaction, getattr(self, f"_{action}"))

        self.add_reaction(constants.Emojis.trashcan, self._delete)

        # Private references
        self._bot: t.Optional[Bot] = None
        self._user_id: t.Optional[int] = None
        self._channel: t.Optional[discord.TextChannel] = None
        self._loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self._timeout_due: float = self._loop.time()
        self._timeout_task: t.Optional[asyncio.Task] = None
        self._content_has_changed: bool = False

    # region: Session status

    @property
    def page_count(self) -> int:
        """Number of pages currently generated."""
        return len(self._pages)

    @property
    def is_dirty(self) -> bool:
        """Returns True if the current page doesn't match the page shown to the user in Discord."""
        return self._rendered_page != self.current_page

    @property
    def timeout_remaining(self) -> float:
        return self._timeout_due - self._loop.time()

    # endregion

    # region: Session actions

    async def start(
        self,
        ctx: t.Optional[Context] = None,
        *,
        bot: Bot = None,
        user: t.Union[discord.Member, discord.User, None] = None,
        channel: t.Optional[discord.TextChannel] = None,
    ) -> None:
        """
        Start the pagination session.

        Any `user` or `channel` arguments given explicitly will always be used instead of the values on `ctx`.
        This would allow pagination to occur in another channel, for example.

        `bot` and `channel` become required arguments if `ctx` is not provided.
        `user` becomes a required argument if `ctx` is not provided and `public` is `False`.

        Supporting sessions without `ctx` ensures we can make use of paginators when it isn't available,
        such as from an event handler or on bot start.
        """
        try:
            self._bot = bot or ctx.bot
            self._channel = channel or ctx.channel
        except AttributeError:
            raise ValueError("`bot`, `channel` are required arguments if `ctx` is not provided.")

        if user:
            self._user_id = user.id
        elif ctx:
            self._user_id = ctx.author.id
        else:
            if self.public is False:
                raise ValueError("`user` is required if `ctx` is not provided and `public` is False.")
            self._user_id = None

        self.add_event("on_reaction_add", self._handle_reaction)
        if not self.remove_user_reactions:
            self.add_event("on_reaction_remove", self._handle_reaction)
        self.add_event("on_message_delete", self._handle_delete)

        await self.update()

    async def stop(self, *, delete: t.Optional[bool] = False) -> None:
        """Stops the pagination session by stopping event handling and deleting/clearing the message."""
        self._ended = True
        self.clear_events()
        self._timeout_task.cancel()

        if not self.message:
            return

        with suppress(discord.NotFound):
            if self._to_delete or delete:
                await self.message.delete()
            else:
                await self.message.clear_reactions()

    async def update(self) -> None:
        """Updates the pagination message contents if the page changed."""
        if self.is_dirty:
            await self._refresh_message()

    def clear_reactions(self) -> asyncio.Task:
        """Removes all reactions from the paginator."""
        return asyncio.create_task(await self.message.clear_reactions())

    def clear_events(self) -> None:
        """Removes all handled paginator events from the Discord client."""
        for event, handlers in self._events.items():
            for handler in handlers:
                self._bot.remove_listener(handler, event)

        self._events.clear()

    # endregion

    # region: Default event handlers

    async def _handle_reaction(self, reaction: discord.Reaction, user: t.Union[discord.Member, discord.User]) -> None:
        """Process reaction add and remove events from Discord."""
        # filter out reaction events on other messages
        if reaction.message.id != self.message.id:
            return

        # ignore bot reactions (including self-reactions on pagination start)
        if user.bot:
            return

        # restrict to controlling user or mods+ if not a public session
        if not self.public:
            if not self._is_controller(user) and not self._is_mod(user):
                asyncio.create_task(self.message.remove_reaction(reaction.emoji, user))
                return

        # get the handler callable
        func = self._reactions.get(str(reaction.emoji))

        # ignore emojis that aren't watched by pagination session
        if not func:
            return

        # call the handler, passing an instance of the paginator if the handler is not bound to this object
        if getattr(func, "__self__", None) == self:
            func()
        else:
            func(self)

        # stop the pagination session if a handler set the ended flag
        if self._ended:
            await self.stop()
            return

        # update the pagination message to reflect any page changes
        await self.update()

        # remove the reaction we just handled if configured to do so
        if self.remove_user_reactions:
            asyncio.create_task(self.message.remove_reaction(reaction.emoji, user))

    async def _handle_delete(self, _message: discord.Message) -> None:
        """Process message_delete events from Discord."""
        self._to_delete = True
        self._ended = True

    @staticmethod
    def _is_mod(user: t.Union[discord.Member, discord.User]) -> bool:
        """Helper to check if the given user is a moderator."""
        try:
            roles = user.roles
        except AttributeError:
            return False

        for role in roles:
            if role.id in constants.MODERATION_ROLES:
                return True

        return False

    def _is_controller(self, user: t.Union[discord.Member, discord.User]) -> bool:
        """Helper to check if the given user is the pagination controller."""
        return user.id == self._user_id

    # endregion

    # region: Handler registration

    def add_reaction(self, reaction: t.Union[discord.Emoji, str], handler: t.Callable) -> t.Optional[asyncio.Task]:
        """Add a reaction and it's handler function to the paginator."""
        self._reactions[reaction] = handler
        if self._started:
            return asyncio.create_task(self.message.add_reaction(reaction))

    def remove_reaction(self, reaction: t.Union[discord.Emoji, str]) -> t.Optional[asyncio.Task]:
        """Remove a reaction and it's handler."""
        del self._reactions[reaction]
        if self._started:
            return asyncio.create_task(self.message.clear_reaction(reaction))

    def add_event(self, event: str, handler: t.Callable[[...], t.Coroutine]) -> None:
        """Start listening to an event with the provided handler coroutine."""
        # allow both event name forms, e.g. "message_delete"/"on_message_delete"
        if not event.startswith("on_"):
            event = f"on_{event}"

        self._events[event].append(handler)
        if self._started:
            self._bot.add_listener(handler, event)

    def remove_event(self, event: str, handler: t.Callable[[...], t.Coroutine] = None) -> None:
        """Stop listening to a handled event."""
        # allow both event name forms, e.g. "message_delete"/"on_message_delete"
        if not event.startswith("on_"):
            event = f"on_{event}"

        if handler:
            with suppress(ValueError):
                self._events[event].remove(handler)
            return

        handlers = self._events[event]
        for handler in handlers:
            self._bot.remove_listener(handler, event)
        del self._events[event]

    # endregion

    # region: Default reaction handlers

    def _next(self) -> None:
        """Go to next page in paginator."""
        if self.current_page >= self.page_count:
            return
        self.current_page += 1

    def _back(self) -> None:
        """Go to previous page in paginator."""
        if self.current_page <= 1:
            return
        self.current_page -= 1

    def _first(self) -> None:
        """Go to first page in paginator."""
        self.current_page = 1

    def _last(self) -> None:
        """Go to last page in paginator."""
        self.current_page = self.page_count

    def _delete(self: BasePaginator) -> None:
        """Mark the paginator to be ended."""
        self._ended = True
        self._to_delete = True

    # endregion

    # region: Internal pagination logic

    async def _refresh_message(self) -> None:
        """Edits or creates the pagination message with the current page contents."""
        if self._content_has_changed:
            self._generate_pages()

        content, embed = self._format_page()

        # initialise session if no message exists
        if not self.message:
            self.message = await self._channel.send(content, embed=embed)
            self._started = True
            self._add_events()
            asyncio.create_task(self._add_reactions())

        else:
            await self.message.edit(content=content, embed=embed)

        self._rendered_page = self.current_page

        # update timeout status
        self._timeout_due = self._loop.time() + self.timeout

        # start timeout task if it doesn't exist
        if not self._timeout_task:
            self._timeout_task = asyncio.create_task(self._do_timeout())

    async def _add_reactions(self) -> None:
        """
        Iterate over reactions to add them to message in correct order.

        Used internally when the pagination session begins.
        """
        skip_navs = [self._nav_reactions["first"], self._nav_reactions["last"]]
        all_navs = [*skip_navs, ]

        for reaction in self._reactions:
            if reaction in skip_navs:
                if not self.use_skip_nav or self.page_count < 3:
                    continue

            await self.message.add_reaction(reaction)

    def _add_events(self) -> None:
        """
        Iterate over registered handled events and register them on the Discord client.

        Used internally when the pagination session begins.
        """
        for event, handlers in self._events.items():
            for handler in handlers:
                self._bot.add_listener(handler, event)

    async def _do_timeout(self) -> None:
        """Checks most recent activity and either reschedules timeout or closes the paginator."""
        await asyncio.sleep(self.timeout_remaining)

        # reschedule if there's been a more recent interaction
        if self.timeout_remaining > 0:
            self._timeout_task = asyncio.create_task(self._do_timeout())
            return

        self._to_delete = self.delete_on_timeout
        await self.stop()

    # endregion

    # region: Abstract methods to be defined in subclasses

    @abc.abstractmethod
    def _generate_pages(self) -> None:
        """Generates page groups from the content to paginate over."""
        pass

    @abc.abstractmethod
    def _format_page(self) -> t.Tuple[t.Optional[str], t.Optional[discord.Embed]]:
        """Formats the page contents and returns the resulting message content and embed."""
        pass

    # endregion


class LinePaginatorNeu(BasePaginator):
    """
    Paginator session based on lines of text for content, not using embeds.

    Attributes
    -----------
    header: str
        Static content to display at the top of all pages.
    footer: str
        Static content to display at the bottom of all pages.
    line_spacing: bool
        If an extra blank line should be inserted between lines.
    max_lines: int
        Maximum number of lines will be shown on each page.
    max_line_chars: int
        Maximum characters allowed to fit on one line before splitting to a new one.
    max_page_chars: int
        Maximum characters allowed to fit on the one page before forcing it into a new page.
    """

    def __init__(
        self,
        *,
        header: t.Optional[str] = None,
        footer: t.Optional[str] = None,
        line_spacing: bool = False,
        max_lines: int = 15,
        max_line_chars: int = 2000,
        max_page_chars: int = 2000,
        public: bool = False,
        timeout: int = 300,
        delete_on_timeout: bool = False,
    ) -> None:
        super().__init__(public=public, timeout=timeout, delete_on_timeout=delete_on_timeout)
        self.header: t.Optional[str] = header
        self.footer: t.Optional[str] = footer if footer is not None else "{page_status}"
        self.line_spacing: bool = line_spacing
        self.max_lines: int = max_lines
        self.max_line_chars: int = max_line_chars
        self.max_page_chars: int = max_page_chars - (len(header) if header else 0) - (len(footer) if footer else 0)

        self._lines: t.List[str] = []

        # long lines wrap naturally in discord, and to better control vertical fluctuation,
        # we will count every x characters in a line as a "virtual line", contributing to max_lines
        # but without literally splitting the content, messing with formatting.
        self._virtual_line_splitting: int = 230

    def add_line(self, content: str) -> None:
        """Add a line of content to the paginator."""
        new_lines = textwrap.wrap(content, self.max_line_chars)
        self._lines.extend(new_lines)
        if self.line_spacing:
            self._lines.append("")

        self._content_has_changed = True

    def _generate_pages(self) -> None:
        """Splits the collection of registered fields into pages based on max per page allowed."""
        line_counter = 0
        character_counter = 0
        current_lines = []
        pages = []

        for line in self._lines:
            # check if we hit the line limit for a page, or if we're about to go over page char limit
            if line_counter >= self.max_lines or (len(line) + character_counter) >= self.max_page_chars:
                pages.append(current_lines)
                current_lines = []
                line_counter = 0
                character_counter = 0

            current_lines.append(line)

            line_counter += len(textwrap.wrap(line, self._virtual_line_splitting))
            character_counter += len(line)

        pages.append(current_lines)
        self._pages = pages

    def _format_page(self) -> t.Tuple[str, None]:
        """
        Formats the embed and the fields for current page.

        Footer will attempt to resolve any `{page_status}` format tags for "Page ?/?" placement.
        """
        contents = []

        if self.header:
            contents.extend([self.header, ""])

        contents.extend(self._pages[self.current_page-1])

        if self.footer:
            contents.extend(["", self.footer.format(page_status=f"Page {self.current_page}/{self.page_count}")])

        return "\n".join(contents), None


class EmbedLinePaginator(LinePaginatorNeu):
    """
    Paginator session based on lines of content using embeds to display pages.

    Attributes
    -----------
    title: str
        The title to be displayed in the embed.
    footer: str
        The footer to be displayed in the embed.
        Use "{page_status}" in your string to insert the "Page ?/?" section on render.
        If using f-strings, use double curly brackets to avoid early formatting, e.g. f"{user} - {{page_status}}"
    non_embed_text: str
        The text to be sent in the plain message content before the embed.
        Suitable for notification mentions and content to be copied easily on mobile clients.
    max_lines: int
        Maximum number of lines will be shown on each page.
    max_line_chars: int
        Maximum characters allowed to fit on one line before splitting to a new one.
    max_page_chars: int
        Maximum characters allowed to fit on the one page before forcing it into a new page.
    """

    def __init__(
        self,
        *,
        title: t.Optional[str] = None,
        footer: t.Optional[str] = None,
        author: t.Optional[str] = None,
        icon: t.Optional[str] = None,
        non_embed_text: t.Optional[str] = None,
        line_spacing: bool = False,
        max_lines: int = 15,
        max_line_chars: int = 2000,
        max_page_chars: int = 2000,
        public: bool = False,
        timeout: int = 300,
        delete_on_timeout: bool = False,
        embed: discord.Embed = None,
    ) -> None:
        super().__init__(
            header=title,
            footer=footer,
            line_spacing=line_spacing,
            max_lines=max_lines,
            max_line_chars=max_line_chars,
            public=public,
            timeout=timeout,
            delete_on_timeout=delete_on_timeout
        )
        self.max_page_chars = max_page_chars
        self.author_text = author
        self.icon_url = icon
        self.non_embed_text = non_embed_text
        self.embed = embed or discord.Embed()

    def _format_page(self) -> t.Tuple[t.Optional[str], discord.Embed]:
        """
        Formats the embed and the fields for current page.

        Footer will attempt to resolve any `{page_status}` format tags for "Page ?/?" placement.
        """
        if self.header:
            self.embed.title = self.header
        if self.footer:
            self.embed.set_footer(text=self.footer.format(page_status=f"Page {self.current_page}/{self.page_count}"))

        self.embed.description = "\n".join(self._pages[self.current_page-1])

        return self.non_embed_text, self.embed


class EmbedFieldPaginator(BasePaginator):
    """
    Embed paginator session based on embed fields for content.

    Attributes
    -----------
    title: str
        The title to be displayed in the embed.
    description: str
        The description to be displayed in the embed.
    footer: str
        The footer to be displayed in the embed.
        Use "{page_status}" in your string to insert the "Page ?/?" section on render.
        If using f-strings, use double curly brackets to avoid early formatting, e.g. f"{user} - {{page_status}}"
    non_embed_text: str
        The text to be sent in the plain message content before the embed.
        Suitable for notification mentions and content to be copied easily on mobile clients.
    max_fields: int
        How many fields will be shown on each page.
    max_chars: int
        How many characters maximum for each field to show.
    """

    def __init__(
        self,
        *,
        title: t.Optional[str] = None,
        description: t.Optional[str] = None,
        footer: t.Optional[str] = None,
        non_embed_text: str = None,
        max_fields: int = 10,
        max_chars: int = 1000,
        public: bool = False,
        timeout: int = 300,
        delete_on_timeout: bool = False,
        embed: discord.Embed = None,
    ):
        super().__init__(public=public, timeout=timeout, delete_on_timeout=delete_on_timeout)

        self._pages: t.List[t.List[t.Tuple[str, str, bool]]]

        self.title = title
        self.description = description
        self.footer = footer or "{page_status}"
        self.non_embed_text = non_embed_text
        self.max_fields = max_fields
        self.max_chars = max_chars
        self.embed = embed or discord.Embed()
        self._fields: t.List[t.Tuple[str, str, bool]] = []

    @property
    def field_count(self) -> int:
        """Number of fields currently registered."""
        return len(self._fields)

    def add_field(self, name: str, value: str, *, index: t.Optional[int] = None, inline: bool = False) -> None:
        """Add fields to the collection to be split across multiple pages based on max per page allowed."""
        value = value[:self.max_chars-1] + ".." if len(value) > self.max_chars else value
        if index:
            self._fields.insert(index, (name, value, inline))
        else:
            self._fields.append((name, value, inline))
        self._content_has_changed = True

    def clear_fields(self) -> None:
        """Remove all fields from the paginator."""
        self._fields.clear()
        self._content_has_changed = True

    def _generate_pages(self) -> None:
        """Splits the collection of registered fields into pages based on max per page allowed."""
        self._pages = list(chunked(self._fields, self.max_fields))

    def _format_page(self) -> t.Tuple[t.Optional[str], discord.Embed]:
        """
        Formats the embed and the fields for current page.

        Footer will attempt to resolve any `{page_status}` format tags for "Page ?/?" placement.
        """
        if self.title:
            self.embed.title = self.title
        if self.description:
            self.embed.description = self.description

        self.embed.set_footer(text=self.footer.format(page_status=f"Page {self.current_page}/{self.page_count}"))

        self.embed.clear_fields()
        for name, value, inline in self._pages[self.current_page-1]:
            value = value[:self.max_chars-1] + ".." if len(value) > self.max_chars else value
            self.embed.add_field(name=name, value=value, inline=inline)

        return self.non_embed_text, self.embed
