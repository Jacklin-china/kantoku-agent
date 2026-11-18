"""Cumulative multi-turn A/B including compaction, understanding and final-answer input."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from kantoku.config.runtime_paths import isolated_runtime
from kantoku.config.settings import ROOT
from kantoku.core.conversation_context import (
    ConversationContext,
    ConversationSummary,
    TurnUnderstanding,
    token_size,
)
from kantoku.core.conversations import MessageRole, MessageType
from kantoku.core.runtime.store import RuntimeStore


def _model_call(messages, counters):
    system = messages[0]["content"]
    request = messages[-1]["content"]
    size = token_size(messages)
    counters["internal_calls"] += 1
    counters["internal_input_tokens"] += size
    if system.startswith("压缩较早对话"):
        counters["summary_calls"] += 1
        counters["summary_input_tokens"] += size
        return ConversationSummary(
            overview="合成历史摘要；原始消息仍保存在数据库中。", tasks=[], sources=[]
        ).model_dump_json()
    counters["understanding_calls"] += 1
    counters["understanding_input_tokens"] += size
    return TurnUnderstanding(action="chat", relation="new", goal=request).model_dump_json()


def _full_history_messages(history, request):
    return [
        {
            "role": "system",
            "content": "以下是完整会话历史，只根据这些资料回答：\n"
            + json.dumps(
                [
                    {"id": item.id, "role": item.role.value, "content": item.content}
                    for item in history
                ],
                ensure_ascii=False,
            ),
        },
        {"role": "user", "content": request},
    ]


def run(root: Path, turns: int, *, interpret: bool) -> dict:
    directory = root / "tmp" / "cumulative-context"
    directory.mkdir(parents=True)
    store = RuntimeStore(directory / "context.db")
    conversation = store.create_conversation("累计上下文 A/B", domain="comic")
    context = ConversationContext(store, budget=8000, summary_budget=1000, recall_budget=1500)
    counters = {
        "internal_calls": 0,
        "internal_input_tokens": 0,
        "summary_calls": 0,
        "summary_input_tokens": 0,
        "understanding_calls": 0,
        "understanding_input_tokens": 0,
    }
    rows = []
    for turn in range(1, turns + 1):
        request = f"第{turn}轮：继续讨论镜头调度与角色动作，保留当前任务。"
        history = store.list_conversation_messages(conversation.id)
        full_tokens = token_size(_full_history_messages(history, request))
        prepared = context.prepare(
            conversation.id,
            request,
            model_call=lambda messages: _model_call(messages, counters),
            interpret=interpret,
        )
        final_tokens = prepared.token_count
        understanding_valid = (
            prepared.understanding is not None and prepared.understanding.goal == request
        ) if interpret else prepared.understanding is None
        functional_probe = (
            understanding_valid
            and prepared.messages[-1]["content"] == request
            and final_tokens <= 8000
        )
        user_message = store.add_conversation_message(
            conversation.id, role=MessageRole.USER, type=MessageType.TEXT, content=request
        )
        context.remember(conversation.id, prepared, user_message.id)
        store.add_conversation_message(
            conversation.id,
            role=MessageRole.ASSISTANT,
            type=MessageType.TEXT,
            content=f"第{turn}轮合成回复：已记录镜头调度讨论。" * 3,
        )
        rows.append({
            "turn": turn,
            "history_messages_before_request": len(history),
            "full_history_input_tokens": full_tokens,
            "managed_final_answer_input_tokens": final_tokens,
            "managed_internal_input_tokens_so_far": counters["internal_input_tokens"],
            "managed_internal_calls_so_far": counters["internal_calls"],
            "functional_probe_passed": functional_probe,
            "degraded": prepared.degraded,
        })
    full_total = sum(row["full_history_input_tokens"] for row in rows)
    final_total = sum(row["managed_final_answer_input_tokens"] for row in rows)
    managed_total = final_total + counters["internal_input_tokens"]
    return {
        "contract": "kantoku-cumulative-context-ab-v1",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model": "deterministic schema-valid test double; no external model",
            "path": "full_understanding" if interpret else "explicit_direct",
            "turns": turns,
            "messages_per_turn": 2,
        },
        "summary": {
            "functional_probe_passed": sum(row["functional_probe_passed"] for row in rows),
            "functional_probe_total": len(rows),
            "full_history_model_calls": turns,
            "managed_total_model_calls": counters["internal_calls"] + turns,
            "managed_understanding_calls": counters["understanding_calls"],
            "managed_summary_calls": counters["summary_calls"],
            "managed_final_answer_calls": turns,
            "full_history_total_input_tokens": full_total,
            "managed_final_answer_input_tokens": final_total,
            "managed_internal_input_tokens": counters["internal_input_tokens"],
            "managed_summary_input_tokens": counters["summary_input_tokens"],
            "managed_understanding_input_tokens": counters["understanding_input_tokens"],
            "managed_total_input_tokens": managed_total,
            "total_input_reduction_percent": round(100 * (1 - managed_total / full_total), 2),
            "final_answer_input_reduction_percent": round(100 * (1 - final_total / full_total), 2),
        },
        "turns": rows,
        "limitations": [
            "Eighty-turn synthetic conversation measures the real context-selection and persistence algorithm",
            "Baseline sends the accumulated history to one answer call per turn; managed mode includes understanding, summary and final-answer input",
            "Token values use the project's character-based estimator, not provider billing usage",
            "Schema-valid test doubles do not measure semantic recall or answer quality; call counts are reported separately from tokens",
            "Direct mode is measured separately for explicit requests and is not a substitute for semantic understanding of dependent requests",
        ],
        "implementation_sha256": {
            "src/kantoku/core/conversation_context.py": hashlib.sha256(
                (ROOT / "src/kantoku/core/conversation_context.py").read_bytes()
            ).hexdigest(),
            "scripts/context_cumulative_benchmark.py": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=80)
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.turns < 2:
        parser.error("--turns must be at least 2")
    with isolated_runtime(prefix="kantoku-cumulative-context-") as root:
        report = run(root, args.turns, interpret=not args.direct)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    summary = report["summary"]
    return 0 if summary["functional_probe_passed"] == summary["functional_probe_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
