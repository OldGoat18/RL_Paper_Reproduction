"""Build execution matrices without treating command strings as shell programs."""
from itertools import product
from pathlib import Path
import re
import shlex

ALIASES = {
    'algorithms': ('--alg', '--algorithm', '--algo'),
    'environments': ('--env', '--environment', '--env-id', '--env_id'),
    'seeds': ('--seed', '--random-seed', '--random_seed'),
}
STEP_FLAGS = ('--total-timesteps', '--total_timesteps', '--timesteps', '--num_steps',
              '--max_steps', '--max-steps', '--training_steps', '--training-steps')


def arguments(command):
    if isinstance(command, str):
        # Repair templates pasted without a separator, e.g. {algorithm}--env.
        command = re.sub(r'}(?=--)', '} ', command)
        argv = shlex.split(command)
    else:
        argv = list(command)
    if not argv or not all(isinstance(a, str) and '\x00' not in a for a in argv):
        raise ValueError('Enter a valid command')
    return argv


def option(argv, aliases):
    """Return the last value, as argparse does for repeated options."""
    found = None
    for i, arg in enumerate(argv):
        if arg == '--':
            break
        key, sep, value = arg.partition('=')
        if key in aliases:
            found = (key, value if sep else argv[i + 1] if i + 1 < len(argv) else '')
    return found


def replace_option(argv, aliases, flag, value):
    result = []
    index = 0
    insertion = None
    while index < len(argv):
        arg = argv[index]
        if arg == '--':
            break
        if arg.partition('=')[0] in aliases:
            insertion = len(result) if insertion is None else insertion
            index += 1 if '=' in arg else 2
        else:
            result.append(arg)
            index += 1
    insertion = len(result) if insertion is None else insertion
    result.extend(argv[index:])
    if value is not None:
        result[insertion:insertion] = [flag, value]
    return result


def describe_command(command):
    argv = arguments(command)
    values = {}
    for name, aliases in ALIASES.items():
        found = option(argv, aliases)
        values[name] = [found[1]] if found and '{' not in found[1] else []
    return values


def build_commands(data):
    argv = arguments(data['command'])
    selections = data['selections']
    if not isinstance(selections, dict):
        raise ValueError('Selections must be an object')
    dimensions = []
    placeholders = ('{algorithm}', '{env}', '{seeds}')
    flags = data.get('flags', [])
    if data.get('steps') is not None:
        steps = data['steps']
        if type(steps) is not int or steps < 1:
            raise ValueError('Training steps must be a positive integer')
        found = option(argv, STEP_FLAGS)
        step_flag = data.get('steps_flag') or (found[0] if found else next((f for f in STEP_FLAGS if f in flags), None))
        if not step_flag:
            raise ValueError('Training step argument is unconfirmed for this entry point')
        if step_flag not in STEP_FLAGS and step_flag not in flags:
            raise ValueError('Unsupported training step flag')
        argv = replace_option(argv, (*STEP_FLAGS, step_flag), step_flag, str(steps))
    for (name, aliases), placeholder in zip(ALIASES.items(), placeholders):
        binding = data.get('bindings', {}).get(name)
        aliases = (*aliases, binding) if binding else aliases
        values = selections.get(name, [])
        if not isinstance(values, list) or len(values) > 64:
            raise ValueError('Invalid selection list')
        if name == 'seeds':
            if not all(type(v) is int and 0 <= v < 2**32 for v in values):
                raise ValueError('Seeds must be unsigned 32-bit integers')
        elif not all(isinstance(v, str) and v and '\x00' not in v for v in values):
            raise ValueError('Invalid algorithm or environment')
        values = list(dict.fromkeys(values))
        dimensions.append(values or [None])
        found = option(argv, aliases)
        flag = found[0] if found else next((a for a in aliases if a in flags), aliases[0])
        if values:
            if data.get('bindings') is not None and not data['bindings'].get(name) and not found:
                if name == 'algorithms' and len(values) == 1:
                    continue
                if name == 'environments' and data.get('environment_arguments'):
                    continue
                if name == 'environments' and len(values) == 1 and data.get('fixed_environment'):
                    continue
                raise ValueError('No confirmed argument for ' + name)
            argv = replace_option(argv, aliases, data.get('bindings', {}).get(name) or flag, placeholder)
    repeats = data.get('repeats', 1)
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError('Repeats must be between 1 and 20')
    count = repeats
    for values in dimensions:
        count *= len(values)
    if count > 4096:
        raise ValueError('At most 4096 executions per request')
    python = data.get('python')
    if python and re.fullmatch(r'python(?:\d+(?:\.\d+)?)?(?:\.exe)?', Path(argv[0]).name):
        argv[0] = python
    executions = []
    for algorithm, environment, seed, repeat in product(*dimensions, range(1, repeats + 1)):
        substitutions = {'{algorithm}': algorithm, '{env}': environment, '{seed}': seed, '{seeds}': seed, '{repeat}': repeat}
        def substitute(arg):
            for key, value in substitutions.items():
                if key in arg:
                    if value is None:
                        raise ValueError('Select a value for ' + key)
                    arg = arg.replace(key, str(value))
            return arg
        command = [substitute(a) for a in argv]
        if data.get('environment_arguments') and environment is not None:
            mapped = data['environment_arguments'].get(environment)
            if not mapped:
                raise ValueError('Unknown environment selection')
            for index in range(0, len(mapped), 2):
                command = replace_option(command, (mapped[index],), mapped[index], mapped[index + 1])
        executions.append({'command': command, 'display': shlex.join(command), 'seed': seed,
                           'algorithm': algorithm, 'rl_environment': environment, 'steps': data.get('steps'),
                           'repeat': repeat, 'output': substitute(data['output']) if data.get('output') else None})
    return {'template': shlex.join(argv), 'executions': executions}
