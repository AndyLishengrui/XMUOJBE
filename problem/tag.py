from collections import OrderedDict

from django.db import transaction
from django.db.models import Count, Q

from utils.api import APIError

from .models import Problem, ProblemTag


def clean_tag_name(name):
    if name is None:
        return ""
    return " ".join(str(name).strip().split())


def normalize_tag_name(name):
    cleaned_name = clean_tag_name(name)
    if not cleaned_name:
        return ""
    return cleaned_name.lower()


def clean_tag_aliases(aliases, canonical_name=None):
    canonical_normalized = normalize_tag_name(canonical_name)
    cleaned_aliases = []
    seen = set()
    for alias in aliases or []:
        cleaned_alias = clean_tag_name(alias)
        normalized_alias = normalize_tag_name(cleaned_alias)
        if not normalized_alias or normalized_alias == canonical_normalized or normalized_alias in seen:
            continue
        seen.add(normalized_alias)
        cleaned_aliases.append(cleaned_alias)
    return cleaned_aliases


def normalize_problem_tag_instance(tag):
    tag.name = clean_tag_name(tag.name)
    tag.normalized_name = normalize_tag_name(tag.name)
    tag.aliases = clean_tag_aliases(getattr(tag, "aliases", []), canonical_name=tag.name)
    return tag


def build_problem_tag_lookup(tags):
    lookup = OrderedDict()
    collisions = {}
    for tag in tags:
        keys = [tag.normalized_name or normalize_tag_name(tag.name)]
        keys.extend(normalize_tag_name(alias) for alias in (tag.aliases or []))
        for key in keys:
            if not key:
                continue
            existing = lookup.get(key)
            if existing is None:
                lookup[key] = tag
            elif existing.id != tag.id:
                collisions.setdefault(key, [existing.id])
                if tag.id not in collisions[key]:
                    collisions[key].append(tag.id)
    return lookup, collisions


def get_problem_tag_queryset(keyword=None, include_inactive=False, only_used=False):
    queryset = ProblemTag.objects.all()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    normalized_keyword = normalize_tag_name(keyword)
    keyword = clean_tag_name(keyword)
    if keyword:
        keyword_query = Q(name__icontains=keyword)
        if normalized_keyword:
            keyword_query |= Q(normalized_name__icontains=normalized_keyword)
        queryset = queryset.filter(keyword_query)
    queryset = queryset.annotate(problem_count=Count("problem", distinct=True))
    if only_used:
        queryset = queryset.filter(problem_count__gt=0)
    return queryset.order_by("rank", "name", "id")


def resolve_problem_tags(tag_names, allow_create=False):
    active_tags = list(ProblemTag.objects.filter(is_active=True).order_by("rank", "id"))
    lookup, collisions = build_problem_tag_lookup(active_tags)
    resolved_tags = []
    invalid_tags = []
    seen_tag_ids = set()
    for raw_tag in tag_names:
        cleaned_name = clean_tag_name(raw_tag)
        normalized_name = normalize_tag_name(cleaned_name)
        if not normalized_name:
            continue
        tag = lookup.get(normalized_name)
        if tag is None:
            if not allow_create:
                invalid_tags.append(cleaned_name)
                continue
            tag = ProblemTag.objects.create(name=cleaned_name,
                                            normalized_name=normalized_name,
                                            aliases=[],
                                            is_active=True)
            lookup[normalized_name] = tag
        if tag.id in seen_tag_ids:
            continue
        seen_tag_ids.add(tag.id)
        resolved_tags.append(tag)
    return resolved_tags, invalid_tags, collisions


def assign_problem_tags(problem, tag_names, allow_create=False):
    resolved_tags, invalid_tags, collisions = resolve_problem_tags(tag_names, allow_create=allow_create)
    if invalid_tags:
        raise APIError("Unknown tags: {}".format(", ".join(invalid_tags)), err="invalid-tags")
    if not resolved_tags:
        raise APIError("At least one valid tag is required", err="invalid-tags")
    problem.tags.set(resolved_tags)
    return resolved_tags, collisions


@transaction.atomic
def merge_problem_tags(target_tag, source_tags):
    source_tags = [tag for tag in source_tags if tag.id != target_tag.id]
    if not source_tags:
        raise APIError("At least one source tag is required", err="invalid-tags")

    through_model = Problem.tags.through
    alias_candidates = list(target_tag.aliases or [])

    for source_tag in source_tags:
        if source_tag.name != target_tag.name:
            alias_candidates.append(source_tag.name)
        alias_candidates.extend(source_tag.aliases or [])

        problem_ids = through_model.objects.filter(problemtag_id=source_tag.id).values_list("problem_id", flat=True)
        for problem_id in problem_ids:
            through_model.objects.get_or_create(problem_id=problem_id, problemtag_id=target_tag.id)

    target_tag.aliases = clean_tag_aliases(alias_candidates, canonical_name=target_tag.name)
    normalize_problem_tag_instance(target_tag)
    target_tag.save()

    through_model.objects.filter(problemtag_id__in=[tag.id for tag in source_tags]).delete()
    ProblemTag.objects.filter(id__in=[tag.id for tag in source_tags]).delete()
    return target_tag


@transaction.atomic
def delete_problem_tag(tag):
    through_model = Problem.tags.through
    through_model.objects.filter(problemtag_id=tag.id).delete()
    tag.delete()


def serialize_problem_tag_audit(low_frequency_threshold=2):
    tags = list(ProblemTag.objects.annotate(problem_count=Count("problem", distinct=True)).order_by("rank", "name", "id"))
    duplicate_groups = OrderedDict()
    low_frequency_tags = []
    zero_problem_tags = []
    alias_conflicts = []
    lookup, collisions = build_problem_tag_lookup(tags)
    del lookup

    for tag in tags:
        normalized_name = tag.normalized_name or normalize_tag_name(tag.name)
        duplicate_groups.setdefault(normalized_name, []).append({
            "id": tag.id,
            "name": tag.name,
            "problem_count": getattr(tag, "problem_count", 0),
            "is_active": tag.is_active
        })
        if getattr(tag, "problem_count", 0) == 0:
            zero_problem_tags.append({"id": tag.id, "name": tag.name})
        if 0 < getattr(tag, "problem_count", 0) <= low_frequency_threshold:
            low_frequency_tags.append({
                "id": tag.id,
                "name": tag.name,
                "problem_count": getattr(tag, "problem_count", 0)
            })

    for normalized_name, tag_ids in collisions.items():
        alias_conflicts.append({
            "normalized_name": normalized_name,
            "tag_ids": tag_ids
        })

    duplicates = []
    for normalized_name, items in duplicate_groups.items():
        if normalized_name and len(items) > 1:
            duplicates.append({
                "normalized_name": normalized_name,
                "tags": items
            })

    active_count = 0
    for tag in tags:
        if tag.is_active:
            active_count += 1

    return {
        "summary": {
            "total_tags": len(tags),
            "active_tags": active_count,
            "duplicate_groups": len(duplicates),
            "zero_problem_tags": len(zero_problem_tags),
            "low_frequency_tags": len(low_frequency_tags)
        },
        "duplicates": duplicates,
        "zero_problem_tags": zero_problem_tags,
        "low_frequency_tags": low_frequency_tags,
        "alias_conflicts": alias_conflicts
    }