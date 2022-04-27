import os
import shlex
import sys

from invoke import task, util


in_ci = os.environ.get("CI", "false") == "true"
if in_ci:
    pty = False
else:
    pty = util.isatty(sys.stdout) and util.isatty(sys.stderr)


@task
def reformat(c):
    c.run("isort app.py cartifacts tasks.py", pty=pty)
    c.run("black app.py cartifacts tasks.py", pty=pty)


@task
def lint(c):
    c.run("flake8 --show-source --statistics app.py cartifacts tasks.py", pty=pty)


@task
def type_check(c):
    c.run("mypy app.py cartifacts", pty=pty)
