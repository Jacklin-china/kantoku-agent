"""Offline structured-output failure injection; no model or database writes."""

from __future__ import annotations

import copy
import json

from kantoku.capabilities.storyboard import parse_storyboard_response
from kantoku.config import SchemaError
from kantoku.schemas.storyboard import Shot, Storyboard


def structured_output_contract() -> list[dict]:
    board = Storyboard(
        episode="离线故障注入",
        shots=[
            Shot(
                shot_no=number,
                desc=f"镜头{number}",
                dialogue="",
                camera="中景",
                duration_s=3,
                characters=["角色"],
            )
            for number in range(1, 21)
        ],
    ).model_dump(mode="json")
    invalid = {
        "null_content": None,
        "empty_content": "",
        "malformed_json": "{",
        "markdown_fence": "```json\n{}\n```",
        "top_level_list": "[]",
    }

    def changed(identifier, mutate):
        payload = copy.deepcopy(board)
        mutate(payload)
        invalid[identifier] = json.dumps(payload, ensure_ascii=False)

    changed("missing_episode", lambda p: p.pop("episode"))
    changed("blank_episode", lambda p: p.update(episode=" "))
    changed("missing_shots", lambda p: p.pop("shots"))
    changed("shots_object", lambda p: p.update(shots={}))
    changed("null_shots", lambda p: p.update(shots=None))
    changed("nineteen_shots", lambda p: p.update(shots=p["shots"][:19]))

    def twenty_one(p):
        p["shots"].append({**copy.deepcopy(p["shots"][-1]), "shot_no": 21})

    changed("twenty_one_shots", twenty_one)
    changed("duplicate_shot_no", lambda p: p["shots"][1].update(shot_no=1))
    changed("missing_shot_no", lambda p: p["shots"][0].pop("shot_no"))
    changed("blank_description", lambda p: p["shots"][0].update(desc=" "))
    changed("missing_camera", lambda p: p["shots"][0].pop("camera"))
    changed("zero_duration", lambda p: p["shots"][0].update(duration_s=0))
    changed("float_duration", lambda p: p["shots"][0].update(duration_s=3.5))
    changed("string_duration", lambda p: p["shots"][0].update(duration_s="3"))
    changed("characters_string", lambda p: p["shots"][0].update(characters="角色"))
    changed("blank_character", lambda p: p["shots"][0].update(characters=[" "]))
    changed("non_string_dialogue", lambda p: p["shots"][0].update(dialogue=1))
    changed("extra_top_level", lambda p: p.update(unexpected=True))
    changed("extra_shot_field", lambda p: p["shots"][0].update(unexpected=True))
    cases = [("valid_board", json.dumps(board, ensure_ascii=False), True)]
    cases.extend((identifier, raw, False) for identifier, raw in invalid.items())
    results = []
    for identifier, raw, should_pass in cases:
        try:
            parsed = parse_storyboard_response(raw)
            passed, error = should_pass and len(parsed.shots) == 20, None
        except SchemaError as exc:
            passed, error = not should_pass, f"{type(exc).__name__}: {exc.message}"
        except Exception as exc:
            passed, error = False, f"Unexpected:{type(exc).__name__}"
        results.append(
            {
                "id": identifier,
                "expected": "valid" if should_pass else "rejected",
                "passed": passed,
                "error": error,
            }
        )
    return results
