import os
import sys

from invoke import task, util


in_ci = os.environ.get("CI", "false") == "true"
if in_ci:
    pty = False
else:
    pty = util.isatty(sys.stdout) and util.isatty(sys.stderr)


@task
def reformat(c):
    c.run("isort --skip-glob 'cartifacts/vendor/*' app.py cartifacts s3md5.py tasks.py", pty=pty)
    c.run("black --exclude 'vendor/' app.py cartifacts s3md5.py tasks.py", pty=pty)


@task
def lint(c):
    c.run("flake8 --show-source --statistics app.py cartifacts s3md5.py tasks.py", pty=pty)


@task
def type_check(c):
    c.run("mypy app.py cartifacts s3md5.py", pty=pty)
