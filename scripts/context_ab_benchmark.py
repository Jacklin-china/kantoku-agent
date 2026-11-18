"""Deterministic full-history vs summary/on-demand-read engineering A/B."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
from datetime import UTC, datetime
from pathlib import Path

from kantoku.config.runtime_paths import isolated_runtime
from kantoku.config.settings import ROOT
from kantoku.core.conversation_context import (
    ConversationContext,
    ConversationSummary,
    HistoryRead,
    SourceRef,
    TurnUnderstanding,
    token_size,
)
from kantoku.core.conversations import MessageRole, MessageType
from kantoku.core.runtime.store import RuntimeStore

SCENARIOS = (
    ("early_fact", 120, ((3, "关键事实：主角名字是阿青。"),), 3),
    (
        "latest_correction",
        120,
        ((5, "关键事实：服装是红色。"), (90, "关键事实：服装纠正为蓝色，红色撤回。")),
        90,
    ),
    ("no_matching_fact", 120, (), None),
    ("cross_conversation_isolation", 120, (), None),
    ("long_history", 240, ((2, "关键事实：最早的地点是旧车站。"),), 2),
)


def _model_call(messages, counters):
    system = messages[0]["content"]
    counters["calls"] += 1
    size = token_size(messages)
    counters["total_input_tokens"] += size
    if system.startswith("压缩较早对话"):
        counters["summary_input_tokens"] += size
        return ConversationSummary(
            overview="合成历史摘要，原始消息仍可读取。", tasks=[], sources=[]
        ).model_dump_json()
    counters["final_turn_input_tokens"] = size
    memory = json.loads(messages[1]["content"])
    targets = [
        item
        for item in memory.get("recalled", {}).values()
        if "关键事实" in item.get("content", "")
    ]
    for message in messages[2:]:
        match = re.match(r"\[历史消息 id=([^ ]+) role=", message.get("content", ""))
        if match and "关键事实" in message["content"]:
            targets.append({"id": match.group(1), "kind": "message", "content": message["content"]})
    if targets:
        target = targets[-1]
        return TurnUnderstanding(
            action="chat",
            relation="recall",
            goal=target["content"],
            sources=[SourceRef(id=target["id"], kind="message")],
        ).model_dump_json()
    if memory.get("read_rounds_remaining", 0) >= 2:
        return TurnUnderstanding(
            action="chat",
            relation="recall",
            goal="查找关键事实",
            reads=[HistoryRead(kind="messages", query="关键事实")],
        ).model_dump_json()
    return TurnUnderstanding(
        action="chat", relation="recall", goal="没有找到关键事实"
    ).model_dump_json()


def run_benchmark(root: Path) -> dict:
    cases = []
    for name, message_count, special, expected_index in SCENARIOS:
        directory = root / "tmp" / f"context-{name}"
        directory.mkdir(parents=True)
        store = RuntimeStore(directory / "context.db")
        conversation = store.create_conversation(f"QA {name}", domain="comic")
        if name == "cross_conversation_isolation":
            other = store.create_conversation("QA other", domain="comic")
            store.add_conversation_message(
                other.id,
                role=MessageRole.USER,
                type=MessageType.TEXT,
                content="关键事实：另一会话的秘密角色。",
            )
        messages = []
        expected_id = None
        expected_content = None
        special_map = dict(special)
        for index in range(message_count):
            content = special_map.get(index, f"历史话题 {index % 7} 第 {index} 次讨论。" * 4)
            record = store.add_conversation_message(
                conversation.id, role=MessageRole.USER, type=MessageType.TEXT, content=content
            )
            messages.append(record)
            if index == expected_index:
                expected_id, expected_content = record.id, content
        request = "请核对关键事实。" if expected_id else "请查找是否存在关键事实。"
        full_messages = [
            {
                "role": "system",
                "content": (
                    "以下是有来源的会话记忆。原始用户消息优先于摘要；撤回/纠正优先于旧记录。"
                    "只使用本轮相关来源：\n"
                    + json.dumps(
                        {
                            "recent_dialogue": [
                                {
                                    "id": item.id,
                                    "kind": "message",
                                    "role": "user",
                                    "content": item.content,
                                }
                                for item in messages
                            ],
                            "resolution": None,
                        },
                        ensure_ascii=False,
                    )
                ),
            },
            {"role": "user", "content": request},
        ]
        full_tokens = token_size(full_messages)
        counters = {
            "calls": 0,
            "total_input_tokens": 0,
            "summary_input_tokens": 0,
            "final_turn_input_tokens": 0,
        }
        context = ConversationContext(store, budget=8000, summary_budget=1000, recall_budget=1500)
        prepared = context.prepare(
            conversation.id,
            request,
            model_call=lambda input_messages, counters=counters: _model_call(
                input_messages, counters
            ),
        )
        selected = [source.id for source in prepared.understanding.sources]
        if expected_id:
            full_ok = any(expected_content in message["content"] for message in full_messages)
            managed_ok = (
                expected_id in selected
                and prepared.evidence[expected_id]["content"] == expected_content
            )
        else:
            full_ok = not any("关键事实" in item.content for item in messages)
            managed_ok = not selected and not any(
                "关键事实" in item["content"] for item in prepared.evidence.values()
            )
        reduction = round(100 * (1 - prepared.token_count / full_tokens), 2)
        cases.append(
            {
                "id": name,
                "messages": message_count,
                "expected_source_found": managed_ok,
                "full_history_probe_passed": full_ok,
                "managed_probe_passed": managed_ok,
                "selected_source_ids": selected,
                "full_history_tokens": full_tokens,
                "managed_final_turn_tokens": prepared.token_count,
                "understanding_input_tokens": counters["final_turn_input_tokens"],
                "final_turn_reduction_percent": reduction,
                "summary_input_tokens": counters["summary_input_tokens"],
                "total_managed_model_input_tokens": counters["total_input_tokens"],
                "model_calls": counters["calls"],
                "summary_id": prepared.summary_id,
                "degraded": prepared.degraded,
                "passed": full_ok and managed_ok,
            }
        )
    full_total = sum(case["full_history_tokens"] for case in cases)
    managed_total = sum(case["managed_final_turn_tokens"] for case in cases)
    return {
        "contract": "kantoku-deterministic-context-ab-v1",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model": "deterministic schema-valid test double; no external model",
        },
        "summary": {
            "passed": sum(case["passed"] for case in cases),
            "total": len(cases),
            "full_history_tokens": full_total,
            "managed_final_turn_tokens": managed_total,
            "final_turn_reduction_percent": round(100 * (1 - managed_total / full_total), 2),
            "total_managed_model_input_tokens": sum(
                case["total_managed_model_input_tokens"] for case in cases
            ),
        },
        "cases": cases,
        "limitations": [
            "Synthetic functional probes verify bounded context, source reads and isolation; "
            "not semantic model recall quality",
            "Token values use the project's character-based token_size estimator; "
            "not provider billing usage",
            "Final-turn reduction excludes compaction work; "
            "total managed model input is reported separately",
        ],
        "implementation_sha256": hashlib.sha256(
            (ROOT / "src/kantoku/core/conversation_context.py").read_bytes()
        ).hexdigest(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    with isolated_runtime(prefix="kantoku-context-") as root:
        report = run_benchmark(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] == report["summary"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
