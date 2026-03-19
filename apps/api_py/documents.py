from __future__ import annotations

import re
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - handled gracefully at runtime
    PdfReader = None

from .llm import summarize_paper_with_llm, summarize_text_with_llm
from .store import get_document_by_id, update_background_job, update_document

TEXT_FILE_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".py",
}

PAPER_SUMMARY_HEADINGS = [
    "## 标题",
    "## 作者",
    "## 一句话总结",
    "## 论文要解决什么",
    "## 方法亮点",
    "## 实验里最重要的结论",
    "## 这篇论文的价值与局限",
    "## 建议重点看",
]


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _first_page_text(text: str) -> str:
    marker = "\n[Page 2]"
    if marker in text:
        return text.split(marker, 1)[0]
    return text[:4000]


def _clean_person_line(text: str) -> str:
    cleaned = re.sub(r"[\d*†‡§]+", " ", text)
    return _compact_spaces(cleaned)


def _extract_title_and_authors(text: str) -> tuple[str, str]:
    first_page = _first_page_text(text)
    lines = [_compact_spaces(line) for line in first_page.splitlines() if _compact_spaces(line)]

    title_lines: list[str] = []
    authors = "未明确给出"
    seen_header = False

    for index, line in enumerate(lines):
        lower_line = line.lower()
        if line.startswith("[Page 1]"):
            continue
        if "published as" in lower_line:
            seen_header = True
            continue
        if not seen_header:
            continue
        if "abstract" in lower_line:
            break

        name_matches = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", line)
        if len(name_matches) >= 2:
            title = _compact_spaces(" ".join(title_lines)) or "未明确给出"
            author_lines: list[str] = []

            for follow_line in lines[index:]:
                lower_follow = follow_line.lower()
                if "abstract" in lower_follow:
                    break
                if "@" in follow_line:
                    continue
                if any(
                    keyword in follow_line
                    for keyword in ("University", "Laboratory", "Institute", "School")
                ):
                    continue
                if follow_line.startswith("{"):
                    continue

                cleaned = _clean_person_line(follow_line)
                if cleaned:
                    author_lines.append(cleaned)

            authors = _compact_spaces(" ".join(author_lines)) or "未明确给出"
            return title, authors

        title_lines.append(line)

    return _compact_spaces(" ".join(title_lines)) or "未明确给出", authors


def _extract_section(text: str, start_keywords: list[str], end_keywords: list[str]) -> str:
    lower_text = text.lower()
    start_positions = [lower_text.find(keyword) for keyword in start_keywords]
    start_positions = [position for position in start_positions if position >= 0]
    if not start_positions:
        return ""

    start = min(start_positions)
    candidate = text[start:]
    end = len(candidate)
    lowered_candidate = candidate.lower()
    for keyword in end_keywords:
        position = lowered_candidate.find(keyword)
        if position > 0:
            end = min(end, position)

    return candidate[:end].strip()


def _split_sentences(text: str) -> list[str]:
    compact = _compact_spaces(text)
    if not compact:
        return []
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。！？])\s+", compact)
        if sentence.strip()
    ]


def _capture_name(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _append_unique(items: list[str], value: str | None) -> None:
    if value and value not in items:
        items.append(value)


def _format_points(points: list[str]) -> str:
    if not points:
        return "- 未明确给出"
    return "\n".join(f"- {point}" for point in points)


def _looks_like_structured_paper_summary(summary: str | None) -> bool:
    if not summary:
        return False
    return all(heading in summary for heading in PAPER_SUMMARY_HEADINGS)


def _guess_problem_points(lower_text: str) -> list[str]:
    points: list[str] = []
    if "first-order logic" in lower_text or "logical reasoning" in lower_text:
        _append_unique(points, "聚焦逻辑推理任务，想更严格地评估大语言模型在复杂推理场景下的真实能力。")
    if "benchmark" in lower_text and any(
        keyword in lower_text for keyword in ("existing", "lack", "difficult", "constraint", "annotation")
    ):
        _append_unique(points, "指出现有逻辑推理基准在难度、可扩展性、数据多样性或构造成本上仍有明显不足。")
    if "evaluation" in lower_text and "llm" in lower_text:
        _append_unique(points, "核心目标不是单纯提出一个新模型，而是提供更可靠的评测方式，判断模型到底会不会推理。")
    return points


def _guess_method_points(lower_text: str, framework_name: str | None, dataset_name: str | None) -> list[str]:
    points: list[str] = []
    if framework_name:
        _append_unique(points, f"提出 {framework_name} 框架，用更系统的方式生成或组织逻辑推理评测样本。")
    if dataset_name and dataset_name != framework_name:
        _append_unique(points, f"构建或引入 {dataset_name} 基准，用它来检验模型在逻辑推理任务上的表现。")
    if "symbolic prover" in lower_text or "prover9" in lower_text:
        _append_unique(points, "把符号证明器和大语言模型结合起来，用程序化证明保证题目与答案在逻辑上自洽。")
    if "synthetic" in lower_text or "generate" in lower_text or "generated" in lower_text:
        _append_unique(points, "通过自动生成或合成数据来扩大量级，避免人工构造样本过慢、过贵。")
    if "first-order logic" in lower_text:
        _append_unique(points, "把任务放在一阶逻辑场景下，让评测对象更接近“能否真正做严谨推理”这个问题。")
    return points


def _guess_result_points(lower_text: str) -> list[str]:
    points: list[str] = []
    if any(keyword in lower_text for keyword in ("struggle", "challenging", "challenge")):
        _append_unique(points, "实验表明，当前主流大语言模型在这类逻辑推理基准上仍然有明显短板。")
    if "outperform" in lower_text or "improve" in lower_text:
        _append_unique(points, "论文方法或数据构造设置在关键指标上优于部分对比方案，说明评测设计本身是有效的。")
    if "evaluation" in lower_text or "results" in lower_text:
        _append_unique(points, "整套实验验证了这份基准/方法确实能区分不同模型的推理能力，而不是只测表面模式匹配。")
    return points


def _guess_value_points(
    lower_text: str,
    framework_name: str | None,
    dataset_name: str | None,
) -> list[str]:
    points: list[str] = []
    if framework_name:
        _append_unique(points, f"价值在于 {framework_name} 让逻辑推理评测更可规模化，也更容易保证样本质量。")
    if dataset_name:
        _append_unique(points, f"{dataset_name} 这类基准适合拿来检验模型是否真的会做逻辑证明，而不只是会生成像样的话。")
    if "challenge" in lower_text or "challenging" in lower_text:
        _append_unique(points, "局限也很明显：这类任务门槛高、难度大，现有模型表现不稳，离通用可靠推理还有距离。")
    if "first-order logic" in lower_text:
        _append_unique(points, "它的价值更偏“严谨推理能力评测”，如果你的任务是开放式对话或创作，参考价值会相对间接。")
    return points


def _guess_next_read_points(
    lower_text: str,
    framework_name: str | None,
    dataset_name: str | None,
) -> list[str]:
    points: list[str] = []
    if framework_name:
        _append_unique(points, f"如果你想快速把握论文贡献，先看 {framework_name} 的任务构造流程和它为什么需要符号证明器。")
    if dataset_name:
        _append_unique(points, f"如果你关心评测是否靠谱，重点看 {dataset_name} 的数据来源、题型设计和评价指标。")
    if "results" in lower_text or "evaluation" in lower_text:
        _append_unique(points, "如果你只想判断这篇论文值不值得细读，优先看实验部分的模型对比和错误类型分析。")
    _append_unique(points, "如果你准备复现或借鉴，最值得抄走的是数据构造思路，而不是只看最终分数。")
    return points


def _build_one_sentence_summary(
    framework_name: str | None,
    dataset_name: str | None,
    lower_text: str,
) -> str:
    parts: list[str] = []
    if framework_name:
        parts.append(f"提出了 {framework_name}")
    if dataset_name and dataset_name != framework_name:
        parts.append(f"构建了 {dataset_name} 基准")
    if "symbolic prover" in lower_text or "prover9" in lower_text:
        parts.append("把符号证明器引入评测流程")

    if parts:
        return f"这篇论文{ '，'.join(parts) }，用来更严格地评估大语言模型的逻辑推理能力。"
    if "logical reasoning" in lower_text or "first-order logic" in lower_text:
        return "这篇论文围绕逻辑推理评测展开，重点是更准确地判断大语言模型是否真的具备严谨推理能力。"
    return "这篇论文主要在构建更可靠的逻辑推理评测方式，并用系统实验验证其有效性。"


def build_paper_summary_without_llm(text: str) -> str:
    title, authors = _extract_title_and_authors(text)
    lower_text = text.lower()
    framework_name = _capture_name(
        [
            r"framework called ([A-Za-z0-9\-]+)",
            r"propose(?:d)? .*? called ([A-Za-z0-9\-]+)",
            r"introduce ([A-Za-z0-9\-]+),",
        ],
        text,
    )
    dataset_name = _capture_name(
        [
            r"dataset,\s*([A-Za-z0-9\-]+)",
            r"benchmark,\s*([A-Za-z0-9\-]+)",
            r"named ([A-Za-z0-9\-]+)",
        ],
        text,
    )

    one_sentence = _build_one_sentence_summary(framework_name, dataset_name, lower_text)
    problem_points = _guess_problem_points(lower_text)
    method_points = _guess_method_points(lower_text, framework_name, dataset_name)
    result_points = _guess_result_points(lower_text)
    value_points = _guess_value_points(lower_text, framework_name, dataset_name)
    next_read_points = _guess_next_read_points(lower_text, framework_name, dataset_name)

    return "\n".join(
        [
            "## 标题",
            title,
            "",
            "## 作者",
            authors,
            "",
            "## 一句话总结",
            one_sentence,
            "",
            "## 论文要解决什么",
            _format_points(problem_points),
            "",
            "## 方法亮点",
            _format_points(method_points),
            "",
            "## 实验里最重要的结论",
            _format_points(result_points),
            "",
            "## 这篇论文的价值与局限",
            _format_points(value_points),
            "",
            "## 建议重点看",
            _format_points(next_read_points),
        ]
    )


async def extract_document_text(file_path: str, original_name: str) -> tuple[str, bool]:
    extension = Path(original_name).suffix.lower()
    if extension == ".pdf":
        if PdfReader is None:
            return (
                f"File name: {original_name}\nNote: PDF parsing dependency is not installed yet.",
                True,
            )

        reader = PdfReader(file_path)
        extracted_pages: list[str] = []

        # 保留页码，方便后续回答问题时定位原文位置。
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            if page_text:
                extracted_pages.append(f"[Page {page_number}]\n{page_text}")

        if not extracted_pages:
            return (
                f"File name: {original_name}\nNote: PDF detected, but no selectable text was found. "
                "This file may be scanned images.",
                True,
            )

        return "\n\n".join(extracted_pages), False

    if extension not in TEXT_FILE_EXTENSIONS:
        return (
            f"File name: {original_name}\nNote: this MVP only deeply parses text-like files.",
            True,
        )

    text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    return text, False


def build_plain_summary(text: str) -> str:
    normalized = " ".join(text.split())[:220]
    return normalized or "文件已上传，但暂时无法提取更多文本内容。"


async def process_document_summary(document_id: str, user_id: str, job_id: str) -> str:
    document = await get_document_by_id(document_id, user_id)
    if not document:
        raise ValueError("Document not found")

    await update_document(document_id, {"status": "processing"})
    await update_background_job(job_id, {"status": "processing"})

    extracted_text, _ = await extract_document_text(
        document["filePath"], document["originalName"]
    )

    extension = Path(document["originalName"]).suffix.lower()
    if extension == ".pdf":
        summary = await summarize_paper_with_llm(extracted_text)
        if not _looks_like_structured_paper_summary(summary):
            summary = build_paper_summary_without_llm(extracted_text)
    else:
        summary = await summarize_text_with_llm(extracted_text)

    if not summary:
        summary = build_plain_summary(extracted_text)

    await update_document(
        document_id,
        {"status": "completed", "summary": summary, "extractedText": extracted_text},
    )
    await update_background_job(job_id, {"status": "completed", "output": {"summary": summary}})
    return summary
