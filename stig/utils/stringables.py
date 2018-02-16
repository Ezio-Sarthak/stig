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

from ..logging import make_logger
log = make_logger(__name__)

from functools import partial
from itertools import chain
import re
from collections.abc import Iterable
import os


_INFINITY = float('inf')

def _resolve_alias(value, aliases):
    # Only hashable values can be aliases
    try:
        hash(value)
    except Exception:
        return value
    else:
        # Return original value if it doesn't have an alias
        return aliases.get(value, value)


def _pretty_float(n):
    n_abs = abs(n)
    if n_abs >= _INFINITY:
        return ('-' if n < 0 else '') + '∞'
    elif n_abs == 0:
        return '0'
    n_abs_r2 = round(n_abs, 2)
    if n_abs_r2 == int(n_abs):
        return '%.0f' % n
    elif n_abs_r2 < 10:
        return ('%.2f' % n).rstrip('0')
    elif round(n_abs, 1) < 100:
        return ('%.1f' % n).rstrip('0')
    else:
        return '%.0f' % n


class _PartialConstructor(partial):
    def __init__(self, cls, *posargs, **kwargs):
        repr = cls.__name__ + '('
        if posargs:
            repr += ', '.join('%r' % arg for arg in posargs)
        if kwargs:
            repr += ', '.join('%s=%r' % (k,v) for k,v in kwargs.items())
        self.__repr = repr + ')'
        self.__name__ = cls.__name__

    def __repr__(self):
        return self.__repr


class StringableMixin():
    @classmethod
    def partial(cls, **kwargs):
        return _PartialConstructor(cls, **kwargs)


class String(str, StringableMixin):
    """
    String

    Options:
      minlen: Minimum length of the string
      maxlen: Maximum length of the string
    """
    def __new__(cls, value, minlen=0, maxlen=_INFINITY):
        # Convert
        self = super().__new__(cls, value)

        # Validate
        self_len = len(self)
        if maxlen is not None and self_len > maxlen:
            raise ValueError('Too long (maximum length is %s)' % maxlen)
        if minlen is not None and self_len < minlen:
            raise ValueError('Too short (minimum length is %s)' % minlen)

        self._minlen = minlen
        self._maxlen = maxlen
        return self

    @property
    def syntax(self):
        minlen = self._minlen
        maxlen = self._maxlen
        text = 'string'
        if ((minlen == 1 or minlen <= 0) and
            (maxlen == 1 or maxlen >= _INFINITY)):
            chrstr = 'character'
        else:
            chrstr = 'characters'
        if minlen > 0 and maxlen < _INFINITY:
            if minlen == maxlen:
                text += ' (%s %s)' % (minlen, chrstr)
            else:
                text += ' (%s-%s %s)' % (minlen, maxlen, chrstr)
        elif minlen > 0:
            text += ' (at least %s %s)' % (minlen, chrstr)
        elif maxlen < _INFINITY:
            text += ' (at most %s %s)' % (maxlen, chrstr)
        return text


class Bool(str, StringableMixin):
    """
    Boolean

    Options:
    TODO: ...
    """
    def __new__(cls, value,
                true=('enabled', 'yes', 'on', 'true', '1'),
                false=('disabled', 'no', 'off', 'false', '0')):
        # Validate
        if value in true:
            value = true[0]
            is_true = True
        elif value in false:
            value = false[0]
            is_true = False
        else:
            raise ValueError('Not a boolean value: %r' % value)

        self = super().__new__(cls, value)
        self._is_true = is_true
        self._true = true
        self._false = false
        return self

    @property
    def syntax(self):
        pairs = []
        for pair in zip(self._true, self._false):
            pair = tuple(str(val) for val in pair)
            if pair not in pairs:
                pairs.append(pair)
        return '%s' % '|'.join('/'.join((t,f)) for t,f in pairs)

    def __bool__(self):
        return self._is_true


class Path(str, StringableMixin):
    """
    File system path

    Options:
      mustexist: Whether the path must exist on the local file system
    """
    def __new__(cls, value, mustexist=False):
        # Convert
        value = os.path.expanduser(os.path.normpath(value))
        self = super().__new__(cls, value)

        # Validate
        if mustexist and not os.path.exists(self):
            raise ValueError('No such file or directory')

        return self

    @property
    def syntax(self):
        return 'file system path'

    def __str__(self):
        home = os.environ['HOME']
        if self.startswith(home):
            return '~' + self[len(home):]
        else:
            return self


class Tuple(tuple, StringableMixin):
    """
    Immutable list

    Options:
      sep:     Separator between list items when parsing string
      options: Iterable of valid values; any other values raise ValueError
      aliases: <alias> -> <value> mapping: any occurence of <alias> is replaced
               with <value>
      dedup:   Whether to remove duplicate items
    """
    def __new__(cls, *value, sep=', ', options=None, aliases={}, dedup=False):
        # Convert
        def normalize(val):
            if isinstance(val, str):
                for item in val.split(sep.strip()):
                    yield item.strip()
            else:
                yield val
        value = (chain.from_iterable((normalize(item) for item in value)))

        if aliases:
            value = (_resolve_alias(item, aliases) for item in value)
        if dedup:
            _seen = set()
            value = (item for item in value if not (item in _seen or _seen.add(item)))

        self = super().__new__(cls, value)

        # Validate
        if options is not None:
            invalid_items = tuple(str(item) for item in self if item not in options)
            if invalid_items:
                raise ValueError('Invalid option%s: %s' % (
                    's' if len(invalid_items) != 1 else '',
                    sep.join(invalid_items)))

        self._sep = sep
        self._options = options
        self._aliases = aliases
        return self

    @property
    def syntax(self):
        sep = self._sep.strip()
        return '<OPTION>%s<OPTION>%s...' % (sep, sep)

    @property
    def options(self):
        return self._options

    @property
    def aliases(self):
        return self._aliases

    def __str__(self):
        return self._sep.join(str(item) for item in self)


class Option(str, StringableMixin):
    """
    Single string that can only be one of a given set of string

    Options:
      options: Iterable of valid values; any other values raise ValueError
      aliases: <alias> -> <value> mapping; any occurence of <alias> is replaced
               with <value>
    """
    def __new__(cls, value, options=(), aliases={}):
        value = str(value)
        value = _resolve_alias(value, aliases)
        if value not in options:
            raise ValueError('Not one of: %s' % ', '.join((str(o) for o in options)))
        self = super().__new__(cls, value)
        self._options = options
        return self

    @property
    def syntax(self):
        return '|'.join(str(opt) for opt in self._options)


class _NumberMixin(StringableMixin):
    converters = {
        'B': {'b': lambda value: value * 8},  # bytes to bits
        'b': {'B': lambda value: value / 8},  # bits to bytes
    }

    _prefixes_binary = (('Ti', 1024**4), ('Gi', 1024**3), ('Mi', 1024**2), ('Ki', 1024))
    _prefixes_metric = (('T', 1000**4), ('G', 1000**3), ('M', 1000**2), ('k', 1000))
    _prefixes_dct = {prefix.lower():size
                     for prefix,size in chain.from_iterable(zip(_prefixes_binary,
                                                                _prefixes_metric))}
    _regex = re.compile('^([-+]?(?:\d+\.\d+|\d+|\.\d+|inf)) ?(' +\
                        '|'.join(p[0] for p in chain.from_iterable(zip(_prefixes_binary,
                                                                       _prefixes_metric))) + \
                        '|)([^\s0-9]*?)$',
                        flags=re.IGNORECASE)

    def __new__(cls, value, unit=None, convert_to=None, prefix=None,
                hide_unit=None, min=None, max=None, precise=False):
        log.debug('Making float: value:%r, unit:%r, convert_to:%r, prefix:%r, hide_unit:%r',
                  value, unit, convert_to, prefix, hide_unit)

        if isinstance(value, cls):
            # Use value's arguments as defaults
            defaults = value._args
            unit = unit if unit is not None else defaults['unit']
            prefix = prefix if prefix is not None else defaults['prefix']
            hide_unit = hide_unit if hide_unit is not None else defaults['hide_unit']
            value = float(value)

        # Fill in hardcoded defaults
        prefix = 'metric' if prefix is None else prefix
        hide_unit = False if hide_unit is None else hide_unit

        # Parse strings
        if isinstance(value, str):
            # log.debug('Parsing string: %r', value)
            string = str(value)
            match = cls._regex.match(string)
            if match is None:
                raise ValueError('Not a number: %r' % string)
            else:
                value = float(match.group(1))
                prfx = match.group(2)
                unit = match.group(3) or unit
                if prfx:
                    value *= cls._prefixes_dct[prfx.lower()]

                prfx_len = len(prfx)
                if prfx_len == 2:
                    prefix = 'binary'
                elif prfx_len == 1:
                    prefix = 'metric'

                log.debug('Parsed string to %r, unit=%r, prefix=%r', value, unit, prefix)

        # Scale number to different unit
        if convert_to is not None and unit != convert_to:
            log.debug('converting %r from %r to %r', value, unit, convert_to)
            if unit is None:
                # num has no unit - assume num is already in target unit
                unit = convert_to
                log.debug('  assuming %r is already in %r', value, convert_to)
            else:
                converters = cls.converters
                if unit in converters and convert_to in converters[unit]:
                    converter = converters[unit][convert_to]
                    log.debug('  running %r(%r)', converter, value)
                    value = converter(value)
                    unit = convert_to
                else:
                    raise ValueError('Cannot convert %s to %s' % (unit, convert_to))

        if issubclass(cls, int):
            parent_type = int
            value = round(value)
        else:
            parent_type = float

        if min is not None and value < min:
            raise ValueError('Too small (minimum is %s)' % min)
        elif max is not None and value > max:
            raise ValueError('Too big (maximum is %s)' % max)

        try:
            self = super().__new__(cls, value)
        except TypeError:
            raise ValueError('Not a number: %r' % value)

        self._parent_type = parent_type
        self._str = partial(self.string, unit=not hide_unit, precise=precise)

        if prefix == 'binary':
            self._prefixes = self._prefixes_binary
        elif prefix == 'metric':
            self._prefixes = self._prefixes_metric
        else:
            raise ValueError("prefix must be 'binary' or 'metric', not {!r}".format(prefix))


        # Remember arguments so we can copy them if this instance is passed to the same class
        self._args = {'unit': unit, 'prefix': prefix, 'hide_unit': hide_unit,
                       'min': min, 'max': max, 'precise': precise}
        return self

    @property
    def syntax(self):
        prefixes = (p[0] for p in chain(self._prefixes_binary, self._prefixes_metric))
        return '[+|-]<NUMBER>[%s]' % '|'.join(prefixes)

    def __str__(self):
        return self._str()

    def string(self, unit=True, precise=False):
        """String representation"""
        def get_unit():
            if unit:
                unit_str = self._args['unit']
                if unit_str is not None:
                    return unit_str
            return ''

        absolute = abs(self)
        if self == 0:
            # This should increase efficiency since 0 is a common value
            return '0'
        elif absolute >= _INFINITY:
            return _pretty_float(self)
        elif precise:
            return str(self._parent_type(self)).rstrip('0').rstrip('.') + get_unit()
        else:
            def stringify():
                for prefix,size in self._prefixes:
                    if absolute >= size:
                        # Converting to float/int before doing the math is faster
                        # because we overload math operators.
                        return _pretty_float(float(self) / size) + prefix
                return _pretty_float(self)
            return stringify() + get_unit()

    def _do_math(self, funcname, *args, **kwargs):
        # Get the new value as int or float
        if self >= _INFINITY:
            # No need to do anything with infinity because `int` has no infinity
            # value implemented.
            result = _INFINITY
        else:
            parent_func = getattr(self._parent_type, funcname)
            log.debug('Calling %r(%r, %r, %r)', parent_func, self, args, kwargs)
            result = parent_func(self, *args, **kwargs)

        if result is NotImplemented and len(args) > 0:
            # This may have happened because `self` is `int` and it got a
            # `float` to handle.  To make this work, we must flip `self` and
            # `other`, getting the method from `other` and passing it `self`:
            #
            #     int.__add__(<int>, <float>)  ->  float.__add__(<float>, <int>)
            #
            # If we get the parent method from the instance instead of its type,
            # we don't have to pass two values and it's a little bit faster.
            other_func = getattr(args[0], funcname)
            result = other_func(self, **kwargs)
            if result is NotImplemented:
                return NotImplemented

        # Determine the appropriate class (1.0 should return a Int)
        if isinstance(result, int) or (result < _INFINITY and
                                       isinstance(result, float) and
                                       int(result) == result):
            result_cls = Int
        else:
            result_cls = Float
        log.debug('result_cls: %r', result_cls)

        # Create new instance with copied properties
        return result_cls(result, **self._args)

    def __add__(self, other):             return self._do_math('__add__', other)
    def __sub__(self, other):             return self._do_math('__sub__', other)
    def __mul__(self, other):             return self._do_math('__mul__', other)
    def __div__(self, other):             return self._do_math('__div__', other)
    def __truediv__(self, other):         return self._do_math('__truediv__', other)
    def __floordiv__(self, other):        return self._do_math('__floordiv__', other)
    def __mod__(self, other):             return self._do_math('__mod__', other)
    def __divmod__(self, other):          return self._do_math('__divmod__', other)
    def __pow__(self, other):             return self._do_math('__pow__', other)
    def __floor__(self):                  return self._do_math('__floor__')
    def __ceil__(self):                   return self._do_math('__ceil__')
    def __round__(self, *args, **kwargs): return self._do_math('__round__', *args, **kwargs)

class Float(_NumberMixin, float):
    """
    Floating point number

    TODO: ...
    """

class Int(_NumberMixin, int):
    """
    Integer number

    TODO: ...
    """