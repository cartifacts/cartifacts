from __future__ import annotations

from typing import TYPE_CHECKING

from celery import Celery


if TYPE_CHECKING:
    from flask import Flask


def make_celery(app: Flask):
    celery = Celery(app.import_name)
    celery.conf.update(app.config["CELERY_CONFIG"])

    class ContextTask(celery.Task):  # type: ignore
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery
