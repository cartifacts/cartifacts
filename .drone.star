PY_IMAGE = "python:3.10"

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

def main(ctx):
    pipelines = []

    if ctx.build.event in ("push", "pull_request"):
        pipelines.append(py_code_quality())

    return pipelines
