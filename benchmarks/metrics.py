"""Command-level ranking metrics; out-of-scope cases are diagnostics only."""

from collections import defaultdict
from math import log2


def _strings(value: object, label: str, *, unique: bool = True) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label} must be a list of nonempty strings")
    if unique and len(set(value)) != len(value):
        raise ValueError(f"{label} must not contain duplicates")
    return value


def _validate_cases(cases: object) -> dict[str, dict]:
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")
    validated = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case must be an object")
        case_id = case.get("id")
        category = case.get("category")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("case id must be a nonempty string")
        if case_id in validated:
            raise ValueError(f"duplicate case id: {case_id}")
        if not isinstance(category, str) or not category.strip():
            raise ValueError(f"{case_id}: category must be a nonempty string")
        expected = _strings(case.get("expected_commands"), f"{case_id}.expected_commands")
        forbidden = _strings(case.get("forbidden_commands", []), f"{case_id}.forbidden_commands")
        tags = _strings(case.get("tags", []), f"{case_id}.tags")
        out_of_scope = category == "out_of_scope" or "out_of_scope" in tags
        if out_of_scope and expected:
            raise ValueError(f"{case_id}: out-of-scope cases must have no expected commands")
        if not out_of_scope and not expected:
            raise ValueError(f"{case_id}: in-scope cases need expected commands")
        if set(expected) & set(forbidden):
            raise ValueError(f"{case_id}: a command cannot be both expected and forbidden")
        validated[case_id] = {
            "id": case_id,
            "category": category,
            "tags": list(tags),
            "expected_commands": list(expected),
            "forbidden_commands": list(forbidden),
            "out_of_scope": out_of_scope,
        }
    return validated


def _case_metrics(case: dict, ranking: list[str], ks: tuple[int, ...]) -> dict:
    expected = set(case["expected_commands"])
    forbidden = set(case["forbidden_commands"])
    first_rank = next((i for i, command in enumerate(ranking, 1) if command in expected), None)
    result = {
        **case,
        "ranking": ranking,
        "first_relevant_rank": first_rank,
        "mrr@7": None if case["out_of_scope"] else 1.0 / first_rank if first_rank and first_rank <= 7 else 0.0,
    }
    for k in ks:
        if case["out_of_scope"]:
            result.update({f"hit@{k}": None, f"recall@{k}": None, f"ndcg@{k}": None})
            continue
        found = [i for i, command in enumerate(ranking[:k], 1) if command in expected]
        dcg = sum(1.0 / log2(i + 1) for i in found)
        ideal = sum(1.0 / log2(i + 1) for i in range(1, min(k, len(expected)) + 1))
        result.update({
            f"hit@{k}": float(bool(found)),
            f"recall@{k}": len(found) / len(expected),
            f"ndcg@{k}": dcg / ideal,
        })
    for k in (1, 3):
        result[f"forbidden_hit@{k}"] = (
            float(any(command in forbidden for command in ranking[:k]))
            if forbidden and not case["out_of_scope"] else None
        )
    return result


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _aggregate(rows: list[dict], ks: tuple[int, ...]) -> dict:
    quality_keys = ["mrr@7"] + [f"{metric}@{k}" for k in ks for metric in ("hit", "recall", "ndcg")]
    result = {
        "case_count": len(rows),
        "forbidden_case_count": sum(bool(row["forbidden_commands"]) for row in rows),
    }
    for key in quality_keys + ["forbidden_hit@1", "forbidden_hit@3"]:
        result[key] = _mean([row[key] for row in rows if row[key] is not None])
    return result


def evaluate(
    cases: list[dict],
    rankings: dict[str, list[str]],
    ks: tuple[int, ...] = (1, 3, 5, 7),
) -> dict:
    """Evaluate distinct command templates; missing predictions count as misses.

    Hit counts any acceptable command, recall covers all labeled alternatives,
    and nDCG uses binary relevance. MRR is truncated at seven. Forbidden-hit
    rates use only annotated in-scope cases; higher is worse. Empty metric
    denominators yield None. Macro scores weight represented categories equally.
    """
    if not isinstance(ks, (tuple, list)) or not ks or any(
        not isinstance(k, int) or isinstance(k, bool) or k < 1 for k in ks
    ) or len(set(ks)) != len(ks):
        raise ValueError("ks must contain distinct positive integer cutoffs")
    ks = tuple(sorted(ks))
    validated = _validate_cases(cases)
    if not isinstance(rankings, dict):
        raise ValueError("rankings must map case IDs to command lists")
    if any(not isinstance(case_id, str) for case_id in rankings):
        raise ValueError("ranking IDs must be strings")
    unknown = set(rankings) - set(validated)
    if unknown:
        raise ValueError("rankings contain unknown case IDs: " + ", ".join(sorted(unknown)))
    for case_id, commands in rankings.items():
        _strings(commands, f"rankings[{case_id}]", unique=False)

    per_case = {
        case_id: _case_metrics(case, list(dict.fromkeys(rankings.get(case_id, []))), ks)
        for case_id, case in validated.items()
    }
    in_scope = [row for row in per_case.values() if not row["out_of_scope"]]
    out_of_scope = [row for row in per_case.values() if row["out_of_scope"]]
    categories = defaultdict(list)
    tags = defaultdict(list)
    for row in in_scope:
        categories[row["category"]].append(row)
        for tag in row["tags"]:
            tags[tag].append(row)
    by_category = {key: _aggregate(rows, ks) for key, rows in sorted(categories.items())}
    by_tag = {key: _aggregate(rows, ks) for key, rows in sorted(tags.items())}
    overall = _aggregate(in_scope, ks)
    macro = {
        "category_count": len(by_category),
        "forbidden_category_count": sum(group["forbidden_case_count"] > 0 for group in by_category.values()),
    }
    for key in overall:
        if key not in ("case_count", "forbidden_case_count"):
            macro[key] = _mean([group[key] for group in by_category.values() if group[key] is not None])
    return {
        "case_count": len(per_case),
        "evaluated_case_count": len(in_scope),
        "cutoffs": list(ks),
        "overall": overall,
        "by_category": by_category,
        "by_tag": by_tag,
        "macro_by_category": macro,
        "out_of_scope": {
            "case_count": len(out_of_scope),
            "predicted_case_count": sum(bool(row["ranking"]) for row in out_of_scope),
            "empty_ranking_count": sum(not row["ranking"] for row in out_of_scope),
            "cases": [{"id": row["id"], "ranking": row["ranking"]} for row in out_of_scope],
        },
        "per_case": per_case,
    }
