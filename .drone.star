# cspell:words deps

PY_IMAGE = "python:3.10"
NODE_IMAGE = "node:16"

GIT_SAFE_CMD = "git config --global --add safe.directory /drone/src"

DEFAULT_BRANCH = "main"

def build_refs(ctx):
    if ctx.build.event == "pull_request":
        before_ref = "origin/$$DRONE_TARGET_BRANCH"
        after_ref = "$$DRONE_TARGET_BRANCH"
    elif ctx.build.event == "push":
        if ctx.build.branch == DEFAULT_BRANCH:
            before_ref = "origin/$$DRONE_REPO_BRANCH"
            after_ref = "$$DRONE_COMMIT_BRANCH"
        else:
            before_ref = "$$DRONE_COMMIT_BEFORE"
            after_ref = "$$DRONE_COMMIT_AFTER"

    return before_ref, after_ref

def install_deps():
    return {
        "name": "install-deps",
        "image": PY_IMAGE,
        "pull": "always",
        "commands": [
            "python3 -m venv .venv",
            ". .venv/bin/activate",
            "pip install --upgrade --no-cache-dir pip wheel poetry",
            "poetry install --no-root",
        ],
    }

def py_code_quality_step(name, cmd):
    return {
        "name": name,
        "image": PY_IMAGE,
        "depends_on": ["install-deps"],
        "commands": [
            ". .venv/bin/activate",
            cmd,
        ],
    }

def py_code_quality():
    return {
        "kind": "pipeline",
        "name": "py-code-quality",
        "steps": [
            install_deps(),
            py_code_quality_step("lint", "inv lint"),
            py_code_quality_step("type-check", "inv type-check"),
        ],
    }

def cspell():
    return {
        "name": "cspell",
        "image": NODE_IMAGE,
        "pull": "always",
        "commands": [
            "npm install -g cspell",
            "cspell --version",
            "cspell lint '**/*' '.*'",
        ],
    }

def spellcheck():
    return {
        "kind": "pipeline",
        "name": "spellcheck",
        "steps": [
            cspell(),
        ],
    }

def gitleaks(ctx):
    before_ref, after_ref = build_refs(ctx)

    log_opts = ("--all", before_ref + ".." + after_ref)

    return {
        "name": "gitleaks",
        "image": "ghcr.io/zricethezav/gitleaks:latest",
        "pull": "always",
        "commands": [
            GIT_SAFE_CMD,
            "gitleaks version",
            'gitleaks detect --log-opts="' + " ".join(log_opts) + '"',
        ],
    }

def security(ctx):
    return {
        "kind": "pipeline",
        "name": "security",
        "steps": [
            gitleaks(ctx),
        ],
    }

def printenv(ctx):
    return {
        "kind": "pipeline",
        "name": "printenv",
        "steps": [
            {
                "name": "printenv",
                "image": PY_IMAGE,
                "environment": {
                    "CARTIFACTS_DRONE_CTX": str(ctx),
                },
                "commands": ["env | sort"],
            },
            {
                "name": "git_repo",
                "image": "alpine/git:latest",
                "commands": [
                    GIT_SAFE_CMD,
                    "git remote -v",
                    "git branch -a",
                    "git log --graph --oneline --decorate=short --all",
                ],
            },
        ],
    }

def main(ctx):
    pipelines = []

    #pipelines.append(printenv())
    if ctx.build.event in ("push", "pull_request"):
        pipelines.append(py_code_quality())
        pipelines.append(spellcheck())
        pipelines.append(security(ctx))

    return pipelines
