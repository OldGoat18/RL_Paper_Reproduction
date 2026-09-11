import copy


SOURCE = '''import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--alg', choices=['ppo', 'trpo'])
parser.add_argument('--env', choices=['CartPole-v1', 'MountainCar-v0'])
parser.add_argument('--seed', type=int)
parser.add_argument('--steps', type=int)
parser.add_argument('--learning-rate', type=float, default=0.001)
parser.add_argument('--resume')
args = parser.parse_args()
for step in range(args.steps):
    pass
print('finished', args.steps)
'''


def entry():
    return copy.deepcopy({'path': 'train.py', 'directory': '.',
        'command': 'python train.py --alg ppo --env CartPole-v1 --seed 0 --steps 10',
        'algorithms': ['ppo', 'trpo'], 'environments': ['CartPole-v1', 'MountainCar-v0'],
        'bindings': {'algorithms': '--alg', 'environments': '--env', 'seeds': '--seed'},
        'flags': ['--alg', '--env', '--seed', '--steps', '--learning-rate', '--resume'], 'steps_flag': '--steps',
        'environment_arguments': {}, 'defaults': {},
        'runtime': {'dependency_directory': '.', 'python_specifier': '', 'requirements': [], 'notes': []},
        'recovery': None, 'resources': {'cpu_cores': 1, 'ram_mb': 1, 'gpu_memory_mb': 0},
        'completion': {'success_means_target': True, 'evidence': [{'source': 'train.py', 'quote': 'for step in range(args.steps):\n    pass'}]},
        'evidence': [{'source': 'train.py', 'quote': SOURCE}]})


def envelope(result):
    import json
    return {'choices': [{'message': {'content': json.dumps(result)}, 'finish_reason': 'stop'}]}
