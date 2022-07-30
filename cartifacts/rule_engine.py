import datetime

import rule_engine


def make_expired_builds_context(default_timezone: datetime.tzinfo) -> rule_engine.Context:
    type_resolver = {
        "relative_build_number": rule_engine.DataType.from_type(int),
        "created_at": rule_engine.DataType.DATETIME,
    }

    context = rule_engine.Context(
        resolver=rule_engine.resolve_attribute,
        type_resolver=type_resolver,
        default_timezone=default_timezone,
    )

    return context


__all__ = ("make_expired_builds_context",)
