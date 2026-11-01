"""故事草稿的确定性结构检查，不调用模型或修改运行状态。"""

from __future__ import annotations

from .models import ComicStoryDraft


def structural_findings(draft: ComicStoryDraft) -> list[dict]:
    """定位缺失的故事字段与镜头时长；语义一致性由故事服务另行审核。"""
    findings = []
    suggestions = {
        "action": "补充这一镜的动作，写清画面中的主体做了什么。",
        "camera": "补充这一镜的运镜，写清相机的运动方向或固定机位。",
        "transition": "补充与下一镜的衔接方式；最后一镜可填写结束方式。",
    }
    for field, label in (("title", "故事标题"), ("summary", "故事概述")):
        if not getattr(draft, field).strip():
            findings.append({
                "severity": "error", "code": "missing_" + field, "shot_id": None,
                "field": field, "evidence": "（空）", "message": f"缺少{label}。",
                "suggestion": f"补充{label}，说明这段故事的主题或主要情节。",
            })
    if not draft.shots:
        findings.append({
            "severity": "error", "code": "missing_shots", "shot_id": None,
            "field": "image", "evidence": "（还没有镜头）",
            "message": "故事还没有任何镜头，无法核验。",
            "suggestion": "在右侧「本作品的已采用图片」勾选图片后点「AI 整理初稿」，"
                          "或点「添加镜头」逐个补充。",
        })
        return findings
    for index, shot in enumerate(draft.shots, 1):
        for field, label, code in (
            ("action", "动作", "missing_action"),
            ("camera", "运镜", "missing_camera"),
            ("transition", "转场", "missing_transition"),
        ):
            if not str(getattr(shot, field)).strip():
                findings.append({
                    "severity": "error", "code": code, "shot_id": shot.shot_id,
                    "field": field, "evidence": "（空）",
                    "message": f"镜头 {index} 缺少{label}。",
                    "suggestion": suggestions[field],
                })
        if shot.duration_seconds is None:
            findings.append({
                "severity": "error", "code": "missing_duration", "shot_id": shot.shot_id,
                "field": "duration_seconds", "evidence": "（未填写）",
                "message": f"镜头 {index} 缺少时长。",
                "suggestion": "填写 0.1–60 秒之间的时长，按讲述节奏估算即可。",
            })
        elif not 0.1 <= shot.duration_seconds <= 60:
            findings.append({
                "severity": "error", "code": "duration_conflict", "shot_id": shot.shot_id,
                "field": "duration_seconds", "evidence": str(shot.duration_seconds),
                "message": f"镜头 {index} 的时长超出有效范围。",
                "suggestion": "将这一镜的时长改为 0.1–60 秒，再重新校验。",
            })
    return findings
