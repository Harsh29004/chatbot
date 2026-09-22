"""
The template catalogue as the running system sees it: code, plus admin edits.

``bot.templates`` holds ten frozen dataclasses. They are good defaults and
they are also *code* — changing a decline message or nudging a threshold to
settle a real support complaint should not need a deploy. So this module puts
a thin, reversible layer over them:

* the code catalogue is always the baseline,
* a row in ``template_overrides`` changes named fields on one template,
* deleting that row restores the code default exactly.

Only presentation and behaviour a customer would notice is overridable — name,
tagline, description, scope wording, decline lines, the two thresholds, and
whether the template can be picked at all. The starter sheets, sample
questions and the per-vertical action patterns stay in code, because those
encode the safety reasoning behind a template rather than its copy.

This lives apart from ``bot.templates`` for a plain reason: ``bot.store``
imports the templates module, so the templates module cannot import the store
back. Everything that needs the *live* catalogue imports this one instead.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from pymongo import ASCENDING, ReturnDocument

from backend.shared import config
from backend.shared.mongo import coll, register_indexes

from bot.templates import (
    TEMPLATES,
    BotTemplate,
    get_template as get_code_template,
    template_public_dict,
)

OVERRIDES = "template_overrides"

register_indexes(OVERRIDES, [
    ([("template_id", ASCENDING)], {"unique": True, "name": "uniq_template"}),
])

# The fields an admin may change.
_TEXT_FIELDS = (
    "name", "tagline", "description", "scope_label",
    "decline_message", "near_match_suffix",
)
_FLOAT_FIELDS = ("strong_threshold", "near_threshold")
OVERRIDABLE_FIELDS = (*_TEXT_FIELDS, *_FLOAT_FIELDS)

# A threshold outside this range is not a tuning choice, it is a bot that
# answers everything or nothing.
MIN_THRESHOLD = 0.30
MAX_THRESHOLD = 0.99


class TemplateError(ValueError):
    """An edit that would break the catalogue, with a reason to show."""


def _now() -> str:
    return datetime.now(config.IST).isoformat()


def init_template_tables() -> None:
    """
    Kept as an entry point for startup; MongoDB needs no schema built.

    Absence still means "no opinion, use the code value" — a template with no
    override document inherits every field, which is what makes an override a
    patch rather than a copy.
    """
    return None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _override_rows() -> dict[str, dict[str, Any]]:
    """
    Every override, keyed by template id.

    Read fresh each time rather than cached: there are ten rows at most, the
    table is tiny and indexed by primary key, and a cache would mean an edit
    made in the admin panel not taking effect in the worker next door.
    """
    return {row["template_id"]: row for row in coll(OVERRIDES).find()}


def _apply(template: BotTemplate, override: dict[str, Any] | None) -> BotTemplate:
    if not override:
        return template
    changes = {
        field: override[field]
        for field in OVERRIDABLE_FIELDS
        if override.get(field) is not None
    }
    return replace(template, **changes) if changes else template


def get_template(template_id: str) -> BotTemplate | None:
    """
    The live template for *template_id*, admin edits included.

    Returns a disabled template as normal: a customer whose bot already runs
    one must keep getting the behaviour they configured. Disabling only stops
    *new* bots choosing it — see :func:`is_enabled`.
    """
    template = get_code_template(template_id)
    if template is None:
        return None
    return _apply(template, _override_rows().get(template_id))


def is_enabled(template_id: str) -> bool:
    override = _override_rows().get(template_id)
    return True if override is None else bool(override["enabled"])


def list_templates(include_disabled: bool = False) -> list[dict[str, Any]]:
    """The picker payload — enabled templates only, unless asked otherwise."""
    overrides = _override_rows()
    out = []
    for template in TEMPLATES:
        override = overrides.get(template.id)
        enabled = True if override is None else bool(override["enabled"])
        if not enabled and not include_disabled:
            continue
        payload = template_public_dict(_apply(template, override))
        payload["enabled"] = enabled
        out.append(payload)
    return out


def list_for_admin() -> list[dict[str, Any]]:
    """
    Every template with its code default *and* its current live value, so the
    admin screen can show what was changed and offer to put it back.
    """
    overrides = _override_rows()
    out = []
    for template in TEMPLATES:
        override = overrides.get(template.id)
        live = _apply(template, override)
        changed = [
            field for field in OVERRIDABLE_FIELDS
            if override is not None and override.get(field) is not None
        ]
        out.append({
            **template_public_dict(live),
            "enabled": True if override is None else bool(override["enabled"]),
            "overridden_fields": changed,
            "updated_at": override["updated_at"] if override else None,
            "defaults": {field: getattr(template, field) for field in OVERRIDABLE_FIELDS},
        })
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _validate(changes: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}

    for field, value in changes.items():
        if field not in OVERRIDABLE_FIELDS:
            raise TemplateError(f"{field} is not an editable field.")
        if value is None:
            cleaned[field] = None
            continue

        if field in _FLOAT_FIELDS:
            number = float(value)
            if not MIN_THRESHOLD <= number <= MAX_THRESHOLD:
                raise TemplateError(
                    f"{field} must be between {MIN_THRESHOLD} and {MAX_THRESHOLD} — "
                    "outside that range the bot either answers everything or nothing."
                )
            cleaned[field] = number
        else:
            text = str(value).strip()
            if not text:
                raise TemplateError(f"{field} cannot be blank. Reset it instead.")
            cleaned[field] = text

    return cleaned


def set_override(
    template_id: str,
    changes: dict[str, Any],
    *,
    enabled: bool | None = None,
    updated_by: str = "admin",
) -> dict[str, Any]:
    """
    Apply *changes* to one template. Fields set to ``None`` go back to code.

    Cross-field validation runs against the *resulting* template, not against
    the patch: an edit that only lowers ``strong_threshold`` still has to end
    up above the near threshold it is not touching.
    """
    template = get_code_template(template_id)
    if template is None:
        raise TemplateError(f"No template called {template_id!r}.")

    cleaned = _validate(changes)

    live = get_template(template_id)
    assert live is not None

    def resulting(field: str) -> float:
        """What this field ends up as: patched, reset to code, or untouched."""
        if field not in cleaned:
            return getattr(live, field)
        return getattr(template, field) if cleaned[field] is None else cleaned[field]

    if resulting("near_threshold") > resulting("strong_threshold"):
        raise TemplateError(
            "That would put the near-match threshold above the strong one, so "
            "nothing could ever be a strong match."
        )

    set_fields: dict[str, Any] = {
        **cleaned, "updated_at": _now(), "updated_by": updated_by
    }
    insert_fields: dict[str, Any] = {"template_id": template_id}

    # ``enabled`` goes in exactly one of the two operators. MongoDB rejects an
    # update naming the same field in $set and $setOnInsert, and the two mean
    # different things here: an explicit retire must apply on insert as well as
    # on update, while an edit that says nothing about it must not un-retire a
    # template that is already retired.
    if enabled is not None:
        set_fields["enabled"] = 1 if enabled else 0
    else:
        insert_fields["enabled"] = 1

    coll(OVERRIDES).find_one_and_update(
        {"template_id": template_id},
        {"$set": set_fields, "$setOnInsert": insert_fields},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return next(t for t in list_for_admin() if t["id"] == template_id)


def reset_override(template_id: str) -> dict[str, Any]:
    """Drop every edit and go back to what the code says."""
    if get_code_template(template_id) is None:
        raise TemplateError(f"No template called {template_id!r}.")
    coll(OVERRIDES).delete_one({"template_id": template_id})
    return next(t for t in list_for_admin() if t["id"] == template_id)
