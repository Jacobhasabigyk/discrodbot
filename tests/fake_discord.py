"""
A tiny stand-in for discord.py, just enough to run the ticket cog's logic
offline (no Discord connection). It records what the bot sends/edits.
Install with install() BEFORE importing cogs.
"""
import sys
import types
import itertools
import datetime

_ids = itertools.count(1000)


class HTTPException(Exception):
    pass


class Forbidden(HTTPException):
    pass


class LoginFailure(Exception):
    pass


class Embed:
    def __init__(self, title=None, description=None, color=None, colour=None):
        self.title, self.description, self.color = title, description, color or colour
        self.fields, self.footer = [], None

    def add_field(self, name, value, inline=True):
        self.fields.append(types.SimpleNamespace(name=name, value=value, inline=inline))
        return self

    def insert_field_at(self, index, name, value, inline=True):
        self.fields.insert(index, types.SimpleNamespace(name=name, value=value, inline=inline))
        return self

    def set_footer(self, text=None, **kw):
        self.footer = text
        return self


class Enum:
    def __init__(self, **kw):
        self.__dict__.update(kw)


ButtonStyle = Enum(primary=1, secondary=2, success=3, danger=4, link=5, red=4, green=3, blurple=1, grey=2)
TextStyle = Enum(short=1, paragraph=2)
ChannelType = Enum(private_thread=12, public_thread=11, text=0)
InteractionType = Enum(component=3, modal_submit=5, application_command=2)


class PermissionOverwrite:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, item):
        return None

    def is_empty(self):
        return not self.__dict__


class AllowedMentions:
    def __init__(self, **kw):
        pass

    @classmethod
    def none(cls):
        return cls()


class File:
    def __init__(self, fp, filename=None):
        self.data = fp.read()
        self.filename = filename


# ---------------- ui ----------------
class Item:
    pass


class Button(Item):
    def __init__(self, label=None, emoji=None, style=None, custom_id=None, url=None, **kw):
        self.label, self.emoji, self.style, self.custom_id, self.url = label, emoji, style, custom_id, url


class TextInput(Item):
    def __init__(self, label=None, required=True, style=None, placeholder=None, max_length=None, **kw):
        self.label, self.required, self.value = label, required, ""


class UserSelect(Item):
    def __init__(self, **kw):
        self.values = []


class View:
    def __init__(self, timeout=180):
        self.children = []
        for name in dir(type(self)):
            attr = getattr(type(self), name, None)
            if getattr(attr, "__select__", None):
                self.children.append(attr)

    def add_item(self, item):
        self.children.append(item)
        return self


def select(cls=None, **kw):
    def deco(fn):
        fn.__select__ = True
        return fn
    return deco


class Modal:
    def __init_subclass__(cls, title=None, **kw):
        cls._title = title

    def __init__(self, title=None, timeout=None):
        self.title = title or getattr(self, "_title", None)
        self.children = []
        for name in dir(type(self)):
            attr = getattr(type(self), name, None)
            if isinstance(attr, TextInput):
                fresh = TextInput(label=attr.label)
                setattr(self, name, fresh)
                self.children.append(fresh)

    def add_item(self, item):
        self.children.append(item)


ui = types.SimpleNamespace(View=View, Button=Button, TextInput=TextInput, UserSelect=UserSelect, Modal=Modal, select=select, Item=Item)


# ---------------- models ----------------
class Role:
    def __init__(self, id, name="role"):
        self.id, self.name = id, name
        self.mention = f"<@&{id}>"


class Perms:
    def __init__(self, admin=False, **overrides):
        self.administrator = admin
        self.__dict__.update(overrides)

    def __getattr__(self, item):
        return True


class User:
    def __init__(self, id, name="user", bot=False):
        self.id, self.name, self.display_name, self.bot = id, name, name, bot
        self.mention = f"<@{id}>"
        self.dms = []
        self.roles = []

    async def send(self, content=None, embed=None, view=None, file=None, **kw):
        if getattr(self, "dm_closed", False):
            raise Forbidden("dm closed")
        self.dms.append(types.SimpleNamespace(content=content, embed=embed, view=view, file=file))


class Member(User):
    def __init__(self, id, name, guild, roles=(), admin=False):
        super().__init__(id, name)
        self.guild = guild
        self.roles = list(roles)
        self.guild_permissions = Perms(admin)

    async def add_roles(self, *roles, reason=None):
        self.roles.extend(roles)


class Message:
    def __init__(self, channel, author, content=None, embed=None, view=None, file=None):
        self.id = next(_ids)
        self.channel, self.author = channel, author
        self.content, self.clean_content = content, content or ""
        self.embeds = [embed] if embed else []
        self.view, self.file = view, file
        self.components = [1] if view and getattr(view, "children", None) else []
        self.attachments = []
        self.created_at = datetime.datetime.now(datetime.timezone.utc)
        self.deleted = False
        self.pinned = False
        self.jump_url = f"https://discord.com/channels/x/{channel.id}/{self.id}"
        self.interaction_metadata = None

    async def edit(self, content=None, embed=None, view="keep", **kw):
        if embed is not None:
            self.embeds = [embed]
        if view != "keep":
            self.view = view
        if content is not None:
            self.content = content

    async def delete(self):
        self.deleted = True

    async def pin(self):
        self.pinned = True


class PartialMessage:
    def __init__(self, channel, id):
        self.channel, self.id = channel, id

    def _real(self):
        for m in self.channel.messages:
            if m.id == self.id:
                return m
        raise HTTPException("unknown message")

    async def edit(self, **kw):
        await self._real().edit(**kw)

    async def delete(self):
        await self._real().delete()


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class TextChannel:
    def __init__(self, guild, id, name, category=None):
        self.guild, self.id, self.name, self.category = guild, id, name, category
        self.messages = []
        self.mention = f"<#{id}>"
        self.jump_url = f"https://discord.com/channels/{guild.id}/{id}"
        self.threads = []

    async def send(self, content=None, embed=None, view=None, file=None, **kw):
        m = Message(self, self.guild.me, content, embed, view, file)
        self.messages.append(m)
        return m

    def get_partial_message(self, id):
        return PartialMessage(self, id)

    async def fetch_message(self, id):
        return PartialMessage(self, id)._real()

    async def history(self, limit=100, oldest_first=False):
        items = [m for m in self.messages if not m.deleted]
        items = items if oldest_first else list(reversed(items))
        for m in items[:limit]:
            yield m

    def typing(self):
        return Typing()

    blocked_thread_send = set()

    def permissions_for(self, member):
        ow = getattr(self, "overwrites", {}).get(getattr(member, "id", None))
        if ow and ow.send_messages_in_threads:
            return Perms()
        if getattr(member, "id", None) in self.blocked_thread_send:
            return Perms(send_messages_in_threads=False)
        return Perms()

    def overwrites_for(self, member):
        return getattr(self, "overwrites", {}).get(member.id, PermissionOverwrite())

    async def set_permissions(self, target, overwrite=None, reason=None, **kw):
        if not hasattr(self, "overwrites"):
            self.overwrites = {}
        if overwrite is None:
            self.overwrites.pop(target.id, None)
        else:
            self.overwrites[target.id] = overwrite

    async def create_thread(self, name, type=None, invitable=True, auto_archive_duration=None, reason=None, **kw):
        t = Thread(self, next(_ids), name)
        self.threads.append(t)
        self.guild.client.channels[t.id] = t
        return t

    async def delete(self, reason=None):
        self.deleted = True


class Thread(TextChannel):
    def __init__(self, parent, id, name):
        super().__init__(parent.guild, id, name)
        self.parent = parent
        self.members = set()
        self.archived = self.locked = False

    async def add_user(self, user):
        self.members.add(user.id)

    async def remove_user(self, user):
        self.members.discard(user.id)

    async def edit(self, archived=None, locked=None, **kw):
        self.archived, self.locked = bool(archived), bool(locked)


class Guild:
    def __init__(self, client, id):
        self.client, self.id = client, id
        self.roles, self.members, self.text_channels = {}, {}, []
        self.owner_id = 1
        self.default_role = Role(id, "@everyone")
        self.me = None

    def get_role(self, id):
        return self.roles.get(id)

    def get_member(self, id):
        return self.members.get(int(id))

    def get_channel(self, id):
        return self.client.channels.get(int(id))

    async def create_text_channel(self, name, category=None, overwrites=None, topic=None, **kw):
        ch = TextChannel(self, next(_ids), name, category)
        self.text_channels.append(ch)
        self.client.channels[ch.id] = ch
        return ch


class Response:
    def __init__(self, interaction):
        self.i = interaction
        self.done = False

    def is_done(self):
        return self.done

    async def send_message(self, content=None, embed=None, view=None, ephemeral=False, **kw):
        self.done = True
        self.i.sent.append(types.SimpleNamespace(content=content, view=view, ephemeral=ephemeral, embed=embed))

    async def send_modal(self, modal):
        self.done = True
        self.i.modal = modal

    async def defer(self, ephemeral=False, thinking=False):
        self.done = True

    async def edit_message(self, content=None, embed=None, view=None, **kw):
        self.done = True
        self.i.edited = types.SimpleNamespace(content=content, view=view)


class Followup:
    def __init__(self, interaction):
        self.i = interaction

    async def send(self, content=None, ephemeral=False, **kw):
        self.i.sent.append(types.SimpleNamespace(content=content, ephemeral=ephemeral))


class Interaction:
    def __init__(self, user, guild=None, channel=None, custom_id=None, type=3, message=None):
        self.user, self.guild, self.channel = user, guild, channel
        self.type = type
        self.data = {"custom_id": custom_id} if custom_id else {}
        self.sent, self.modal, self.edited = [], None, None
        self.response = Response(self)
        self.followup = Followup(self)
        self.message = message


# ---------------- ext ----------------
class _Listener:
    @staticmethod
    def listener(name=None):
        def deco(fn):
            return fn
        return deco


class Cog(_Listener):
    pass


class Bot:
    pass


def _loop(**kw):
    def deco(fn):
        class Loop:
            def __init__(self):
                self.fn = fn

            def __get__(self, obj, objtype=None):
                loop = self
                class Bound:
                    def start(self_inner):
                        pass

                    def cancel(self_inner):
                        pass

                    async def __call__(self_inner):
                        return await fn(obj)
                return Bound()

            def before_loop(self, f):
                return f
        return Loop()
    return deco


class Choice:
    def __init__(self, name, value):
        self.name, self.value = name, value

    def __class_getitem__(cls, item):
        return cls


class Group:
    def __init__(self, name=None, description=None):
        self.name = name

    def command(self, **kw):
        def deco(fn):
            fn.autocomplete = lambda name: (lambda f: f)
            return fn
        return deco


def _passthrough(*a, **kw):
    def deco(fn):
        return fn
    return deco


def install():
    discord = types.ModuleType("discord")
    for name, value in dict(
        HTTPException=HTTPException, Forbidden=Forbidden, LoginFailure=LoginFailure, Embed=Embed, ButtonStyle=ButtonStyle,
        TextStyle=TextStyle, ChannelType=ChannelType, InteractionType=InteractionType, PermissionOverwrite=PermissionOverwrite,
        AllowedMentions=AllowedMentions, File=File, ui=ui, Member=Member, User=User, Thread=Thread, TextChannel=TextChannel,
        Message=Message, Interaction=Interaction, Guild=Guild, Role=Role,
        utils=types.SimpleNamespace(get=lambda items, **kw: next((i for i in items if all(getattr(i, k) == v for k, v in kw.items())), None)),
    ).items():
        setattr(discord, name, value)

    commands = types.ModuleType("discord.ext.commands")
    commands.Cog, commands.Bot = Cog, Bot
    tasks = types.ModuleType("discord.ext.tasks")
    tasks.loop = _loop
    ext = types.ModuleType("discord.ext")
    ext.commands, ext.tasks = commands, tasks
    app_commands = types.ModuleType("discord.app_commands")
    app_commands.Group, app_commands.Choice = Group, Choice
    app_commands.command = _passthrough
    app_commands.choices = _passthrough
    app_commands.describe = _passthrough
    discord.app_commands, discord.ext = app_commands, ext

    sys.modules.update({
        "discord": discord, "discord.ext": ext, "discord.ext.commands": commands,
        "discord.ext.tasks": tasks, "discord.app_commands": app_commands,
    })
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientSession, aiohttp.ClientTimeout = object, (lambda **kw: None)
    sys.modules.setdefault("aiohttp", aiohttp)
