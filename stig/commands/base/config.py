# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details
# http://www.gnu.org/licenses/gpl-3.0.txt

import operator
import os
import subprocess
import textwrap
from collections import abc
from datetime import datetime

from . import _mixin as mixin
from .. import CmdError, InitCommand, utils
from ... import __appname__, __version__, objects
from ...client import ClientError
from ...completion import candidates
from ...settings import defaults, rcfile
from ...utils import cliparser, usertypes
from ._common import (make_COLUMNS_doc, make_SCRIPTING_doc, make_SORT_ORDERS_doc,
                      make_X_FILTER_spec)

from ...logging import make_logger  # isort:skip
log = make_logger(__name__)


class DumpCmdbase(mixin.get_rc_filepath,
                  metaclass=InitCommand):
    name = 'dump'
    aliases = ()
    category = 'configuration'
    provides = set()
    description = 'Generate commands that reproduce current settings, keybindings and tabs'
    usage = ('dump [<OPTIONS>] [<FILE>]',)
    examples = (f'dump rc.current    # Write $XDG_CONFIG_HOME{os.sep}.config/{{__appname__}}{os.sep}rc.current',
                f'dump ./rc.current  # Write rc.current in current working directory')
    argspecs = (
        {'names': ('FILE',), 'nargs': '?',
         'description': ('Path to rc file; if FILE does not exist and does not start with '
                         '"/", "./" or "~", "$XDG_CONFIG_HOME/.config/{__appname__}/" '
                         'is prepended')},
        { 'names': ('--force', '-f'), 'action': 'store_true',
          'description': 'Overwrite FILE if it exists' },
    )

    DUMP_WIDTH = 79

    def run(self, force, FILE):
        # Because it is a TUI-only command, calls to "bind" are ignored in CLI mode.
        with objects.cmdmgr.temporary_active_interface('tui'):
            objects.cmdmgr.run_ignored_calls_sync('bind')

        now = datetime.now()
        content = '\n'.join((
            f'# This is an rc file for {__appname__} {__version__}.',
            f'# This file was created on {now.isoformat(sep=" ", timespec="seconds")}.',
            '', '',
            '### SETTINGS',
            '',
            self._get_settings(),
            '', '',
            '### KEYBINDINGS',
            '',
            self._get_keybindings(),
        )) + '\n'
        if FILE:
            return self.dump_rc(content, force=force, path=self.get_rc_filepath(FILE))
        else:
            return self.dump_rc(content, force=force, path=None)

    def write_rc_file(self, content, path, force=False):
        if os.path.exists(path) and not force:
            raise CmdError('File exists: %s' % (path,))
        else:
            try:
                with open(path, 'w') as f:
                    f.write(content)
            except OSError as e:
                raise CmdError('Unable to write %s: %s' % (path, e))
            else:
                log.info('Wrote rc file: %s' % (path,))
                return True

    def _get_settings(self):
        lcfg = objects.localcfg
        settings = []
        for name in sorted(lcfg):
            value = lcfg[name]
            if isinstance(value, abc.Iterable) and not isinstance(value, str):
                value = ' '.join(value)
            else:
                value = str(value)
                if ' ' in value:
                    value = repr(value)
            escape_set_cmd = lcfg[name] == lcfg.default(name)
            lines = (self._wrap_setting_description(lcfg.description(name)) +
                     self._wrap_setting_default(lcfg.default(name)) +
                     self._wrap_set_cmd(name, value, escape=escape_set_cmd))
            settings.append('\n'.join(lines))
        return '\n\n'.join(settings)

    def _wrap_setting_description(self, string):
        return textwrap.wrap('# ' + str(string),
                             width=self.DUMP_WIDTH,
                             subsequent_indent='# ')

    def _wrap_setting_default(self, string):
        subseqind = '# ' + len('Default: ') * ' '
        prefix = '# Default: '
        lines = textwrap.wrap(prefix + str(string),
                              width=self.DUMP_WIDTH,
                              subsequent_indent=subseqind,
                              break_long_words=False,
                              break_on_hyphens=False)
        # First line must always contain the beginning of the default value, even if it is
        # too long
        if len(lines) >= 2 and lines[0].strip() == prefix.strip():
            lines[0] = lines[0] + lines[1][len(subseqind)-1:]
            del lines[1]
        return lines

    def _wrap_set_cmd(self, name, value, escape):
        cmd = 'set'
        lines = textwrap.wrap(' '.join((cmd, name, value)),
                              width=self.DUMP_WIDTH,
                              subsequent_indent=(len(cmd) + len(name) + 2) * ' ',
                              break_long_words=False,
                              break_on_hyphens=False)

        # First line must always contain command, name and beginning of value, even if it
        # is too long
        if len(lines) >= 3 and lines[0].strip() == cmd:
            lines[0] = ' '.join((lines[0], lines[1].strip(), lines[2].strip()))
            del lines[1], lines[1]
        elif len(lines) >= 2 and lines[0].strip() == ' '.join((cmd, name)):
            lines[0] = ' '.join((lines[0], lines[1].strip()))
            del lines[1]

        # Escape linebreaks of multi-line commands
        for i in range(len(lines)-1):
            lines[i] += ' \\'

        if escape:
            return ['#' + line for line in lines]
        else:
            return lines

    def _get_keybindings(self):
        from ...tui.tuiobjects import keymap
        contexts = []
        for context in sorted(keymap.contexts):
            # Command can consist of one or two lines
            lines = []
            for key,action in keymap.map(context):
                desc = keymap.get_description(key, context)
                escape = self._is_default_keybinding(key, context, action, desc)
                cmd = self._wrap_bind_cmd(key, action, context, desc, escape)
                lines.extend(cmd)
            contexts.append('\n'.join(lines))
        return '\n\n'.join(contexts)

    def _is_default_keybinding(self, key, context, action, description):
        from ...tui.tuiobjects import keymap
        if isinstance(key, tuple):
            key_str = ' '.join(k.strip('<>') for k in key)
        else:
            key_str = key.strip('<>')
        dct = {'key': key_str, 'action': action}
        if context != keymap.DEFAULT_CONTEXT:
            dct['context'] = context
        if description:
            dct['description'] = description
        for d in defaults.DEFAULT_KEYMAP:
            if dct == d:
                return True
        return False

    def _wrap_bind_cmd(self, key, action, context, description, escape=False):
        from ...tui.tuiobjects import keymap
        lines = [['bind']]
        if description:
            lines[0].extend(('--description', cliparser.quote(description), '\\'))
            indent = len(lines[0][0]) * ' '
            lines.append([indent])
        if context != keymap.DEFAULT_CONTEXT:
            lines[-1].extend(('--context', cliparser.quote(context))),
        lines[-1].append(cliparser.quote(str(key)))
        lines[-1].append(str(action))
        if escape:
            return ['#' + ' '.join(line) for line in lines]
        else:
            return [' '.join(line) for line in lines]


class RcCmdbase(mixin.get_rc_filepath,
                metaclass=InitCommand):
    name = 'rc'
    aliases = ('source',)
    category = 'configuration'
    provides = set()
    description = 'Run commands in rc file'
    usage = ('rc <FILE>',)
    examples = (f'rc rc.example    # Load $XDG_CONFIG_HOME{os.sep}.config/{{__appname__}}{os.sep}rc.example',
                f'rc ./rc.example  # Load rc.example from current working directory',)
    argspecs = (
        {'names': ('FILE',),
         'description': ('Path to rc file; if FILE does not exist and does not start with '
                         f'"{os.sep}", ".{os.sep}" or "~", '
                         f'"$XDG_CONFIG_HOME{os.sep}.config{os.sep}{{__appname__}}{os.sep}" '
                         'is prepended')},
    )

    async def run(self, FILE):
        filepath = self.get_rc_filepath(FILE)
        try:
            lines = rcfile.read(filepath)
        except rcfile.RcFileError as e:
            raise CmdError('Loading rc file failed: %s' % e)
        else:
            log.debug('Running commands from rc file: %r', filepath)
            for cmdline in lines:
                success = await objects.cmdmgr.run_async(cmdline)
                # False means failure, None means the command didn't run because
                # the active interface doesn't support it
                if success is False:
                    raise CmdError()

    @classmethod
    def completion_candidates_posargs(cls, args):
        """Complete positional arguments"""
        # Command only takes one argument
        if args.curarg_index == 1:
            return candidates.fs_path(args.curarg.before_cursor,
                                      base=os.path.dirname(defaults.DEFAULT_RCFILE))


class ResetCmdbase(metaclass=InitCommand):
    name = 'reset'
    category = 'configuration'
    provides = set()
    description = 'Reset settings to their default values'
    usage = ('reset <NAME> <NAME> <NAME> ...',)
    examples = ('reset connect.port',)
    argspecs = (
        {'names': ('NAME',), 'nargs': '+',
         'description': 'Name of setting'},
    )
    more_sections = {
        'SEE ALSO': ('Run `help settings` for a list of all available settings.',
                     'Note that remote settings (srv.*) cannot be reset.'),
    }

    def run(self, NAME):
        success = True
        for name in utils.listify_args(NAME):
            try:
                objects.cfg.reset(name)
            except NotImplementedError:
                self.error('Remote settings cannot be reset: %s' % name)
                success = False
            except KeyError:
                self.error('Unknown setting: %s' % name)
                success = False
        if not success:
            raise CmdError()

    @classmethod
    def completion_candidates_posargs(cls, args):
        """Complete positional arguments"""
        return candidates.setting_names()


class SetCmdbase(mixin.get_setting_sorter, mixin.get_setting_columns,
                 metaclass=InitCommand):
    name = 'set'
    category = 'configuration'
    provides = set()
    description = 'Change or list settings'
    usage = ('set [<NAME>[:eval]] [<VALUE>]',)
    examples = ('set connect.host my.server.example.org',
                'set connect.user jonny_sixpack',
                'set connect.password:eval getpw --id transmission',
                'set tui.log.height +=10')
    from ...views.setting import COLUMNS
    from ...client.sorters import SettingSorter
    argspecs = (
        {'names': ('NAME',), 'nargs': '?',
         'description': "Name of setting; append ':eval' to turn VALUE into a shell command"},

        {'names': ('VALUE',), 'nargs': 'REMAINDER',
         'description': ('New value or shell command that prints the new value to stdout; '
                         "numerical values can be adjusted by prepending '+=' or '-='")},

        { 'names': ('--sort', '-s'),
          'description': ('Comma-separated list of sort orders when listing settings '
                          '(see SORT ORDERS section)') },

        { 'names': ('--columns', '-c'),
          'default_description': "current value of 'columns.settings' setting",
          'description': ('Comma-separated list of column names when listing settings '
                          '(see COLUMNS section)') },
    )
    more_sections = {
        'COLUMNS': make_COLUMNS_doc(COLUMNS, '--columns', 'columns.settings'),
        'SORT ORDERS': make_SORT_ORDERS_doc(SettingSorter, '--sort', 'sort.settings'),
        'SCRIPTING': make_SCRIPTING_doc(name),
        'SEE ALSO': (('Run `help settings` or run `set` without commands '
                      'for a list of available local and remote settings.'),),
    }

    async def run(self, NAME, VALUE, sort, columns):
        if not NAME and not VALUE:
            # Get remote setting values
            try:
                await objects.cfg.update()
            except ClientError as e:
                error = e
            else:
                error = None

            # Show list of settings
            sort = objects.localcfg['sort.settings'] if sort is None else self._parse_value(sort, listify=True)
            columns = objects.localcfg['columns.settings'] if columns is None else self._parse_value(columns, listify=True)
            try:
                sort = self.get_setting_sorter(sort)
                columns = self.get_setting_columns(columns)
            except ValueError as e:
                raise CmdError(e)
            else:
                self.make_setting_list(sort, columns)
                if error:
                    raise CmdError(error)
            return

        # NAME might have ':eval' attached if VALUE is shell command.
        try:
            name = self._parse_name(NAME)
        except ValueError as e:
            raise CmdError(e)

        # Get current value in case we want to display or adjust it
        if objects.cfg.is_remote(name):
            try:
                await objects.cfg.update()
            except ClientError as e:
                raise CmdError(e)

        # VALUE might be shell command or have '+='/'-=' prepended
        try:
            value = self._parse_value(VALUE,
                                      listify=isinstance(objects.cfg[name], (tuple, list)),
                                      is_cmd=NAME.endswith(':eval'))
        except ValueError as e:
            # Report potential stderr output if VALUE is a command
            raise CmdError('%s: %s' % (name, e))

        # Separate '+=' or '-=' from value
        try:
            op, value = self._get_operator(value)
        except ValueError as e:
            # Report invalid value after operator (e.g. nan)
            raise CmdError('%s = %s: %s' % (name, self._stringify(value), e))

        # Apply operator to current value
        if op is not None:
            try:
                value = self._adjust_value(objects.cfg[name], op, value)
            except ValueError as e:
                # Report out-of-bounds value
                opfunc = getattr(operator, op)
                unbound = objects.cfg[name].copy(min=-float('inf'), max=float('inf'))
                invalid = opfunc(unbound, value)
                raise CmdError('%s = %s: %s' % (name, self._stringify(invalid), e))

        # Update setting's value
        try:
            await objects.cfg.set(name, value)
        except ValueError as e:
            raise CmdError('%s = %s: %s' % (name, self._stringify(value), e))
        except ClientError as e:
            raise CmdError(e)

    def _parse_name(self, name):
        if name.endswith(':eval'):
            name = name[:-5]
        if name in objects.cfg:
            return name
        else:
            raise ValueError('Unknown setting: %s' % name)

    def _parse_value(self, value, listify=False, is_cmd=False):
        if is_cmd:
            value = [self._eval_cmd(value)]
        if listify:
            return utils.listify_args(value)
        else:
            return ' '.join(value)

    @staticmethod
    def _eval_cmd(cmd):
        if not isinstance(cmd, str):
            cmd = ' '.join(cmd)
        log.debug('Running shell command: %r', cmd)
        proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout = proc.stdout.decode('utf-8').strip('\n')
        stderr = proc.stderr.decode('utf-8').strip('\n')
        if stderr:
            raise ValueError(stderr)
        else:
            return stdout

    @staticmethod
    def _get_operator(value):
        if isinstance(value, str):
            stripped = value.strip()
            if len(stripped) >= 3:
                def to_num(string):
                    try:
                        return usertypes.Float(string)
                    except ValueError as e:
                        raise ValueError('%s: %r' % (e, string))
                if stripped[:2] == '+=':
                    return '__add__', to_num(stripped[2:])
                elif stripped[:2] == '-=':
                    return '__sub__', to_num(stripped[2:])
        return None, value

    @staticmethod
    def _adjust_value(current, op, value):
        if isinstance(current, (float, int)):
            if current >= float('inf'):
                current = usertypes.Float(0)
            func = getattr(operator, op)
            value = func(current, value)
        return value

    @staticmethod
    def _stringify(value):
        if not isinstance(value, str) and isinstance(value, abc.Iterable):
            return ', '.join(str(item) for item in value)
        else:
            return str(value)

    @classmethod
    def completion_candidates_posargs(cls, args):
        """Complete positional arguments"""
        # If --columns or --sort is anywhere, we only display options
        for arg in args:
            if cls.short_options.get(arg, arg) in ('--columns', '--sort'):
                return

        settings = candidates.setting_names()
        if args.curarg_index == 1:
            log.debug('Returning setting names: %r', settings)
            return settings
        elif args.curarg_index >= 2:
            log.debug('Completing values for %r', args[1])
            # Remove command name from command line
            return candidates.setting_values(args[1:])

    @classmethod
    def completion_candidates_opts(cls, args):
        """Return candidates for arguments that start with '-'"""
        # Only complete options or parameters for options if there are no
        # positional arguments (i.e. when the command doesn't look like it's
        # changing a setting).  But positional arguments may also be parameters
        # for --columns or --sort.
        posargs = args.posargs({('--columns', '-c'): 1,
                                ('--sort', '-s'): 1})
        if len(posargs) == 1:
            if args.curarg_index == 1:
                return (super().completion_candidates_opts(args),
                        candidates.setting_names())
            else:
                return super().completion_candidates_opts(args)

    @classmethod
    def completion_candidates_params(cls, option, args):
        """Complete parameters (e.g. --option parameter1,parameter2)"""
        if option == '--sort':
            return candidates.sort_orders('SettingSorter')
        elif option == '--columns':
            return candidates.column_names('settings')


class RateLimitCmdbase(metaclass=InitCommand):
    name = 'ratelimit'
    aliases = ('rate', 'rl')
    provides = set()
    category = 'configuration'
    description = 'Limit transfer rates per torrent or globally'
    usage = ('ratelimit',
             'ratelimit <DIRECTION>',
             'ratelimit <DIRECTION> <LIMIT>',
             'ratelimit <DIRECTION> <LIMIT> <TORRENT FILTER> <TORRENT FILTER> ...')
    examples = ('ratelimit up 5Mb',
                'ratelimit down,up 1M global',
                'ratelimit up,dn off "This torrent" size<100MB')
    argspecs = (
        {'names': ('DIRECTION',), 'nargs': '?', 'default': 'up,down',
         'description': 'Any combination of "up", "down" or "dn" separated by a comma'},

        {'names': ('LIMIT',), 'nargs': '?',
         'description': ('Maximum allowed transfer rate (see `help srv.limit.rate.up` '
                         'for the syntax) or "show" to display the current limit')},

        make_X_FILTER_spec('TORRENT', or_focused=True, nargs='*',
                           more_text=('"global" to set global limit (same as setting '
                                      "srv.limit.rate.<DIRECTION>); may be omitted in CLI mode "
                                      'for the same effect as specifying "global"')),

        { 'names': ('--quiet','-q'), 'action': 'store_true',
          'description': 'Do not show new bandwidth rate(s)' },
    )

    async def run(self, DIRECTION, LIMIT, TORRENT_FILTER, quiet):
        directions = tuple('down' if d == 'dn' else d
                           for d in map(str.lower, DIRECTION.split(',')))
        for d in directions:
            if d not in ('up', 'down'):
                raise CmdError('Invalid direction: %r' % (d,))

        # _show_limits() and _set_limits() are defined in cli.config and
        # tui.config because the TUI can use the focused torrent while the CLI
        # can't.
        if not LIMIT or LIMIT == 'show':
            await self._show_limits(TORRENT_FILTER, directions)
        else:
            # Do we adjust current limits or set absolute limits?
            limit = LIMIT.strip()
            if limit[:2] == '+=' or limit[:2] == '-=':
                adjust = True
                limit = limit[0] + limit[2:]  # Remove '=' so it can be parsed as a number
            else:
                adjust = False

            await self._set_limits(TORRENT_FILTER, directions, limit,
                                   adjust=adjust, quiet=quiet)

    async def _show_global_limits(self, directions):
        for d in directions:
            get_method = getattr(objects.srvapi.settings, 'get_limit_rate_' + d)
            limit = await get_method()
            self._output('Global %sload rate limit: %s' % (d, limit))

    async def _show_individual_limits(self, TORRENT_FILTER, directions):
        try:
            tfilter = self.select_torrents(TORRENT_FILTER,
                                           allow_no_filter=False,
                                           discover_torrent=True)
        except ValueError as e:
            raise CmdError(e)
        request = objects.srvapi.torrent.torrents(
            tfilter, keys=('name', 'limit-rate-up', 'limit-rate-down'))
        response = await self.make_request(request, polling_frenzy=True, quiet=True)
        if response.success:
            for t in response.torrents:
                for d in directions:
                    self._output('%s %sload rate limit: %s' %
                                 (t['name'], d, t['limit-rate-%s' % d]))

    async def _set_global_limits(self, directions, limit, quiet=False, adjust=False):
        for d in directions:
            log.debug('Setting global %s rate limit: %r', d, limit)
            set_method = getattr(objects.srvapi.settings,
                                 ('adjust' if adjust else 'set') + '_limit_rate_' + d)
            get_method = getattr(objects.srvapi.settings, 'get_limit_rate_' + d)
            try:
                try:
                    await set_method(limit)
                except ValueError as e:
                    raise CmdError('%s: %r' % (e, limit))
                if not quiet:
                    limit = await get_method()
                    self.info('Global %sload rate limit: %s' % (d, limit))
            except ClientError as e:
                raise CmdError(e)

    async def _set_individual_limits(self, TORRENT_FILTER, directions, limit, quiet=False, adjust=False):
        try:
            tfilter = self.select_torrents(TORRENT_FILTER,
                                           allow_no_filter=False,
                                           discover_torrent=True)
        except ValueError as e:
            raise CmdError(e)

        log.debug('Setting %sload rate limit for %s torrents: %r',
                  '+'.join(directions), tfilter, limit)

        success = True
        for d in directions:
            method = getattr(objects.srvapi.torrent,
                             ('adjust' if adjust else 'set') + '_limit_rate_' + d)
            response = await self.make_request(method(tfilter, limit),
                                               polling_frenzy=True, quiet=quiet)
            success = success and response.success
        if not success:
            raise CmdError()

    @classmethod
    def completion_candidates_posargs(cls, args):
        posargs = args.posargs()
        if posargs.curarg_index == 1:
            cands = []
            if 'up' not in posargs.curarg:
                cands.append('up')
            if all(x not in posargs.curarg for x in ('down', 'dn')):
                cands.append('down')
            return candidates.Candidates(cands, label='Direction', curarg_seps=(',',))
        elif posargs.curarg_index >= 3:
            return candidates.torrent_filter(args.curarg)
