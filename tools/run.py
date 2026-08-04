#!/usr/bin/env python3
"""Expand a named profile from profiles.toml into a full harness command.

The harnesses here take 50-odd flags and read 30-odd EICON_* environment
variables, and the combinations that matter are stable: the same quartet of
switches, the same firmware paths, the same registrar, call after call. A
profile names one such combination.

Nothing is hidden: the resolved command, environment included, is printed to
stderr before it runs, in the form that belongs in a session entry in
docs/eicon_adsp_firmware_analysis.md. `-n` prints it without running it.

  ./run native-tower --run 35
  ./run v34-live --run 12 --watch-dm 0x32F7
  ./run -n native-tower --capture-prefix artifacts/scratch/probe
"""

import argparse
import os
import shlex
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / 'profiles.toml'
LOCAL_PROFILES = ROOT / 'profiles.local.toml'


class ProfileError(Exception):
    pass


def load_config():
    """profiles.toml, with profiles.local.toml overlaid a table at a time."""
    if not PROFILES.exists():
        raise ProfileError(f'no profile file at {PROFILES}')
    with PROFILES.open('rb') as fh:
        config = tomllib.load(fh)
    if LOCAL_PROFILES.exists():
        with LOCAL_PROFILES.open('rb') as fh:
            local = tomllib.load(fh)
        config.setdefault('vars', {}).update(local.get('vars', {}))
        for name, profile in local.get('profiles', {}).items():
            config.setdefault('profiles', {}).setdefault(name, {}).update(profile)
    return config


def expand(value, variables, _seen=()):
    """Substitute {name} from [vars], which may themselves refer to others."""
    if not isinstance(value, str):
        return value
    out = value
    for _ in range(10):
        try:
            nxt = out.format(**variables)
        except KeyError as exc:
            raise ProfileError(f'unknown {{{exc.args[0]}}} in {value!r}') from None
        except IndexError:
            raise ProfileError(f'bad placeholder in {value!r}') from None
        if nxt == out:
            return out
        out = nxt
    raise ProfileError(f'placeholder recursion in {value!r}')


def resolve_vars(raw):
    return {name: expand(value, raw) for name, value in raw.items()}


def flatten(config, name, _chain=()):
    """Walk the extends chain, parent first, into one merged profile."""
    profiles = config.get('profiles', {})
    if name not in profiles:
        known = ', '.join(sorted(profiles)) or '(none)'
        raise ProfileError(f'unknown profile {name!r}; known profiles: {known}')
    if name in _chain:
        raise ProfileError(f'extends loop: {" -> ".join((*_chain, name))}')

    profile = profiles[name]
    parent = profile.get('extends')
    merged = flatten(config, parent, (*_chain, name)) if parent else {
        'switches': [], 'options': {}, 'env': {},
    }

    for key in ('python', 'script', 'capture_dir', 'capture_stem'):
        if key in profile:
            merged[key] = profile[key]
    if 'python_args' in profile:
        merged['python_args'] = list(profile['python_args'])

    for switch in profile.get('switches', []):
        if switch not in merged['switches']:
            merged['switches'].append(switch)
    for switch in profile.get('drop_switches', []):
        if switch in merged['switches']:
            merged['switches'].remove(switch)

    merged['options'].update(profile.get('options', {}))
    merged['env'].update(profile.get('env', {}))
    return merged


def negation(switch):
    """--foo <-> --no-foo, so a child or the command line can countermand one."""
    if switch.startswith('--no-'):
        return '--' + switch[len('--no-'):]
    if switch.startswith('--'):
        return '--no-' + switch[2:]
    return None


def build_command(profile, extras, run_number, extra_env, variables):
    if 'script' not in profile:
        raise ProfileError('profile defines no script')

    options = {expand(k, variables): expand(v, variables)
               for k, v in profile['options'].items()}
    switches = [expand(s, variables) for s in profile['switches']]
    env = {k: expand(v, variables) for k, v in profile['env'].items()}
    env.update(extra_env)

    if run_number is not None:
        capture_dir = profile.get('capture_dir')
        if not capture_dir:
            raise ProfileError('--run needs a capture_dir on the profile; '
                               'pass --capture-prefix instead')
        stem = profile.get('capture_stem', 'run')
        options['--capture-prefix'] = expand(
            f'{capture_dir}/{stem}{run_number}', variables)

    # Anything named on the command line wins outright, and the profile's own
    # copy is dropped rather than repeated -- the printed line has to be one a
    # reader can trust, not one with the same flag twice.
    named = {token.split('=', 1)[0] for token in extras if token.startswith('-')}
    options = {k: v for k, v in options.items() if k not in named}
    switches = [s for s in switches
                if s not in named and negation(s) not in named]

    argv = [expand(profile.get('python', sys.executable), variables)]
    argv += list(profile.get('python_args', []))
    argv.append(expand(profile['script'], variables))
    argv += switches
    for key, value in options.items():
        argv += [key, str(value)]
    argv += extras
    return argv, env


def format_command(argv, env, width=76):
    """The command as it should appear in the analysis log: wrapped, quoted."""
    lines = []
    prefix = ' '.join(f'{k}={shlex.quote(str(v))}' for k, v in sorted(env.items()))
    current = prefix

    for token in argv:
        quoted = shlex.quote(str(token))
        if not current:
            current = quoted
        elif len(current) + 1 + len(quoted) <= width:
            current = f'{current} {quoted}'
        else:
            lines.append(current)
            current = '    ' + quoted
    if current:
        lines.append(current)
    return ' \\\n'.join(lines)


def list_profiles(config):
    profiles = config.get('profiles', {})
    if not profiles:
        print('no profiles defined')
        return
    width = max(len(name) for name in profiles)
    for name in sorted(profiles):
        description = profiles[name].get('description', '')
        print(f'  {name:<{width}}  {description}')


def parse_env(assignments):
    env = {}
    for item in assignments:
        if '=' not in item:
            raise ProfileError(f'-e wants KEY=VALUE, got {item!r}')
        key, value = item.split('=', 1)
        env[key] = value
    return env


# Launcher flags, and how many values each takes. None of these collide with a
# flag on the harnesses, so they are recognised wherever they appear -- writing
# `./run native-tower --run 35` is the natural order and has to work. Anything
# after a bare `--` is passed through untouched regardless.
LAUNCHER_FLAGS = {
    '-h': 0, '--help': 0,
    '-l': 0, '--list': 0,
    '-n': 0, '--dry-run': 0,
    '-e': 1, '--env': 1,
    '--run': 1,
}


def split_argv(tokens):
    """Separate launcher flags and the profile name from pass-through args."""
    launcher, extras = [], []
    profile = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == '--':
            extras.extend(tokens[index + 1:])
            break
        name = token.split('=', 1)[0]
        if name in LAUNCHER_FLAGS:
            if '=' in token or LAUNCHER_FLAGS[name] == 0:
                launcher.append(token)
            else:
                launcher.extend(tokens[index:index + 2])
                index += 1
        elif profile is None and not token.startswith('-'):
            profile = token
        else:
            extras.append(token)
        index += 1
    return launcher, profile, extras


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='run', usage='%(prog)s [-n] [-e KEY=VAL] [--run N] PROFILE [ARGS ...]',
        description=__doc__.split('\n\n')[0],
        epilog='Arguments other than these are passed straight through to the '
               'harness and override any same-named flag the profile sets; '
               'everything after a bare -- is passed through untouched.')
    ap.add_argument('-l', '--list', action='store_true',
                    help='list the available profiles and exit')
    ap.add_argument('-n', '--dry-run', action='store_true',
                    help='print the resolved command without running it')
    ap.add_argument('-e', '--env', action='append', default=[], metavar='KEY=VAL',
                    help='set an environment variable (repeatable); wins over '
                         'the profile')
    ap.add_argument('--run', metavar='N',
                    help="capture as <capture_dir>/run<N>, e.g. --run 35")

    launcher, profile, extras = split_argv(
        list(sys.argv[1:] if argv is None else argv))
    args = ap.parse_args(launcher)
    args.profile, args.extras = profile, extras

    try:
        config = load_config()
        if args.list or not args.profile:
            list_profiles(config)
            return 0 if args.list else 2

        variables = resolve_vars(config.get('vars', {}))
        profile = flatten(config, args.profile)
        command, env = build_command(profile, args.extras, args.run,
                                     parse_env(args.env), variables)
    except ProfileError as exc:
        print(f'run: {exc}', file=sys.stderr)
        return 2

    print(format_command(command, env), file=sys.stderr)
    if args.dry_run:
        return 0
    print(file=sys.stderr)

    os.chdir(ROOT)
    try:
        os.execvpe(command[0], command, {**os.environ, **env})
    except OSError as exc:
        print(f'run: cannot execute {command[0]}: {exc}', file=sys.stderr)
        return 127


if __name__ == '__main__':
    sys.exit(main())
