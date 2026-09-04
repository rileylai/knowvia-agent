from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional

from src.proposal_limits import (
    MAX_SUPPLEMENT_CONCEPTS,
    MAX_SUPPLEMENT_NOTES,
    MAX_SUPPLEMENT_SUMMARY_CHARS,
    MAX_SUPPLEMENT_TOTAL_TEXT_CHARS,
)

if TYPE_CHECKING:
    from src.orchestrators.supplement_proposal_schema import SupplementProposalSchema


_URL_LINE_PATTERN = re.compile(
    r"^(?:(?:https?|ftp|file|chrome|edge|about):/{1,3}|www\.)\S+$",
    re.IGNORECASE,
)
_DOMAIN_LINE_PATTERN = re.compile(
    r"^(?:[a-z0-9-]+\.)+(?:com|net|org|io|dev|ai|app|co|tw|cn|jp|uk)(?:/\S*)?$",
    re.IGNORECASE,
)
_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_+#./:-]{1,}")
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_CJK_RUN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}")
_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z])(?:\d+(?:[.,]\d+)?%?|v\d+(?:\.\d+)+)(?![A-Za-z])",
    re.IGNORECASE,
)
_TECHNICAL_ATOM_PATTERN = re.compile(
    r"`[^`\n]+`|https?://\S+|(?<!\w)--?[A-Za-z][A-Za-z0-9_-]*|"
    r"(?<!\w)[A-Za-z][A-Za-z0-9_]*(?:[./:#][A-Za-z0-9_./:#-]+)(?!\w)",
    re.IGNORECASE,
)
_SUMMARY_SENTENCE_BOUNDARY_PATTERN = re.compile(
    r"(?<=[。！？!?])\s*|(?<=[.])(?:\s+|$)"
)
_IMAGE_SECTION_MARKER_PATTERN = re.compile(
    r"\[image\s+\d+(?::[^\]]+)?\]",
    re.IGNORECASE,
)

SCREENSHOT_VALIDATOR_VERSION = "screenshot_grounding_v4"
SCREENSHOT_VALIDATION_GRANULARITY = "summary_sentence_list_item_v1"
SCREENSHOT_TITLE_GROUNDING_FAILURE_MESSAGE = (
    "screenshot proposal title is not supported by OCR source"
)

TITLE_FAILURE_REASON_NO_USABLE_ANCHOR = "NO_USABLE_TITLE_ANCHOR"
TITLE_FAILURE_REASON_INSUFFICIENT_MATCHED_ANCHORS = "INSUFFICIENT_MATCHED_ANCHORS"
TITLE_FAILURE_REASON_UNMATCHED_TECHNICAL_IDENTIFIER = (
    "UNMATCHED_TECHNICAL_IDENTIFIER"
)
TITLE_FAILURE_REASON_UNMATCHED_PRODUCT_NAME = "UNMATCHED_PRODUCT_NAME"
TITLE_FAILURE_REASON_UNMATCHED_NUMBER_OR_VERSION = "UNMATCHED_NUMBER_OR_VERSION"
TITLE_FAILURE_REASON_GENERIC_ONLY = "GENERIC_TITLE_ONLY"
TITLE_FAILURE_REASON_OCR_NORMALIZATION_MISMATCH = "OCR_NORMALIZATION_MISMATCH"
TITLE_FAILURE_REASONS = frozenset(
    {
        TITLE_FAILURE_REASON_NO_USABLE_ANCHOR,
        TITLE_FAILURE_REASON_INSUFFICIENT_MATCHED_ANCHORS,
        TITLE_FAILURE_REASON_UNMATCHED_TECHNICAL_IDENTIFIER,
        TITLE_FAILURE_REASON_UNMATCHED_PRODUCT_NAME,
        TITLE_FAILURE_REASON_UNMATCHED_NUMBER_OR_VERSION,
        TITLE_FAILURE_REASON_GENERIC_ONLY,
        TITLE_FAILURE_REASON_OCR_NORMALIZATION_MISMATCH,
    }
)

_BROWSER_CHROME_LINES = frozenset(
    {
        "back",
        "forward",
        "reload",
        "refresh",
        "home",
        "new tab",
        "search tabs",
        "bookmarks",
        "bookmark",
        "extensions",
        "share",
        "translate",
        "返回",
        "前進",
        "前进",
        "重新整理",
        "刷新",
        "首頁",
        "首页",
        "新分頁",
        "新分页",
        "分頁",
        "分页",
        "搜尋分頁",
        "搜索标签页",
        "書籤",
        "书签",
        "擴充功能",
        "扩展程序",
        "分享",
        "翻譯",
        "翻译",
    }
)

_TRADITIONAL_OUTPUT_REQUIRED = (
    ("会", "會"),
    ("后", "後"),
    ("内容", "內容"),
    ("处理", "處理"),
    ("准备", "準備"),
    ("任务", "任務"),
    ("延迟", "延遲"),
    ("服务", "服務"),
    ("系统", "系統"),
    ("学习", "學習"),
    ("总结", "總結"),
    ("实践", "實踐"),
    ("建议", "建議"),
    ("浏览器", "瀏覽器"),
    ("页面", "頁面"),
    ("数据", "資料"),
)
_GENERIC_SCREENSHOT_TITLES = frozenset(
    {
        "screenshot summary",
        "screenshot proposal",
        "screenshot supplement",
        "learning notes",
        "learning summary",
        "ocr summary",
        "image summary",
    }
)

_SIMPLIFIED_TO_TRADITIONAL = str.maketrans(
    {
        "后": "後",
        "简": "簡",
        "体": "體",
        "与": "與",
        "查": "查",
        "询": "詢",
        "优": "優",
        "化": "化",
        "数": "數",
        "据": "據",
        "处": "處",
        "迟": "遲",
        "启": "啟",
        "动": "動",
        "执": "執",
        "务": "務",
        "统": "統",
        "会": "會",
        "页": "頁",
        "浏": "瀏",
        "览": "覽",
        "器": "器",
        "学": "學",
        "习": "習",
        "总": "總",
        "结": "結",
        "实": "實",
        "践": "踐",
        "议": "議",
        "资": "資",
        "库": "庫",
        "检": "檢",
        "索": "索",
        "编": "編",
        "辑": "輯",
        "调": "調",
        "务": "務",
        "导": "導",
        "致": "致",
        "这": "這",
        "种": "種",
        "开": "開",
        "发": "發",
        "现": "現",
        "线": "線",
        "别": "別",
    }
)
_TITLE_CJK_ALIAS_GROUPS = {
    "index": ("索引",),
    "query": ("查詢",),
    "optimization": ("優化", "調校"),
    "tuning": ("調校", "優化"),
    "performance": ("效能", "性能"),
    "database": ("資料庫", "數據庫"),
    "recruitment": ("招募", "招聘"),
    "readiness": ("就緒", "可用", "準備完成", "準備"),
    "work": ("工作", "任務"),
    "processing": ("處理", "執行"),
}
_TITLE_GENERIC_CJK_PHRASES = frozenset(
    {
        "介紹",
        "整理",
        "筆記",
        "摘要",
        "概覽",
        "概述",
        "內容",
        "說明",
        "主題",
        "標題",
        "來源",
        "畫面",
        "資訊",
    }
)
_TITLE_GENERIC_ENGLISH_TOKENS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "for",
        "from",
        "guide",
        "in",
        "introduction",
        "learning",
        "main",
        "notes",
        "of",
        "on",
        "overview",
        "proposal",
        "summary",
        "startup",
        "the",
        "to",
        "topic",
        "with",
    }
)
_TITLE_HIGH_SPECIFICITY_ENGLISH_TOKENS = frozenset(
    {
        "api",
        "docker",
        "explain",
        "http",
        "java",
        "kubernetes",
        "mysql",
        "notion",
        "postgres",
        "postgresql",
        "pytorch",
        "python",
        "redis",
        "rq",
        "sql",
    }
)
_TITLE_PRODUCT_NAME_TOKENS = frozenset(
    {
        "docker",
        "java",
        "kubernetes",
        "mysql",
        "notion",
        "postgres",
        "postgresql",
        "pytorch",
        "python",
        "redis",
        "redi",
        "telegram",
    }
)
_TITLE_SEMANTIC_GROUPS = (
    ("advice", "建議", "應該", "必須", "推薦", "recommend", "should"),
    ("comparison", "比較", "對比", "相較", "compare", "comparison", "versus", "vs"),
    ("result", "結果", "結論", "影響", "result", "outcome", "conclusion"),
    ("improvement", "提升", "改善", "優化", "更快", "improve", "faster", "better"),
)
_TITLE_SAFE_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "from",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
        "via",
        "about",
        "guide",
        "notes",
        "overview",
        "summary",
        "topic",
        "key",
        "main",
        "based",
        "startup",
    }
)
_TITLE_CONNECTIVE_CJK = frozenset("與的和及之、")

# These words describe the relationship between a proposal and its source.
# They are intentionally limited to reporting/structural language. Content
# words still need a source token or an explicitly supported synonym.
_PROPOSAL_FRAME_TOKENS = frozenset(
    {
        "a",
        "after",
        "all",
        "also",
        "an",
        "and",
        "are",
        "as",
        "at",
        "before",
        "both",
        "by",
        "can",
        "command",
        "content",
        "describes",
        "described",
        "during",
        "each",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "section",
        "service",
        "screenshots",
        "source",
        "states",
        "summary",
        "text",
        "that",
        "the",
        "then",
        "these",
        "this",
        "those",
        "through",
        "to",
        "via",
        "was",
        "were",
        "with",
    }
)

# A small synonym lexicon makes the validator tolerant of ordinary
# paraphrase without turning it into a semantic judge. Unknown product names,
# commands, quantities, and claim words are still rejected below.
_SYNONYM_GROUPS = (
    (
        "describe",
        "describes",
        "described",
        "show",
        "shows",
        "shown",
        "state",
        "states",
        "indicate",
        "indicates",
        "mention",
        "mentions",
        "outline",
        "outlines",
        "summarize",
        "summarizes",
        "capture",
        "captures",
        "highlight",
        "highlights",
        "illustrate",
        "illustrates",
        "present",
        "presents",
        "focus",
        "focuses",
        "detail",
        "details",
    ),
    ("start", "starts", "started", "begin", "begins", "began", "launch", "launches", "launched"),
    ("available", "availability", "ready", "readiness"),
    ("process", "processes", "processed", "processing", "handle", "handles", "handled", "execute", "executes", "executed", "run", "runs", "running"),
    ("use", "uses", "using", "employ", "employs", "employed"),
    ("perform", "performs", "performed", "carry", "carries", "carried", "do", "does"),
    (
        "include",
        "includes",
        "included",
        "contain",
        "contains",
        "contained",
        "list",
        "lists",
    ),
    ("rollout", "rolling", "rollout"),
    ("new", "updated", "update", "updates"),
    ("job", "jobs", "task", "tasks", "work", "works"),
    ("step", "steps", "gradually", "incrementally", "sequence", "flow"),
    ("note", "notes", "point", "points", "item", "items"),
    ("source", "screen", "screenshot", "image", "document"),
    ("process", "workflow", "operation", "operations"),
    ("control", "controls", "determine", "determines", "coordinate", "coordinates"),
)
_SYNONYM_TO_CANONICAL = {
    token: group[0]
    for group in _SYNONYM_GROUPS
    for token in group
}

# Words such as "process" and "ready" can be introduced by a faithful
# summary even when the OCR used a different verb. They are not product or
# domain atoms, and therefore do not by themselves constitute source evidence.
_SAFE_PARAPHRASE_TOKENS = frozenset(
    {
        "begins",
        "better",
        "command",
        "captures",
        "contains",
        "document",
        "efficient",
        "explains",
        "first",
        "follows",
        "gradual",
        "highlights",
        "information",
        "key",
        "later",
        "main",
        "method",
        "overview",
        "primary",
        "process",
        "ready",
        "second",
        "shows",
        "service",
        "summarizes",
        "startup",
        "step",
        "steps",
        "topic",
        "topics",
        "updated",
        "way",
        "when",
        "once",
        "next",
        "finally",
    }
)
_CJK_HIGH_SIGNAL_TECHNICAL_PHRASES = frozenset(
    {
        "分庫分表",
        "分库分表",
    }
)

_ADVICE_PATTERN = re.compile(
    # Do not treat the ordinary verb "use" as advice by itself.  A
    # descriptive sentence such as "the section shows how to use SQL" is
    # still subject to source-evidence checks, but is not an instruction.
    # Imperative "use" remains advice when it starts a sentence.
    r"\b(?:should|recommend(?:ed|ation)?|consider|try|must|need(?:s)? to|"
    r"avoid|you can)\b|"
    r"(?:^|[.!?。！？]\s*)(?:use|try|avoid|consider)\b|"
    r"(?:建議|建议|應該|应该|請|请|必須|必须|務必|务必|需要|避免|推薦|推荐|"
    r"你可以|您可以|最佳實務|最佳实践|best practice)"
)
_CONCLUSION_PATTERN = re.compile(
    r"\b(?:improv(?:e|es|ed|ement)|enhanc(?:e|es|ed|ement)|"
    r"reduc(?:e|es|ed|tion)|increas(?:e|es|ed)|decreas(?:e|es|ed)|"
    r"ensur(?:e|es|ed)|guarante(?:e|es|d)|prevent(?:s|ed)?|"
    r"caus(?:e|es|ed)|lead(?:s|ing)? to|result(?:s|ed)? in|"
    r"therefore|thus|faster|better|more efficient|important|benefit|advantage)\b|"
    r"(?:提升|改善|降低|增加|減少|减少|確保|确保|保證|保证|導致|导致|因此|所以|"
    r"更快|更有效率|重要|優點|优点|好處|好处)"
)
_ABSOLUTE_OR_DESTRUCTIVE_CONTEXT_PATTERN = re.compile(
    r"\b(?:always|never|must|guarantee|guaranteed|best|all cases|drop|delete|truncate|"
    r"overwrite|force|kill)\b|"
    r"(?:一定|永遠|最佳|保證|保證能|所有情況|刪除|清空|截斷|覆寫|強制|不可逆|破壞)"
)
_BOUNDED_CONTEXT_MARKER_PATTERN = re.compile(
    r"\b(?:practical(?:ly)?|application|production|enterprise|trade[- ]?off|"
    r"pitfall|caveat|backend|database|system design|release|representative data)\b|"
    r"(?:實務|企業|生產|發布|注意事項|限制|取捨|應用|成本|觀察|檢查)"
)
_BOUNDED_CONTEXT_ASCII_TOKENS = frozenset(
    {
        "application",
        "architecture",
        "backend",
        "check",
        "cost",
        "data",
        "database",
        "design",
        "engineer",
        "enterprise",
        "evaluate",
        "frequency",
        "high",
        "inspect",
        "maintenance",
        "observe",
        "path",
        "performance",
        "pitfall",
        "plan",
        "practical",
        "production",
        "query",
        "read",
        "release",
        "representative",
        "relevant",
        "system",
        "trade",
        "trade-off",
        "behavior",
        "write",
    }
)
_BOUNDED_CONTEXT_CJK_PHRASES = frozenset(
    {
        "實務",
        "實務上",
        "實務應用",
        "企業",
        "企業系統",
        "生產",
        "生產環境",
        "後端",
        "系統設計",
        "可用於",
        "評估",
        "高頻查詢路徑",
        "發布前",
        "代表性資料",
        "查詢計畫",
        "檢查",
        "注意事項",
        "需同時考慮",
        "讀取效能",
        "寫入維護成本",
        "成本",
        "限制",
        "取捨",
        "觀察",
        "應用",
    }
)
_COMPARISON_PATTERN = re.compile(
    r"\b(?:compare|comparison|versus|vs\.?)\b|"
    r"(?:比較|对比|對比|相較|相较|優於|优于|勝過|胜过)"
)

SUMMARY_GROUNDING_REASON_NO_CLAIM_EXTRACTED = "NO_CLAIM_EXTRACTED"
SUMMARY_GROUNDING_REASON_NEW_TECHNICAL_IDENTIFIER = "NEW_TECHNICAL_IDENTIFIER"
SUMMARY_GROUNDING_REASON_NEW_NUMBER_OR_VERSION = "NEW_NUMBER_OR_VERSION"
SUMMARY_GROUNDING_REASON_UNSUPPORTED_ADVICE = "UNSUPPORTED_ADVICE"
SUMMARY_GROUNDING_REASON_UNSUPPORTED_COMPARISON = "UNSUPPORTED_COMPARISON"
SUMMARY_GROUNDING_REASON_UNSUPPORTED_RESULT = "UNSUPPORTED_RESULT"
SUMMARY_GROUNDING_REASON_INSUFFICIENT_SOURCE_ANCHORS = "INSUFFICIENT_SOURCE_ANCHORS"
SUMMARY_GROUNDING_REASON_PARAPHRASE_NOT_GROUNDED = "PARAPHRASE_NOT_GROUNDED"

_SUMMARY_REPAIR_SAFE_REASONS = frozenset(
    {
        SUMMARY_GROUNDING_REASON_INSUFFICIENT_SOURCE_ANCHORS,
        SUMMARY_GROUNDING_REASON_PARAPHRASE_NOT_GROUNDED,
    }
)
_BODY_REPAIR_SAFE_REASONS = frozenset(
    {
        *_SUMMARY_REPAIR_SAFE_REASONS,
        "CONCEPT_COVERAGE_INCOMPLETE",
        "DUPLICATE_NOTE",
    }
)


@dataclass(frozen=True)
class ScreenshotLanguage:
    code: str
    instruction: str


@dataclass(frozen=True)
class ScreenshotSourceSnapshot:
    """The exact cleaned OCR snapshot shared by prompt and validator."""

    text: str
    normalized_text: str
    source_normalized_char_count: int
    digest: str


@dataclass(frozen=True)
class ScreenshotGroundingDiagnostics:
    source_normalized_char_count: int
    candidate_field_char_count: int
    evidence_claim_count: int
    unsupported_claim_count: int
    validator_version: str
    source_snapshot_digest: str
    prompt_source_digest: str
    validation_source_digest: str
    title_anchor_count: int
    matched_title_anchor_count: int
    unmatched_title_anchor_count: int
    numeric_anchor_count: int
    unmatched_numeric_anchor_count: int
    extracted_claim_count: int
    matched_claim_count: int
    first_unsupported_claim_index: Optional[int]
    first_unsupported_reason: Optional[str]
    failed_field_count: int
    summary_repair_eligible: bool
    validation_granularity: str
    validation_unit_count: int
    matched_validation_unit_count: int
    failed_validation_unit_count: int
    failed_logical_region_count: int
    failed_logical_regions: tuple[str, ...]
    failed_proposal_field_count: int
    summary_validation_unit_count: int
    concept_validation_unit_count: int
    note_validation_unit_count: int
    failed_summary_validation_unit_count: int
    failed_concept_validation_unit_count: int
    failed_note_validation_unit_count: int
    first_unsupported_validation_unit_index: Optional[int]
    body_repair_eligible: bool
    repair_scope: Optional[str]
    matched_exact_ascii_anchor_count: int
    matched_cjk_anchor_count: int
    unmatched_general_token_count: int
    failure_reason_counts: tuple[tuple[str, int], ...]
    failed_validation_unit_details: tuple[Dict[str, object], ...]
    title_failure_reason: Optional[str] = None
    matched_high_specificity_anchor_count: int = 0
    unmatched_high_specificity_anchor_count: int = 0
    matched_general_anchor_count: int = 0
    unmatched_general_anchor_count: int = 0
    unmatched_general_ascii_count: int = 0
    matched_technical_identifier_count: int = 0
    unmatched_technical_identifier_count: int = 0
    concept_count: int = 0
    note_count: int = 0
    covered_concept_count: int = 0
    uncovered_concept_count: int = 0
    notes_with_application_count: int = 0
    title_repair_failure_reason: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "source_normalized_char_count": self.source_normalized_char_count,
            "candidate_field_char_count": self.candidate_field_char_count,
            "evidence_claim_count": self.evidence_claim_count,
            "unsupported_claim_count": self.unsupported_claim_count,
            "validator_version": self.validator_version,
            "source_snapshot_digest": self.source_snapshot_digest,
            "prompt_source_digest": self.prompt_source_digest,
            "validation_source_digest": self.validation_source_digest,
            "title_anchor_count": self.title_anchor_count,
            "matched_title_anchor_count": self.matched_title_anchor_count,
            "unmatched_title_anchor_count": self.unmatched_title_anchor_count,
            "numeric_anchor_count": self.numeric_anchor_count,
            "unmatched_numeric_anchor_count": self.unmatched_numeric_anchor_count,
            "extracted_claim_count": self.extracted_claim_count,
            "matched_claim_count": self.matched_claim_count,
            "first_unsupported_claim_index": self.first_unsupported_claim_index,
            "first_unsupported_reason": self.first_unsupported_reason,
            "failed_field_count": self.failed_field_count,
            "summary_repair_eligible": self.summary_repair_eligible,
            "validation_granularity": self.validation_granularity,
            "validation_unit_count": self.validation_unit_count,
            "matched_validation_unit_count": self.matched_validation_unit_count,
            "failed_validation_unit_count": self.failed_validation_unit_count,
            "failed_logical_region_count": self.failed_logical_region_count,
            "failed_logical_regions": list(self.failed_logical_regions),
            "failed_proposal_field_count": self.failed_proposal_field_count,
            "summary_validation_unit_count": self.summary_validation_unit_count,
            "concept_validation_unit_count": self.concept_validation_unit_count,
            "note_validation_unit_count": self.note_validation_unit_count,
            "failed_summary_validation_unit_count": (
                self.failed_summary_validation_unit_count
            ),
            "failed_concept_validation_unit_count": (
                self.failed_concept_validation_unit_count
            ),
            "failed_note_validation_unit_count": (
                self.failed_note_validation_unit_count
            ),
            "first_unsupported_validation_unit_index": (
                self.first_unsupported_validation_unit_index
            ),
            "body_repair_eligible": self.body_repair_eligible,
            "repair_scope": self.repair_scope,
            "matched_exact_ascii_anchor_count": (
                self.matched_exact_ascii_anchor_count
            ),
            "matched_cjk_anchor_count": self.matched_cjk_anchor_count,
            "unmatched_general_token_count": self.unmatched_general_token_count,
            "failure_reason_counts": dict(self.failure_reason_counts),
            "failed_validation_unit_details": [
                dict(item) for item in self.failed_validation_unit_details
            ],
            "title_failure_reason": self.title_failure_reason,
            "matched_high_specificity_anchor_count": (
                self.matched_high_specificity_anchor_count
            ),
            "unmatched_high_specificity_anchor_count": (
                self.unmatched_high_specificity_anchor_count
            ),
            "matched_general_anchor_count": self.matched_general_anchor_count,
            "unmatched_general_anchor_count": self.unmatched_general_anchor_count,
            "unmatched_general_ascii_count": self.unmatched_general_ascii_count,
            "matched_technical_identifier_count": (
                self.matched_technical_identifier_count
            ),
            "unmatched_technical_identifier_count": (
                self.unmatched_technical_identifier_count
            ),
            "concept_count": self.concept_count,
            "note_count": self.note_count,
            "covered_concept_count": self.covered_concept_count,
            "uncovered_concept_count": self.uncovered_concept_count,
            "notes_with_application_count": self.notes_with_application_count,
            "title_repair_failure_reason": self.title_repair_failure_reason,
        }


@dataclass(frozen=True)
class ScreenshotProposalValidationResult:
    proposal: "SupplementProposalSchema"
    title_fallback_used: bool = False
    diagnostics: Optional[ScreenshotGroundingDiagnostics] = None


@dataclass(frozen=True)
class _TitleGroundingAnalysis:
    title_anchor_count: int = 0
    matched_title_anchor_count: int = 0
    unmatched_title_anchor_count: int = 0
    numeric_anchor_count: int = 0
    unmatched_numeric_anchor_count: int = 0
    matched_high_specificity_anchor_count: int = 0
    unmatched_high_specificity_anchor_count: int = 0
    matched_general_anchor_count: int = 0
    unmatched_general_anchor_count: int = 0
    unmatched_general_ascii_count: int = 0
    matched_technical_identifier_count: int = 0
    unmatched_technical_identifier_count: int = 0
    title_failure_reason: Optional[str] = None
    supported: bool = False

    def as_diagnostic_fields(self) -> Dict[str, int]:
        return {
            "title_anchor_count": self.title_anchor_count,
            "matched_title_anchor_count": self.matched_title_anchor_count,
            "unmatched_title_anchor_count": self.unmatched_title_anchor_count,
            "numeric_anchor_count": self.numeric_anchor_count,
            "unmatched_numeric_anchor_count": self.unmatched_numeric_anchor_count,
            "title_failure_reason": self.title_failure_reason,
            "matched_high_specificity_anchor_count": (
                self.matched_high_specificity_anchor_count
            ),
            "unmatched_high_specificity_anchor_count": (
                self.unmatched_high_specificity_anchor_count
            ),
            "matched_general_anchor_count": self.matched_general_anchor_count,
            "unmatched_general_anchor_count": self.unmatched_general_anchor_count,
            "unmatched_general_ascii_count": self.unmatched_general_ascii_count,
            "matched_technical_identifier_count": (
                self.matched_technical_identifier_count
            ),
            "unmatched_technical_identifier_count": (
                self.unmatched_technical_identifier_count
            ),
            "title_repair_failure_reason": None,
        }


@dataclass(frozen=True)
class _ClaimGroundingAnalysis:
    matched: bool
    reason: Optional[str] = None
    exact_ascii_anchors: tuple[str, ...] = ()
    cjk_anchors: tuple[str, ...] = ()
    unmatched_general_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ProposalValidationField:
    field_path: str
    logical_region: str
    item_index: Optional[int]
    original_value: str
    validation_units: tuple[str, ...]


def build_screenshot_source_snapshot(raw_text: str) -> ScreenshotSourceSnapshot:
    """Build one bounded, deterministic OCR view for prompt and validation."""

    cleaned_text = preprocess_screenshot_ocr_text(raw_text).strip()
    normalized_text = _normalize_for_grounding(cleaned_text)
    digest = hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()
    return ScreenshotSourceSnapshot(
        text=cleaned_text,
        normalized_text=normalized_text,
        source_normalized_char_count=len(normalized_text),
        digest=digest,
    )


def detect_screenshot_language(text: str) -> ScreenshotLanguage:
    """Choose a stable output-language contract from OCR character classes."""

    normalized = unicodedata.normalize("NFKC", text)
    cjk_count = len(_CJK_PATTERN.findall(normalized))
    kana_count = sum(
        1
        for character in normalized
        if "HIRAGANA" in unicodedata.name(character, "")
        or "KATAKANA" in unicodedata.name(character, "")
    )
    hangul_count = sum(
        1 for character in normalized if "HANGUL" in unicodedata.name(character, "")
    )
    cyrillic_count = sum(
        1
        for character in normalized
        if "CYRILLIC" in unicodedata.name(character, "")
    )
    arabic_count = sum(
        1 for character in normalized if "ARABIC" in unicodedata.name(character, "")
    )
    latin_count = len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", normalized))

    if cjk_count and kana_count > max(1, cjk_count // 5):
        return ScreenshotLanguage("ja", "Japanese")
    if hangul_count:
        return ScreenshotLanguage("ko", "Korean")
    if cjk_count:
        return ScreenshotLanguage(
            "zh-Hant",
            "Traditional Chinese (繁體中文)",
        )
    if cyrillic_count:
        return ScreenshotLanguage("ru", "Russian")
    if arabic_count:
        return ScreenshotLanguage("ar", "Arabic")
    if latin_count:
        return ScreenshotLanguage("en", "English")
    return ScreenshotLanguage(
        "en",
        "English unless the source clearly indicates another language",
    )


def preprocess_screenshot_ocr_text(raw_text: str) -> str:
    """Remove high-confidence browser chrome while preserving source OCR order."""

    cleaned_lines: List[str] = []
    for raw_line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if _is_browser_chrome_line(line):
            continue
        cleaned_lines.append(line)

    while cleaned_lines and cleaned_lines[0] == "":
        cleaned_lines.pop(0)
    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()
    cleaned_text = "\n".join(cleaned_lines)
    # Keep source ordering and line boundaries, but repair OCR spaces inside
    # CJK words before the generic grounding validator sees the source.
    return re.sub(
        r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])\s+(?=[\u3400-\u4dbf\u4e00-\u9fff])",
        "",
        cleaned_text,
    )


def validate_screenshot_proposal(
    *,
    proposal: "SupplementProposalSchema",
    source_text: str,
) -> "SupplementProposalSchema":
    """Apply deterministic screenshot quality and source-grounding checks."""

    return _validate_screenshot_proposal(
        proposal=proposal,
        source_snapshot=build_screenshot_source_snapshot(source_text),
    )[0]


def validate_screenshot_proposal_with_diagnostics(
    *,
    proposal: "SupplementProposalSchema",
    source_text: str,
    source_snapshot: Optional[ScreenshotSourceSnapshot] = None,
) -> ScreenshotProposalValidationResult:
    snapshot = source_snapshot or build_screenshot_source_snapshot(source_text)
    validated, diagnostics = _validate_screenshot_proposal(
        proposal=proposal,
        source_snapshot=snapshot,
    )
    return ScreenshotProposalValidationResult(
        proposal=validated,
        diagnostics=diagnostics,
    )


def _validate_screenshot_proposal(
    *,
    proposal: "SupplementProposalSchema",
    source_snapshot: ScreenshotSourceSnapshot,
) -> tuple["SupplementProposalSchema", ScreenshotGroundingDiagnostics]:
    """Validate claims against one exact OCR snapshot and return diagnostics."""

    # Imported lazily to keep the OCR tool path independent from the
    # orchestrators package import order.
    from src.orchestrators.supplement_proposal_schema import (
        SupplementProposalValidationError,
    )

    evidence_claim_count = 0
    matched_claim_count = 0
    extracted_claim_count = 0
    unsupported_claim_count = 0
    first_unsupported_claim_index: Optional[int] = None
    first_unsupported_reason: Optional[str] = None
    failed_fields: set[str] = set()
    failed_logical_regions: set[str] = set()
    failure_reasons: List[str] = []
    failure_reason_counts: Dict[str, int] = {}
    validation_unit_counts = {"summary": 0, "concepts": 0, "notes": 0}
    failed_validation_unit_counts = {"summary": 0, "concepts": 0, "notes": 0}
    matched_exact_ascii_anchor_count = 0
    matched_cjk_anchor_count = 0
    unmatched_general_token_count = 0
    failed_validation_unit_details: List[Dict[str, object]] = []
    private_field_details: List[Dict[str, object]] = []
    first_failure_field: Optional[str] = None
    first_failure_value = ""
    first_failure_message: Optional[str] = None
    title_analysis = _TitleGroundingAnalysis()
    covered_concept_count = 0
    uncovered_concept_count = 0
    notes_with_application_count = sum(
        1 for note in proposal.notes if _has_bounded_context_marker(note)
    )

    def diagnostics(*, value: str, evidence: int, unsupported: int) -> Dict[str, object]:
        safe_repair_failure = bool(
            failure_reasons
            and all(reason in _BODY_REPAIR_SAFE_REASONS for reason in failure_reasons)
            and failed_logical_regions
            and failed_logical_regions.issubset({"summary", "concepts", "notes"})
        )
        summary_repair_eligible = bool(
            safe_repair_failure and failed_logical_regions == {"summary"}
        )
        body_repair_eligible = bool(
            safe_repair_failure and not summary_repair_eligible
        )
        repair_scope = (
            "summary"
            if summary_repair_eligible
            else "body"
            if body_repair_eligible
            else None
        )
        result = ScreenshotGroundingDiagnostics(
            source_normalized_char_count=source_snapshot.source_normalized_char_count,
            candidate_field_char_count=len(value),
            evidence_claim_count=evidence,
            unsupported_claim_count=unsupported,
            validator_version=SCREENSHOT_VALIDATOR_VERSION,
            source_snapshot_digest=source_snapshot.digest,
            prompt_source_digest=source_snapshot.digest,
            validation_source_digest=source_snapshot.digest,
            extracted_claim_count=extracted_claim_count,
            matched_claim_count=matched_claim_count,
            first_unsupported_claim_index=first_unsupported_claim_index,
            first_unsupported_reason=first_unsupported_reason,
            failed_field_count=len(failed_fields),
            summary_repair_eligible=summary_repair_eligible,
            validation_granularity=SCREENSHOT_VALIDATION_GRANULARITY,
            validation_unit_count=extracted_claim_count,
            matched_validation_unit_count=matched_claim_count,
            failed_validation_unit_count=len(failed_validation_unit_details),
            failed_logical_region_count=len(failed_logical_regions),
            failed_logical_regions=tuple(sorted(failed_logical_regions)),
            failed_proposal_field_count=len(failed_logical_regions),
            summary_validation_unit_count=validation_unit_counts["summary"],
            concept_validation_unit_count=validation_unit_counts["concepts"],
            note_validation_unit_count=validation_unit_counts["notes"],
            failed_summary_validation_unit_count=(
                failed_validation_unit_counts["summary"]
            ),
            failed_concept_validation_unit_count=(
                failed_validation_unit_counts["concepts"]
            ),
            failed_note_validation_unit_count=(
                failed_validation_unit_counts["notes"]
            ),
            first_unsupported_validation_unit_index=(
                first_unsupported_claim_index
            ),
            body_repair_eligible=body_repair_eligible,
            repair_scope=repair_scope,
            matched_exact_ascii_anchor_count=matched_exact_ascii_anchor_count,
            matched_cjk_anchor_count=matched_cjk_anchor_count,
            unmatched_general_token_count=unmatched_general_token_count,
            concept_count=len(proposal.concepts),
            note_count=len(proposal.notes),
            covered_concept_count=covered_concept_count,
            uncovered_concept_count=uncovered_concept_count,
            notes_with_application_count=notes_with_application_count,
            failure_reason_counts=tuple(sorted(failure_reason_counts.items())),
            failed_validation_unit_details=tuple(
                dict(item) for item in failed_validation_unit_details
            ),
            **title_analysis.as_diagnostic_fields(),
        )
        return result.as_dict()

    def fail(
        message: str,
        *,
        field: Optional[str] = None,
        value: str = "",
        evidence: Optional[int] = None,
        unsupported: Optional[int] = None,
    ) -> None:
        raise SupplementProposalValidationError(
            message,
            field=field,
            diagnostics=diagnostics(
                value=value,
                evidence=evidence if evidence is not None else evidence_claim_count,
                unsupported=(
                    unsupported
                    if unsupported is not None
                    else unsupported_claim_count
                ),
            ),
            private_diagnostics={"validation_fields": private_field_details},
        )

    if not 3 <= len(proposal.concepts) <= MAX_SUPPLEMENT_CONCEPTS:
        fail(
            f"screenshot proposal concepts must contain 3 to {MAX_SUPPLEMENT_CONCEPTS} items",
            field="concepts",
            value="\n".join(proposal.concepts),
        )
    if not 1 <= len(proposal.notes) <= MAX_SUPPLEMENT_NOTES:
        fail(
            f"screenshot proposal notes must contain 1 to {MAX_SUPPLEMENT_NOTES} items",
            field="notes",
            value="\n".join(proposal.notes),
        )
    if len(proposal.summary) > MAX_SUPPLEMENT_SUMMARY_CHARS:
        fail(
            "screenshot proposal summary exceeds the configured character bound",
            field="summary",
            value=proposal.summary,
        )
    total_text_chars = sum(
        len(value)
        for _, value in _proposal_text_items(proposal)
    )
    if total_text_chars > MAX_SUPPLEMENT_TOTAL_TEXT_CHARS:
        fail(
            "screenshot proposal exceeds the configured total character bound",
            field="proposal",
            value=" ".join(
                [proposal.title, proposal.summary, *proposal.concepts, *proposal.notes]
            ),
        )

    # Compute title diagnostics before language validation so a title-only
    # failure remains explainable even when the whole proposal also violates
    # the output-language contract.
    title_analysis = _analyze_title_grounding(
        value=proposal.title,
        source_text=source_snapshot.text,
    )
    language = detect_screenshot_language(source_snapshot.text)
    try:
        _validate_output_language(proposal, language)
    except SupplementProposalValidationError as exc:
        raise SupplementProposalValidationError(
            exc.message,
            field=exc.field,
            diagnostics=diagnostics(
                value=" ".join(
                    [proposal.title, proposal.summary, *proposal.concepts, *proposal.notes]
                ),
                evidence=0,
                unsupported=0,
            ),
        ) from exc

    source_normalized = source_snapshot.normalized_text
    for label, value in _proposal_text_items(proposal):
        if label == "title":
            title_analysis = _analyze_title_grounding(
                value=value,
                source_text=source_snapshot.text,
            )
            if not title_analysis.supported:
                unsupported_claim_count += 1
                failed_fields.add("title")
                failed_logical_regions.add("title")
                fail(
                    SCREENSHOT_TITLE_GROUNDING_FAILURE_MESSAGE,
                    field="title",
                    value=value,
                    evidence=evidence_claim_count,
                    unsupported=unsupported_claim_count,
                )
            if _introduces_new_advice(
                value=value,
                source_normalized=source_normalized,
            ):
                unsupported_claim_count += 1
                failed_fields.add("title")
                failed_logical_regions.add("title")
                fail(
                    "screenshot proposal title introduces unsupported advice",
                    field="title",
                    value=value,
                    evidence=evidence_claim_count,
                    unsupported=unsupported_claim_count,
                )
            if _introduces_new_conclusion(
                value=value,
                source_normalized=source_normalized,
            ) or _introduces_new_title_semantics(
                value=value,
                source_text=source_snapshot.text,
            ):
                unsupported_claim_count += 1
                failed_fields.add("title")
                failed_logical_regions.add("title")
                fail(
                    "screenshot proposal title introduces unsupported conclusion",
                    field="title",
                    value=value,
                    evidence=evidence_claim_count,
                    unsupported=unsupported_claim_count,
                )
            continue

        validation_field = _build_validation_field(label=label, value=value)
        claims = validation_field.validation_units
        private_field_detail: Dict[str, object] = {
            "field_path": validation_field.field_path,
            "logical_region": validation_field.logical_region,
            "item_index": validation_field.item_index,
            "original_claim": validation_field.original_value,
            "split_result": list(claims),
            "validation_units": [],
        }
        private_field_details.append(private_field_detail)
        if not claims:
            reason = SUMMARY_GROUNDING_REASON_NO_CLAIM_EXTRACTED
            unsupported_claim_count += 1
            failed_fields.add(label)
            failed_logical_regions.add(validation_field.logical_region)
            failure_reasons.append(reason)
            failure_reason_counts[reason] = failure_reason_counts.get(reason, 0) + 1
            if first_unsupported_claim_index is None:
                first_unsupported_claim_index = extracted_claim_count
                first_unsupported_reason = reason
            if first_failure_field is None:
                first_failure_field = validation_field.logical_region
                first_failure_value = value
                first_failure_message = (
                    f"screenshot proposal {label} is not supported by OCR source"
                )
            continue

        for unit_index, claim in enumerate(claims):
            claim_index = extracted_claim_count
            extracted_claim_count += 1
            validation_unit_counts[validation_field.logical_region] += 1
            claim_analysis = _analyze_claim_grounding(
                value=claim,
                source_normalized=source_normalized,
                concepts=proposal.concepts
                if validation_field.logical_region == "notes"
                else (),
            )
            matched_exact_ascii_anchor_count += len(
                claim_analysis.exact_ascii_anchors
            )
            matched_cjk_anchor_count += len(claim_analysis.cjk_anchors)
            unmatched_general_token_count += len(
                claim_analysis.unmatched_general_tokens
            )
            private_unit_result = {
                "validation_unit_index": claim_index,
                "field_unit_index": unit_index,
                "claim": claim,
                "matched": claim_analysis.matched,
                "matched_evidence": [
                    *claim_analysis.exact_ascii_anchors,
                    *claim_analysis.cjk_anchors,
                ],
                "unmatched_general_tokens": list(
                    claim_analysis.unmatched_general_tokens
                ),
                "failure_reason": claim_analysis.reason,
            }
            cast_units = private_field_detail["validation_units"]
            if isinstance(cast_units, list):
                cast_units.append(private_unit_result)
            if claim_analysis.matched:
                matched_claim_count += 1
                evidence_claim_count = matched_claim_count
                continue

            reason = claim_analysis.reason or SUMMARY_GROUNDING_REASON_PARAPHRASE_NOT_GROUNDED
            unsupported_claim_count += 1
            failed_fields.add(label)
            failed_logical_regions.add(validation_field.logical_region)
            failure_reasons.append(reason)
            failure_reason_counts[reason] = failure_reason_counts.get(reason, 0) + 1
            failed_validation_unit_counts[validation_field.logical_region] += 1
            failed_validation_unit_details.append(
                {
                    "validation_unit_index": claim_index,
                    "field": validation_field.logical_region,
                    "field_path": validation_field.field_path,
                    "item_index": validation_field.item_index,
                    "field_unit_index": unit_index,
                    "failure_reason": reason,
                    "exact_ascii_anchor_count": len(
                        claim_analysis.exact_ascii_anchors
                    ),
                    "cjk_anchor_count": len(claim_analysis.cjk_anchors),
                    "unmatched_general_token_count": len(
                        claim_analysis.unmatched_general_tokens
                    ),
                }
            )
            if first_unsupported_claim_index is None:
                first_unsupported_claim_index = claim_index
                first_unsupported_reason = reason
            if first_failure_field is None:
                first_failure_field = validation_field.logical_region
                first_failure_value = value
                if reason == SUMMARY_GROUNDING_REASON_UNSUPPORTED_ADVICE:
                    first_failure_message = (
                        f"screenshot proposal {label} introduces unsupported advice"
                    )
                elif reason == SUMMARY_GROUNDING_REASON_UNSUPPORTED_COMPARISON:
                    first_failure_message = (
                        f"screenshot proposal {label} introduces unsupported comparison"
                    )
                elif reason == SUMMARY_GROUNDING_REASON_UNSUPPORTED_RESULT:
                    first_failure_message = (
                        f"screenshot proposal {label} introduces unsupported conclusion"
                    )
                else:
                    first_failure_message = (
                        f"screenshot proposal {label} is not supported by OCR source"
                    )

    covered_concepts, uncovered_concepts = _concept_coverage(
        concepts=proposal.concepts,
        notes=proposal.notes,
    )
    covered_concept_count = len(covered_concepts)
    uncovered_concept_count = len(uncovered_concepts)
    if uncovered_concepts:
        reason = "CONCEPT_COVERAGE_INCOMPLETE"
        failure_reasons.append(reason)
        failure_reason_counts[reason] = failure_reason_counts.get(reason, 0) + len(
            uncovered_concepts
        )
        failed_logical_regions.add("notes")
        failed_validation_unit_counts["notes"] += len(uncovered_concepts)
        if first_failure_field is None:
            first_failure_field = "notes"
            first_failure_value = "\n".join(proposal.notes)
            first_failure_message = (
                "screenshot proposal notes do not cover all major key concepts"
            )

    duplicate_note_indices = _duplicate_note_indices(proposal.notes)
    if duplicate_note_indices:
        reason = "DUPLICATE_NOTE"
        failure_reasons.append(reason)
        failure_reason_counts[reason] = failure_reason_counts.get(reason, 0) + len(
            duplicate_note_indices
        )
        failed_fields.update(f"notes[{index}]" for index in duplicate_note_indices)
        failed_logical_regions.add("notes")
        failed_validation_unit_counts["notes"] += len(duplicate_note_indices)
        if first_failure_field is None:
            first_failure_field = "notes"
            first_failure_value = "\n".join(proposal.notes)
            first_failure_message = (
                "screenshot proposal notes contain duplicate or near-duplicate items"
            )

    if first_failure_field is not None:
        fail(
            first_failure_message
            or f"screenshot proposal {first_failure_field} is not supported by OCR source",
            field=first_failure_field,
            value=first_failure_value,
            evidence=matched_claim_count,
            unsupported=unsupported_claim_count,
        )

    return proposal, ScreenshotGroundingDiagnostics(
        source_normalized_char_count=source_snapshot.source_normalized_char_count,
        candidate_field_char_count=sum(
            len(value) for _, value in _proposal_text_items(proposal)
        ),
        evidence_claim_count=matched_claim_count,
        unsupported_claim_count=unsupported_claim_count,
        validator_version=SCREENSHOT_VALIDATOR_VERSION,
        source_snapshot_digest=source_snapshot.digest,
        prompt_source_digest=source_snapshot.digest,
        validation_source_digest=source_snapshot.digest,
        extracted_claim_count=extracted_claim_count,
        matched_claim_count=matched_claim_count,
        first_unsupported_claim_index=first_unsupported_claim_index,
        first_unsupported_reason=first_unsupported_reason,
        failed_field_count=len(failed_fields),
        summary_repair_eligible=False,
        validation_granularity=SCREENSHOT_VALIDATION_GRANULARITY,
        validation_unit_count=extracted_claim_count,
        matched_validation_unit_count=matched_claim_count,
        failed_validation_unit_count=0,
        failed_logical_region_count=0,
        failed_logical_regions=(),
        failed_proposal_field_count=0,
        summary_validation_unit_count=validation_unit_counts["summary"],
        concept_validation_unit_count=validation_unit_counts["concepts"],
        note_validation_unit_count=validation_unit_counts["notes"],
        failed_summary_validation_unit_count=0,
        failed_concept_validation_unit_count=0,
        failed_note_validation_unit_count=0,
        first_unsupported_validation_unit_index=None,
        body_repair_eligible=False,
        repair_scope=None,
        matched_exact_ascii_anchor_count=matched_exact_ascii_anchor_count,
        matched_cjk_anchor_count=matched_cjk_anchor_count,
        unmatched_general_token_count=unmatched_general_token_count,
        concept_count=len(proposal.concepts),
        note_count=len(proposal.notes),
        covered_concept_count=covered_concept_count,
        uncovered_concept_count=uncovered_concept_count,
        notes_with_application_count=notes_with_application_count,
        failure_reason_counts=(),
        failed_validation_unit_details=(),
        **title_analysis.as_diagnostic_fields(),
    )


def _split_summary_sentences(value: str) -> List[str]:
    return [
        claim.strip()
        for claim in _SUMMARY_SENTENCE_BOUNDARY_PATTERN.split(value)
        if claim.strip() and re.search(r"[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff]", claim)
    ]


def _build_validation_field(*, label: str, value: str) -> _ProposalValidationField:
    if label == "summary":
        return _ProposalValidationField(
            field_path=label,
            logical_region="summary",
            item_index=None,
            original_value=value,
            validation_units=tuple(_split_summary_sentences(value)),
        )

    match = re.fullmatch(r"(?P<region>concepts|notes)\[(?P<index>\d+)\]", label)
    if match is None:
        raise ValueError(f"unsupported screenshot proposal field: {label}")
    logical_region = match.group("region")
    return _ProposalValidationField(
        field_path=label,
        logical_region=logical_region,
        item_index=int(match.group("index")),
        original_value=value,
        # One concept or note string is one complete list-item validation unit.
        # Internal commas, colons, semicolons, parentheses, and newlines do not
        # become independent phrase claims.
        validation_units=(value.strip(),) if value.strip() else (),
    )


def validate_screenshot_proposal_with_title_fallback(
    *,
    proposal: "SupplementProposalSchema",
    source_text: str,
    source_snapshot: Optional[ScreenshotSourceSnapshot] = None,
) -> ScreenshotProposalValidationResult:
    """Validate a screenshot proposal and repair only a grounded title failure.

    The fallback is intentionally after LLM parsing and before persistence. It
    never calls OCR or a provider. An unrelated title has no source anchor and
    still fails closed; only a title with enough source evidence to identify
    the topic may be replaced by the deterministic source-keyword title.
    """

    from src.orchestrators.supplement_proposal_schema import (
        SupplementProposalValidationError,
    )

    source_snapshot = source_snapshot or build_screenshot_source_snapshot(source_text)
    try:
        validated, diagnostics = _validate_screenshot_proposal(
            proposal=proposal,
            source_snapshot=source_snapshot,
        )
        return ScreenshotProposalValidationResult(
            proposal=validated,
            diagnostics=diagnostics,
        )
    except SupplementProposalValidationError as exc:
        if exc.field != "title" or not _has_partial_title_anchor(
            value=proposal.title,
            source_text=source_snapshot.text,
        ):
            raise

        fallback_title = build_screenshot_fallback_title(source_snapshot.text)
        fallback_proposal = proposal.model_copy(
            update={"title": fallback_title}
        )
        validated, diagnostics = _validate_screenshot_proposal(
            proposal=fallback_proposal,
            source_snapshot=source_snapshot,
        )
        return ScreenshotProposalValidationResult(
            proposal=validated,
            title_fallback_used=True,
            diagnostics=diagnostics,
        )


def _proposal_text_items(proposal: SupplementProposalSchema) -> Iterable[tuple[str, str]]:
    yield "title", proposal.title
    yield "summary", proposal.summary
    for index, concept in enumerate(proposal.concepts):
        yield f"concepts[{index}]", concept
    for index, note in enumerate(proposal.notes):
        yield f"notes[{index}]", note


def _is_browser_chrome_line(line: str) -> bool:
    normalized = line.casefold().strip("|·•-—_ ")
    if normalized in _BROWSER_CHROME_LINES:
        return True
    if _URL_LINE_PATTERN.match(normalized) or _DOMAIN_LINE_PATTERN.match(normalized):
        return True
    if re.search(r"\b(?:https?|ftp|file|chrome|edge)://", normalized):
        return True
    if re.search(r"(?:地址列|網址列|網址|address bar|tab bar|navigation bar)", normalized):
        return True
    return False


def _normalize_for_grounding(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(
        _SIMPLIFIED_TO_TRADITIONAL
    ).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return re.sub(
        r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])\s+(?=[\u3400-\u4dbf\u4e00-\u9fff])",
        "",
        normalized,
    )


def _normalize_title_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(
        _SIMPLIFIED_TO_TRADITIONAL
    ).casefold()
    normalized = normalized.translate(
        str.maketrans(
            {
                "（": "(",
                "）": ")",
                "【": "[",
                "】": "]",
                "「": "\"",
                "」": "\"",
                "『": "\"",
                "』": "\"",
            }
        )
    )
    normalized = re.sub(
        r"[^a-z0-9_+#./:\-\u3400-\u4dbf\u4e00-\u9fff]+",
        " ",
        normalized,
    )
    # OCR frequently inserts spaces inside a CJK word (for example
    # ``索 引`` or ``查 詢``). Keep Latin token boundaries intact while
    # joining only whitespace surrounded by CJK characters.
    normalized = re.sub(
        r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])\s+(?=[\u3400-\u4dbf\u4e00-\u9fff])",
        "",
        normalized,
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_title_source_text(source_text: str) -> str:
    source_without_image_markers = _IMAGE_SECTION_MARKER_PATTERN.sub(
        " ",
        preprocess_screenshot_ocr_text(source_text),
    )
    return _normalize_title_text(source_without_image_markers)


def _title_ascii_tokens(value: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for raw_token in _ASCII_TOKEN_PATTERN.findall(value):
        canonical = _canonical_token(raw_token)
        if not canonical or canonical in _TITLE_GENERIC_ENGLISH_TOKENS:
            continue
        tokens[canonical] = raw_token
    return tokens


def _title_cjk_chunks(value: str) -> List[str]:
    normalized = _normalize_title_text(value)
    for phrase in sorted(_TITLE_GENERIC_CJK_PHRASES, key=len, reverse=True):
        normalized = normalized.replace(phrase, " ")
    for connective in _TITLE_CONNECTIVE_CJK:
        normalized = normalized.replace(connective, " ")
    return _CJK_RUN_PATTERN.findall(normalized)


def _title_cjk_bigrams(value: str) -> set[str]:
    return {
        run[index : index + 2]
        for run in _title_cjk_chunks(value)
        for index in range(len(run) - 1)
    }


def _title_source_cjk_bigrams(value: str) -> set[str]:
    normalized = _normalize_title_text(value)
    source_bigrams = _title_cjk_bigrams(normalized)
    for aliases in _TITLE_CJK_ALIAS_GROUPS.values():
        if any(_normalize_title_text(alias) in normalized for alias in aliases):
            for alias in aliases:
                source_bigrams.update(_title_cjk_bigrams(alias))
    return source_bigrams


def _title_unmatched_cjk_chunks(
    *,
    value: str,
    source_bigrams: set[str],
) -> set[str]:
    unmatched: set[str] = set()
    for run in _title_cjk_chunks(value):
        covered = [False] * len(run)
        for index in range(len(run) - 1):
            if run[index : index + 2] in source_bigrams:
                covered[index] = True
                covered[index + 1] = True
        start: Optional[int] = None
        for index, is_covered in enumerate(covered + [True]):
            if not is_covered and start is None:
                start = index
            elif is_covered and start is not None:
                chunk = run[start:index]
                if chunk and not (
                    len(chunk) == 1
                    and chunk in {character for bigram in source_bigrams for character in bigram}
                ):
                    unmatched.add(chunk)
                start = None
    return unmatched


def _is_title_high_specificity_token(*, raw_token: str, canonical: str) -> bool:
    return (
        canonical in _TITLE_HIGH_SPECIFICITY_ENGLISH_TOKENS
        or any(character.isupper() for character in raw_token)
        or any(character.isdigit() for character in raw_token)
        or any(symbol in raw_token for symbol in ("_", ".", "/", ":", "#", "+", "-"))
    )


def _analyze_title_grounding(*, value: str, source_text: str) -> _TitleGroundingAnalysis:
    normalized_value = _normalize_title_text(value)
    normalized_source = _normalize_title_source_text(source_text)
    if normalized_value in _GENERIC_SCREENSHOT_TITLES:
        return _TitleGroundingAnalysis(
            title_failure_reason=TITLE_FAILURE_REASON_GENERIC_ONLY,
        )

    value_tokens = _title_ascii_tokens(normalized_value)
    source_tokens = _title_ascii_tokens(normalized_source)
    value_atoms = _technical_atoms(normalized_value)
    source_atoms = _technical_atoms(normalized_source)
    value_numbers = set(_NUMBER_PATTERN.findall(normalized_value))
    source_numbers = set(_NUMBER_PATTERN.findall(normalized_source))
    source_bigrams = _title_source_cjk_bigrams(normalized_source)
    value_bigrams = _title_cjk_bigrams(normalized_value)
    matched_cjk = value_bigrams & source_bigrams
    unmatched_cjk = _title_unmatched_cjk_chunks(
        value=normalized_value,
        source_bigrams=source_bigrams,
    )
    value_high_signal_cjk = {
        phrase
        for phrase in _CJK_HIGH_SIGNAL_TECHNICAL_PHRASES
        if phrase in normalized_value
    }
    source_high_signal_cjk = {
        phrase
        for phrase in _CJK_HIGH_SIGNAL_TECHNICAL_PHRASES
        if phrase in normalized_source
    }
    matched_high_signal_cjk = value_high_signal_cjk & source_high_signal_cjk
    unmatched_high_signal_cjk = value_high_signal_cjk - source_high_signal_cjk
    high_signal_bigrams = {
        bigram
        for phrase in value_high_signal_cjk
        for index in range(len(phrase) - 1)
        for bigram in (phrase[index : index + 2],)
    }
    matched_general_cjk = matched_cjk - high_signal_bigrams
    unmatched_general_cjk = unmatched_cjk - value_high_signal_cjk

    value_identifier_atoms = value_atoms - value_numbers
    source_identifier_atoms = source_atoms - source_numbers
    matched_atoms = value_identifier_atoms & source_identifier_atoms
    unmatched_atoms = value_identifier_atoms - source_identifier_atoms
    matched_numbers = value_numbers & source_numbers
    unmatched_numbers = value_numbers - source_numbers

    value_product_tokens = {
        canonical
        for canonical in value_tokens
        if canonical in _TITLE_PRODUCT_NAME_TOKENS
    }
    source_product_tokens = {
        canonical
        for canonical in source_tokens
        if canonical in _TITLE_PRODUCT_NAME_TOKENS
    }
    value_high_technical_tokens = {
        canonical
        for canonical, raw_token in value_tokens.items()
        if canonical not in _TITLE_PRODUCT_NAME_TOKENS
        and _is_title_high_specificity_token(
            raw_token=raw_token,
            canonical=canonical,
        )
    }
    source_high_technical_tokens = {
        canonical
        for canonical, raw_token in source_tokens.items()
        if canonical not in _TITLE_PRODUCT_NAME_TOKENS
        and _is_title_high_specificity_token(
            raw_token=raw_token,
            canonical=canonical,
        )
    }
    matched_product_tokens = value_product_tokens & source_product_tokens
    unmatched_product_tokens = value_product_tokens - source_product_tokens
    matched_high_technical_tokens = (
        value_high_technical_tokens & source_high_technical_tokens
    )
    unmatched_high_technical_tokens = (
        value_high_technical_tokens - source_high_technical_tokens
    )

    value_general_ascii = (
        set(value_tokens)
        - value_product_tokens
        - value_high_technical_tokens
    )
    matched_general_ascii = (
        set(value_tokens)
        & set(source_tokens)
        - value_product_tokens
        - value_high_technical_tokens
    )
    unmatched_general_ascii = (
        set(value_tokens)
        - set(source_tokens)
        - value_product_tokens
        - value_high_technical_tokens
    )

    matched_technical_identifiers = (
        matched_atoms
        | matched_high_technical_tokens
        | {f"cjk:{phrase}" for phrase in matched_high_signal_cjk}
    )
    unmatched_technical_identifiers = (
        unmatched_atoms | unmatched_high_technical_tokens
        | {f"cjk:{phrase}" for phrase in unmatched_high_signal_cjk}
    )
    matched_high_specificity = (
        len(matched_product_tokens)
        + len(matched_technical_identifiers)
    )
    unmatched_high_specificity = (
        len(unmatched_product_tokens)
        + len(unmatched_technical_identifiers)
    )
    matched_general = len(matched_general_ascii) + len(matched_general_cjk)
    unmatched_general = len(unmatched_general_ascii) + len(unmatched_general_cjk)

    candidate_keys = {
        *(f"product:{token}" for token in value_product_tokens),
        *(f"identifier:{identifier}" for identifier in value_identifier_atoms),
        *(f"word:{token}" for token in value_high_technical_tokens),
        *(f"word:{token}" for token in value_general_ascii),
        *(f"number:{number}" for number in value_numbers),
        *(f"cjk:{anchor}" for anchor in matched_general_cjk),
        *(f"cjk-unmatched:{anchor}" for anchor in unmatched_general_cjk),
        *(f"technical-cjk:{anchor}" for anchor in value_high_signal_cjk),
    }
    matched_keys = {
        *(f"product:{token}" for token in matched_product_tokens),
        *(f"identifier:{identifier}" for identifier in matched_atoms),
        *(f"word:{token}" for token in matched_high_technical_tokens),
        *(f"word:{token}" for token in matched_general_ascii),
        *(f"number:{number}" for number in matched_numbers),
        *(f"cjk:{anchor}" for anchor in matched_general_cjk),
        *(f"technical-cjk:{anchor}" for anchor in matched_high_signal_cjk),
    }
    unmatched_keys = candidate_keys - matched_keys
    has_unmatched_semantics = _introduces_new_title_semantics(
        value=value,
        source_text=source_text,
    )
    has_sufficient_matched_anchors = bool(
        matched_high_specificity or matched_general >= 2
    )
    # CJK paraphrase bigrams are intentionally advisory. Once all products,
    # technical identifiers, and numbers are source-supported, one
    # high-specificity anchor or two general anchors is enough. Unknown ASCII
    # nouns remain strict because they are more likely to be new domain terms.
    supported = (
        has_sufficient_matched_anchors
        and not unmatched_product_tokens
        and not unmatched_technical_identifiers
        and not unmatched_numbers
        and not unmatched_general_ascii
        and not has_unmatched_semantics
    )

    title_failure_reason: Optional[str] = None
    if not supported:
        if unmatched_numbers:
            title_failure_reason = TITLE_FAILURE_REASON_UNMATCHED_NUMBER_OR_VERSION
        elif unmatched_product_tokens:
            title_failure_reason = TITLE_FAILURE_REASON_UNMATCHED_PRODUCT_NAME
        elif unmatched_technical_identifiers:
            title_failure_reason = TITLE_FAILURE_REASON_UNMATCHED_TECHNICAL_IDENTIFIER
        elif not matched_high_specificity and not matched_general:
            title_failure_reason = TITLE_FAILURE_REASON_NO_USABLE_ANCHOR
        elif not has_sufficient_matched_anchors or unmatched_general_ascii:
            title_failure_reason = TITLE_FAILURE_REASON_INSUFFICIENT_MATCHED_ANCHORS
        elif has_unmatched_semantics:
            title_failure_reason = TITLE_FAILURE_REASON_INSUFFICIENT_MATCHED_ANCHORS
        else:
            title_failure_reason = TITLE_FAILURE_REASON_OCR_NORMALIZATION_MISMATCH

    return _TitleGroundingAnalysis(
        title_anchor_count=len(candidate_keys),
        matched_title_anchor_count=len(matched_keys),
        unmatched_title_anchor_count=len(unmatched_keys),
        numeric_anchor_count=len(value_numbers),
        unmatched_numeric_anchor_count=len(unmatched_numbers),
        matched_high_specificity_anchor_count=matched_high_specificity,
        unmatched_high_specificity_anchor_count=unmatched_high_specificity,
        matched_general_anchor_count=matched_general,
        unmatched_general_anchor_count=unmatched_general,
        unmatched_general_ascii_count=len(unmatched_general_ascii),
        matched_technical_identifier_count=len(matched_technical_identifiers),
        unmatched_technical_identifier_count=len(unmatched_technical_identifiers),
        title_failure_reason=title_failure_reason,
        supported=supported,
    )


def _has_title_source_anchor(*, value: str, source_text: str) -> bool:
    return _analyze_title_grounding(value=value, source_text=source_text).supported


def _has_partial_title_anchor(*, value: str, source_text: str) -> bool:
    """Allow fallback only when the failed title is still on the source topic."""
    analysis = _analyze_title_grounding(value=value, source_text=source_text)
    return _is_title_fallback_eligible_from_analysis(analysis)


def _is_title_fallback_eligible_from_analysis(
    analysis: _TitleGroundingAnalysis,
) -> bool:
    if analysis.title_failure_reason not in {
        TITLE_FAILURE_REASON_INSUFFICIENT_MATCHED_ANCHORS,
        TITLE_FAILURE_REASON_OCR_NORMALIZATION_MISMATCH,
        TITLE_FAILURE_REASON_UNMATCHED_PRODUCT_NAME,
    }:
        return False
    if analysis.matched_title_anchor_count == 0:
        return False
    if analysis.unmatched_technical_identifier_count:
        return False
    if analysis.unmatched_numeric_anchor_count:
        return False
    if analysis.unmatched_general_ascii_count:
        return False
    if analysis.title_failure_reason == TITLE_FAILURE_REASON_UNMATCHED_PRODUCT_NAME:
        # A source-supported high-specificity anchor identifies the topic; an
        # unmatched product can then be removed by a source-only fallback.
        return analysis.matched_high_specificity_anchor_count > 0
    return (
        analysis.unmatched_high_specificity_anchor_count == 0
        and analysis.matched_title_anchor_count > 0
    )


def is_screenshot_title_fallback_eligible(
    diagnostics: Dict[str, object],
) -> bool:
    """Apply the bounded title-repair/fallback decision policy to diagnostics."""

    reason = diagnostics.get("title_failure_reason")
    if reason not in {
        TITLE_FAILURE_REASON_INSUFFICIENT_MATCHED_ANCHORS,
        TITLE_FAILURE_REASON_OCR_NORMALIZATION_MISMATCH,
        TITLE_FAILURE_REASON_UNMATCHED_PRODUCT_NAME,
    }:
        return False
    matched_title = int(diagnostics.get("matched_title_anchor_count", 0) or 0)
    matched_high = int(
        diagnostics.get("matched_high_specificity_anchor_count", 0) or 0
    )
    unmatched_high = int(
        diagnostics.get("unmatched_high_specificity_anchor_count", 0) or 0
    )
    unmatched_technical = int(
        diagnostics.get("unmatched_technical_identifier_count", 0) or 0
    )
    unmatched_numeric = int(
        diagnostics.get("unmatched_numeric_anchor_count", 0) or 0
    )
    unmatched_general_ascii = int(
        diagnostics.get("unmatched_general_ascii_count", 0) or 0
    )
    if (
        matched_title == 0
        or unmatched_technical
        or unmatched_numeric
        or unmatched_general_ascii
    ):
        return False
    if reason == TITLE_FAILURE_REASON_UNMATCHED_PRODUCT_NAME:
        return matched_high > 0
    return matched_high > 0 or (
        unmatched_high == 0
        and matched_title > 0
    )


def build_screenshot_title_anchor_allowlist(
    source_snapshot: ScreenshotSourceSnapshot,
) -> str:
    """Extract a bounded, source-only title vocabulary for title repair."""

    source_text = _IMAGE_SECTION_MARKER_PATTERN.sub(" ", source_snapshot.text)
    frame_tokens = {_canonical_token(token) for token in _PROPOSAL_FRAME_TOKENS}
    safe_tokens = {_canonical_token(token) for token in _SAFE_PARAPHRASE_TOKENS}
    anchors: list[str] = []
    seen: set[str] = set()

    def add_anchor(anchor: str) -> None:
        normalized = _normalize_title_text(anchor)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        anchors.append(anchor)

    # Preserve technical spellings and topic nouns exactly as OCR produced
    # them. The cap keeps repair input bounded even for a long screenshot batch.
    for raw_token in _ASCII_TOKEN_PATTERN.findall(source_text):
        canonical = _canonical_token(raw_token)
        if (
            canonical
            and canonical not in frame_tokens
            and canonical not in safe_tokens
            and not _is_browser_chrome_line(raw_token)
        ):
            add_anchor(raw_token.strip(".,!?;:"))
        if len(anchors) >= 24:
            break

    if len(anchors) < 24:
        for atom in sorted(_technical_atoms(_normalize_title_text(source_text))):
            add_anchor(atom)
            if len(anchors) >= 24:
                break

    if len(anchors) < 24:
        for cjk_chunk in _title_cjk_chunks(source_text):
            add_anchor(cjk_chunk[:24])
            if len(anchors) >= 24:
                break

    return "\n".join(f"- {anchor}" for anchor in anchors)


def build_screenshot_fallback_title(source_text: str) -> str:
    """Build a grounded title from OCR headings/keywords without an LLM."""

    cleaned_source = _IMAGE_SECTION_MARKER_PATTERN.sub(
        " ",
        preprocess_screenshot_ocr_text(source_text),
    )
    for raw_line in cleaned_source.splitlines():
        candidate = re.sub(r"\s+", " ", raw_line).strip(" -–—:;|")
        if not candidate or re.match(r"^\[image\s+\d+\]$", candidate, re.I):
            continue
        if len(candidate) > 100 or re.search(r"[.!?。！？]$", candidate):
            continue
        if _has_title_source_anchor(value=candidate, source_text=cleaned_source):
            return candidate

    source_lines = [line for line in cleaned_source.splitlines() if line.strip()]
    technical_terms: list[str] = []
    content_terms: list[str] = []
    cjk_terms: list[str] = []
    frame_tokens = {
        _canonical_token(token) for token in _PROPOSAL_FRAME_TOKENS
    }
    safe_tokens = {
        _canonical_token(token) for token in _SAFE_PARAPHRASE_TOKENS
    }
    for line in source_lines:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_+#./:-]*", line):
            display_token = token.strip(".,!?;:")
            canonical = _canonical_token(token)
            if canonical in {
                _canonical_token(item) for item in technical_terms + content_terms
            }:
                continue
            if canonical not in frame_tokens and canonical not in safe_tokens:
                content_terms.append(display_token)
            if (
                any(character.isupper() for character in token[1:])
                or token.isupper()
                or any(symbol in token for symbol in ("#", "+", ".", "/", "-"))
            ):
                technical_terms.append(display_token)
        for run in _CJK_RUN_PATTERN.findall(line):
            if len(run) >= 2 and run not in cjk_terms:
                cjk_terms.append(run[:24])

    candidates: list[str] = []
    seen_candidates: set[str] = set()
    for candidate in technical_terms[:4] + content_terms[:4] + cjk_terms[:4]:
        candidate_key = _normalize_title_text(candidate)
        if candidate_key and candidate_key not in seen_candidates:
            seen_candidates.add(candidate_key)
            candidates.append(candidate)
    fallback = "、".join(candidates)
    if fallback:
        return fallback
    return "Screenshot source proposal"


def _meaningful_tokens(value: str) -> List[str]:
    tokens = [
        token.casefold().strip(".,!?;:()[]{}\"'")
        for token in _ASCII_TOKEN_PATTERN.findall(value)
    ]
    return [
        token
        for token in tokens
        if len(re.sub(r"\W", "", token)) >= 2
        or any(symbol in token for symbol in ("+", "#"))
    ]


def _has_source_evidence(*, value: str, source_normalized: str) -> bool:
    """Return whether a proposal item is source-grounded.

    This is deliberately a conservative lexical contract rather than an
    LLM judge. It permits common reporting words and a small synonym lexicon,
    while requiring an anchor from the OCR and exact preservation of numbers,
    commands, URLs, and technical atoms. That combination catches invented
    details without requiring every summary sentence to copy OCR wording.
    """

    normalized_value = _normalize_for_grounding(value)
    normalized_source = _normalize_for_grounding(source_normalized)

    source_numbers = set(_NUMBER_PATTERN.findall(normalized_source))
    value_numbers = set(_NUMBER_PATTERN.findall(normalized_value))
    if not value_numbers.issubset(source_numbers):
        return False

    source_atoms = _technical_atoms(normalized_source)
    value_atoms = _technical_atoms(normalized_value)
    if not value_atoms.issubset(source_atoms):
        return False

    source_tokens = _canonical_token_set(normalized_source)
    value_tokens = _canonical_tokens(normalized_value)
    frame_tokens = {
        _canonical_token(token) for token in _PROPOSAL_FRAME_TOKENS
    }
    content_tokens = [
        token
        for token in value_tokens
        if token not in frame_tokens
    ]
    safe_paraphrase_tokens = {
        _canonical_token(token) for token in _SAFE_PARAPHRASE_TOKENS
    }
    unknown_tokens = [
        token
        for token in content_tokens
        if token not in source_tokens and token not in safe_paraphrase_tokens
    ]
    if unknown_tokens:
        # New product names, commands, or domain nouns are not treated as
        # paraphrases merely because another source token is present.
        return False

    exact_ascii_anchors = set(content_tokens) & source_tokens
    ascii_anchor = bool(exact_ascii_anchors)
    cjk_anchor = _has_cjk_anchor(
        value=normalized_value,
        source=normalized_source,
    )
    # Mixed Traditional Chinese/English claims often add neutral reporting
    # words around two or more preserved technical/source terms. Requiring two
    # exact anchors for that shape allows faithful paraphrase without accepting
    # a new domain noun that merely sits beside one familiar term.
    if _CJK_PATTERN.search(normalized_value) and _CJK_PATTERN.search(normalized_source):
        return cjk_anchor or (
            bool(exact_ascii_anchors) and len(exact_ascii_anchors) >= 2
        )
    if ascii_anchor and len(exact_ascii_anchors) >= 2:
        return True
    return ascii_anchor or cjk_anchor


def _has_bounded_context_marker(value: str) -> bool:
    return bool(_BOUNDED_CONTEXT_MARKER_PATTERN.search(_normalize_for_grounding(value)))


def _has_bounded_engineering_context(
    *,
    value: str,
    source_normalized: str,
    concepts: Iterable[str],
) -> bool:
    """Allow only a small, source-anchored engineering-context vocabulary."""

    if not _has_bounded_context_marker(value):
        return False
    normalized_value = _normalize_for_grounding(value)
    normalized_source = _normalize_for_grounding(source_normalized)
    if _ABSOLUTE_OR_DESTRUCTIVE_CONTEXT_PATTERN.search(normalized_value):
        return False

    concept_values = tuple(_normalize_for_grounding(concept) for concept in concepts)
    if not any(
        _has_cjk_anchor(value=normalized_value, source=concept)
        or bool(
            set(_canonical_tokens(normalized_value))
            & set(_canonical_tokens(concept))
        )
        for concept in concept_values
    ):
        return False

    # The recommended note shape may start with ``<Concept>：``. Treat that
    # label as structure so the colon is not mistaken for a new identifier.
    content_value = _strip_bounded_context_labels(normalized_value)
    source_atoms = _technical_atoms(normalized_source)
    if not _technical_atoms(content_value).issubset(source_atoms):
        return False

    source_tokens = _canonical_token_set(normalized_source)
    concept_tokens = {
        token for concept in concept_values for token in _canonical_tokens(concept)
    }
    allowed_tokens = {
        _canonical_token(token) for token in _PROPOSAL_FRAME_TOKENS
    }
    allowed_tokens.update(_canonical_token(token) for token in _SAFE_PARAPHRASE_TOKENS)
    allowed_tokens.update(_canonical_token(token) for token in _BOUNDED_CONTEXT_ASCII_TOKENS)
    unknown_tokens = (
        set(_canonical_tokens(content_value))
        - source_tokens
        - concept_tokens
        - allowed_tokens
    )
    if unknown_tokens:
        return False

    for raw_token in _ASCII_TOKEN_PATTERN.findall(content_value):
        canonical = _canonical_token(raw_token)
        if (
            canonical
            and canonical not in source_tokens
            and canonical not in concept_tokens
            and canonical not in allowed_tokens
            and (
                any(character.isupper() for character in raw_token)
                or any(character.isdigit() for character in raw_token)
                or any(symbol in raw_token for symbol in ("_", ".", "/", ":", "#", "+", "-"))
            )
        ):
            return False

    residual = normalized_value
    cjk_phrases = {
        *_BOUNDED_CONTEXT_CJK_PHRASES,
        *(_CJK_RUN_PATTERN.findall(normalized_source)),
        *(run for concept in concept_values for run in _CJK_RUN_PATTERN.findall(concept)),
    }
    for phrase in sorted(cjk_phrases, key=len, reverse=True):
        residual = residual.replace(phrase, "")
    return not _CJK_PATTERN.search(residual)


def _concept_coverage(
    *,
    concepts: Iterable[str],
    notes: Iterable[str],
) -> tuple[set[int], tuple[int, ...]]:
    concept_values = tuple(concepts)
    note_values = tuple(
        _strip_bounded_context_labels(_normalize_for_grounding(note))
        for note in notes
    )
    covered: set[int] = set()
    for index, concept in enumerate(concept_values):
        normalized_concept = _normalize_for_grounding(concept)
        concept_tokens = set(_canonical_tokens(normalized_concept))
        if any(
            bool(concept_tokens & set(_canonical_tokens(note)))
            or _has_cjk_anchor(value=normalized_concept, source=note)
            for note in note_values
        ):
            covered.add(index)
    return covered, tuple(
        index for index in range(len(concept_values)) if index not in covered
    )


def _strip_bounded_context_labels(value: str) -> str:
    value = re.sub(r"^[^:：\n]{1,80}[:：]\s*", "", value)
    return re.sub(
        r"(?<=[.!?。！？])\s*[^:：\n]{1,80}[:：]\s*",
        " ",
        value,
    )


def _duplicate_note_indices(notes: Iterable[str]) -> tuple[int, ...]:
    normalized_notes = [
        set(
            _canonical_tokens(
                _strip_bounded_context_labels(_normalize_for_grounding(note))
            )
        )
        for note in notes
    ]
    duplicates: set[int] = set()
    for index, tokens in enumerate(normalized_notes):
        if not tokens:
            continue
        for previous_index in range(index):
            previous = normalized_notes[previous_index]
            if previous == tokens:
                duplicates.add(index)
                break
            overlap = len(tokens & previous) / max(1, len(tokens | previous))
            if overlap >= 0.9:
                duplicates.add(index)
                break
    return tuple(sorted(duplicates))


def _analyze_claim_grounding(
    *,
    value: str,
    source_normalized: str,
    concepts: Iterable[str] = (),
) -> _ClaimGroundingAnalysis:
    """Classify one non-title claim without retaining its source text."""

    normalized_value = _normalize_for_grounding(value)
    normalized_source = _normalize_for_grounding(source_normalized)
    exact_ascii_anchors, cjk_anchors, unmatched_general_tokens = (
        _claim_lexical_evidence(
            value=normalized_value,
            source_normalized=normalized_source,
        )
    )

    def analysis(*, matched: bool, reason: Optional[str] = None) -> _ClaimGroundingAnalysis:
        return _ClaimGroundingAnalysis(
            matched=matched,
            reason=reason,
            exact_ascii_anchors=exact_ascii_anchors,
            cjk_anchors=cjk_anchors,
            unmatched_general_tokens=unmatched_general_tokens,
        )

    value_numbers = set(_NUMBER_PATTERN.findall(normalized_value))
    source_numbers = set(_NUMBER_PATTERN.findall(normalized_source))
    if not value_numbers.issubset(source_numbers):
        return analysis(
            matched=False,
            reason=SUMMARY_GROUNDING_REASON_NEW_NUMBER_OR_VERSION,
        )

    if _introduces_new_advice(
        value=value,
        source_normalized=source_normalized,
    ):
        return analysis(
            matched=False,
            reason=SUMMARY_GROUNDING_REASON_UNSUPPORTED_ADVICE,
        )
    if _introduces_new_comparison(
        value=value,
        source_normalized=source_normalized,
    ):
        return analysis(
            matched=False,
            reason=SUMMARY_GROUNDING_REASON_UNSUPPORTED_COMPARISON,
        )
    if _introduces_new_conclusion(
        value=value,
        source_normalized=source_normalized,
    ):
        return analysis(
            matched=False,
            reason=SUMMARY_GROUNDING_REASON_UNSUPPORTED_RESULT,
        )

    value_atoms = _technical_atoms(normalized_value)
    source_atoms = _technical_atoms(normalized_source)
    if _has_bounded_engineering_context(
        value=value,
        source_normalized=source_normalized,
        concepts=concepts,
    ) and not _has_new_high_signal_cjk_phrase(
        value=normalized_value,
        source_normalized=normalized_source,
    ):
        return analysis(matched=True)

    if not value_atoms.issubset(source_atoms):
        return analysis(
            matched=False,
            reason=SUMMARY_GROUNDING_REASON_NEW_TECHNICAL_IDENTIFIER,
        )

    if _has_new_high_signal_identifier(
            value=value,
            source_normalized=source_normalized,
        ) or _has_new_high_signal_cjk_phrase(
            value=normalized_value,
            source_normalized=normalized_source,
        ):
        return analysis(
            matched=False,
            reason=SUMMARY_GROUNDING_REASON_NEW_TECHNICAL_IDENTIFIER,
        )

    if _has_source_evidence(value=value, source_normalized=source_normalized):
        return analysis(matched=True)
    if _has_bounded_engineering_context(
        value=value,
        source_normalized=source_normalized,
        concepts=concepts,
    ):
        return analysis(matched=True)
    if not _has_claim_source_anchor(
        value=normalized_value,
        source_normalized=normalized_source,
    ):
        return analysis(
            matched=False,
            reason=SUMMARY_GROUNDING_REASON_INSUFFICIENT_SOURCE_ANCHORS,
        )
    return analysis(
        matched=False,
        reason=SUMMARY_GROUNDING_REASON_PARAPHRASE_NOT_GROUNDED,
    )


def _claim_lexical_evidence(
    *,
    value: str,
    source_normalized: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return bounded lexical evidence for private and redacted diagnostics."""

    frame_tokens = {
        _canonical_token(token) for token in _PROPOSAL_FRAME_TOKENS
    }
    safe_tokens = {
        _canonical_token(token) for token in _SAFE_PARAPHRASE_TOKENS
    }
    source_tokens = _canonical_token_set(source_normalized)
    content_tokens = set(_canonical_tokens(value)) - frame_tokens
    exact_ascii_anchors = tuple(sorted(content_tokens & source_tokens))
    unmatched_general_tokens = tuple(
        sorted(content_tokens - source_tokens - safe_tokens)
    )

    expanded_value = _expand_cjk_aliases(value)
    expanded_source = _expand_cjk_aliases(source_normalized)
    value_bigrams = {
        run[index : index + 2]
        for run in _CJK_RUN_PATTERN.findall(expanded_value)
        for index in range(len(run) - 1)
    }
    source_bigrams = {
        run[index : index + 2]
        for run in _CJK_RUN_PATTERN.findall(expanded_source)
        for index in range(len(run) - 1)
    }
    cjk_anchors = tuple(sorted(value_bigrams & source_bigrams))
    return exact_ascii_anchors, cjk_anchors, unmatched_general_tokens


def _has_new_high_signal_identifier(*, value: str, source_normalized: str) -> bool:
    source_tokens = _canonical_token_set(source_normalized)
    frame_tokens = {
        _canonical_token(token) for token in _PROPOSAL_FRAME_TOKENS
    }
    safe_tokens = {
        _canonical_token(token) for token in _SAFE_PARAPHRASE_TOKENS
    }
    for raw_token in _ASCII_TOKEN_PATTERN.findall(value):
        canonical = _canonical_token(raw_token)
        if (
            canonical
            and canonical not in source_tokens
            and canonical not in frame_tokens
            and canonical not in safe_tokens
            and (
                any(character.isupper() for character in raw_token)
                or any(character.isdigit() for character in raw_token)
                or any(symbol in raw_token for symbol in ("_", ".", "/", ":", "#", "+", "-"))
            )
        ):
            return True
    return False


def _has_new_high_signal_cjk_phrase(*, value: str, source_normalized: str) -> bool:
    return any(
        phrase in value and phrase not in source_normalized
        for phrase in _CJK_HIGH_SIGNAL_TECHNICAL_PHRASES
    )


def _has_claim_source_anchor(*, value: str, source_normalized: str) -> bool:
    value_tokens = set(_canonical_tokens(value)) - {
        _canonical_token(token) for token in _PROPOSAL_FRAME_TOKENS
    }
    source_tokens = _canonical_token_set(source_normalized)
    if value_tokens & source_tokens:
        return True
    return _has_cjk_anchor(value=value, source=source_normalized)


def _canonical_token(token: str) -> str:
    normalized = token.casefold().strip(".,!?;:()[]{}\"'")
    if not normalized:
        return normalized
    if normalized in _SYNONYM_TO_CANONICAL:
        return _SYNONYM_TO_CANONICAL[normalized]
    if len(normalized) > 5 and normalized.endswith("ies"):
        return normalized[:-3] + "y"
    if len(normalized) > 5 and normalized.endswith("ing"):
        return normalized[:-3].rstrip("e")
    if len(normalized) > 4 and normalized.endswith("ed"):
        return normalized[:-2].rstrip("e")
    if len(normalized) > 4 and normalized.endswith("s"):
        return normalized[:-1]
    return normalized


def _canonical_tokens(value: str) -> List[str]:
    return [_canonical_token(token) for token in _meaningful_tokens(value)]


def _canonical_token_set(value: str) -> set[str]:
    return set(_canonical_tokens(value))


def _technical_atoms(value: str) -> set[str]:
    return {
        atom.casefold().strip(".,!?;:()[]{}\"'")
        for atom in _TECHNICAL_ATOM_PATTERN.findall(value)
        if atom.strip(".,!?;:()[]{}\"'")
    }


def _has_cjk_anchor(*, value: str, source: str) -> bool:
    value = _expand_cjk_aliases(value)
    source = _expand_cjk_aliases(source)
    value_runs = _CJK_RUN_PATTERN.findall(value)
    source_runs = _CJK_RUN_PATTERN.findall(source)
    if not value_runs or not source_runs:
        return False

    value_bigrams = {
        run[index : index + 2]
        for run in value_runs
        for index in range(len(run) - 1)
    }
    source_bigrams = {
        run[index : index + 2]
        for run in source_runs
        for index in range(len(run) - 1)
    }
    if value_bigrams & source_bigrams:
        return True

    # A two-character overlap still supports a short Traditional Chinese
    # concept when OCR punctuation or word segmentation differs.
    value_characters = set(_CJK_PATTERN.findall(value))
    source_characters = set(_CJK_PATTERN.findall(source))
    return len(value_characters & source_characters) >= 2


def _expand_cjk_aliases(value: str) -> str:
    expanded = value
    for aliases in _TITLE_CJK_ALIAS_GROUPS.values():
        if any(alias in value for alias in aliases):
            expanded = f"{expanded} {' '.join(aliases)}"
    return expanded


def _introduces_new_advice(*, value: str, source_normalized: str) -> bool:
    normalized_value = _normalize_for_grounding(value)
    normalized_source = _normalize_for_grounding(source_normalized)
    if normalized_value == "use":
        return False
    if not _ADVICE_PATTERN.search(normalized_value):
        return False
    return not _ADVICE_PATTERN.search(normalized_source)


def _introduces_new_comparison(*, value: str, source_normalized: str) -> bool:
    normalized_value = _normalize_for_grounding(value)
    normalized_source = _normalize_for_grounding(source_normalized)
    if not _COMPARISON_PATTERN.search(normalized_value):
        return False
    return not _COMPARISON_PATTERN.search(normalized_source)


def _introduces_new_conclusion(*, value: str, source_normalized: str) -> bool:
    normalized_value = _normalize_for_grounding(value)
    normalized_source = _normalize_for_grounding(source_normalized)
    if not _CONCLUSION_PATTERN.search(normalized_value):
        return False
    return not _CONCLUSION_PATTERN.search(normalized_source)


def _contains_title_semantic_term(*, value: str, term: str) -> bool:
    if re.fullmatch(r"[a-z]+", term):
        return bool(re.search(rf"\b{re.escape(term)}\b", value))
    return term in value


def _introduces_new_title_semantics(*, value: str, source_text: str) -> bool:
    normalized_value = _normalize_title_text(value)
    normalized_source = _normalize_title_source_text(source_text)
    for group in _TITLE_SEMANTIC_GROUPS:
        value_has_term = any(
            _contains_title_semantic_term(value=normalized_value, term=term)
            for term in group[1:]
        )
        source_has_term = any(
            _contains_title_semantic_term(value=normalized_source, term=term)
            for term in group[1:]
        )
        if value_has_term and not source_has_term:
            return True
    return False


def _validate_output_language(
    proposal: SupplementProposalSchema,
    language: ScreenshotLanguage,
) -> None:
    from src.orchestrators.supplement_proposal_schema import (
        SupplementProposalValidationError,
    )

    natural_text = " ".join(
        [proposal.title, proposal.summary, *proposal.concepts, *proposal.notes]
    )
    if language.code != "zh-Hant":
        return
    if len(_CJK_PATTERN.findall(natural_text)) < 4:
        raise SupplementProposalValidationError(
            "Chinese screenshot proposal must use Traditional Chinese"
        )
    if not _CJK_PATTERN.search(proposal.title) or not _CJK_PATTERN.search(
        proposal.summary
    ):
        raise SupplementProposalValidationError(
            "Chinese screenshot proposal must use Traditional Chinese"
        )
    if any(not _CJK_PATTERN.search(note) for note in proposal.notes):
        raise SupplementProposalValidationError(
            "Chinese screenshot proposal must use Traditional Chinese"
        )
    for simplified, traditional in _TRADITIONAL_OUTPUT_REQUIRED:
        if simplified in natural_text:
            raise SupplementProposalValidationError(
                "Chinese screenshot proposal must use Traditional Chinese"
            )
