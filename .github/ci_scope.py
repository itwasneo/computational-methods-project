"""Select PR checks from the tested merge result; no API token or PR file-list limit."""

import os
from pathlib import Path
import subprocess


def scope(paths):
    frontend = browser = False
    for path in paths:
        if path.startswith(('.github/workflows/', 'examples/interface-contracts/')) or path in {
            '.github/ci_scope.py', 'tools/interface_schema.py', 'src/tsp_hpc/interface/contracts.py',
            'src/tsp_hpc/interface/requests.py', 'src/tsp_hpc/interface/wire.py',
        }:
            return True, True
        if path.startswith('frontend/'):
            frontend = True
            if not (path.endswith('.test.mjs') or path in {
                'frontend/eslint.config.js', 'frontend/prettier.config.js', 'frontend/.prettierignore',
            }):
                browser = True
        elif path.startswith('src/tsp_hpc/interface/') or path in {
            'tools/local_app.py', 'src/tsp_hpc/cli.py', 'src/tsp_hpc/experiments/runner.py',
            'src/tsp_hpc/solvers/registry.py', 'pyproject.toml', 'uv.lock', '.python-version',
        }:
            browser = True
    return frontend, browser


if __name__ == '__main__':
    if os.environ['GITHUB_EVENT_NAME'] == 'pull_request':
        # HEAD is GitHub's synthetic merge commit. Compare with its exact base parent,
        # not the last feature commit. Treat renames as removal + addition.
        paths = subprocess.check_output(['git', 'diff', '--no-renames', '--name-only', '-z', 'HEAD^1', 'HEAD', '--'])
        frontend, browser = scope(os.fsdecode(paths).split('\0'))
    else:  # Manual pre-release/full run.
        frontend = browser = True
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        for key, value in [('frontend', frontend), ('browser', browser)]:
            print(f'{key}={str(value).lower()}', file=output)
            print(f'{key}: {value}')
