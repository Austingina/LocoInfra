"""Render a distinct mobile page while inheriting the tested Line-B UI controls."""

from __future__ import annotations

from apps.line_b_web.server import INDEX as LINE_B_INDEX


def index_html() -> bytes:
    text = LINE_B_INDEX.read_text(encoding="utf-8")
    text = text.replace("Q1 线路 B 控制", "Q1 4×舞蹈与行走")
    text = text.replace("跳舞（完整动作）", "4×新舞蹈（约 30.1 秒）")
    text = text.replace("完整舞蹈已启用", "4×新舞蹈已启用")
    text = text.replace("线路 B：摆姿检查", "新网页：摆姿检查")
    marker = '  <p class="route" id="routeText">'
    at = text.find(marker)
    if at < 0:
        raise RuntimeError("线路 B 页面结构已变更，拒绝生成新页面")
    end = text.find("</p>", at) + len("</p>")
    notice = (
        '\n  <section class="card" style="border-color:#9568df">'
        '<strong>独立新网页 · 端口 8769</strong>'
        '<p class="hint">连续摇杆行走使用随部署包校验的 dual_v3b；舞蹈使用 Q1 4× 策略。'
        '完整舞蹈约 30.1 秒，只能从 WALK 零速站稳状态开始。'
        '本页与原线路 B 不能同时持有低层控制权。</p></section>'
    )
    return (text[:end] + notice + text[end:]).encode("utf-8")
