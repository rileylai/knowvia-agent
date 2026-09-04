from __future__ import annotations

from src.services import (
    PROMPT_ID_QA_ANSWER,
    PROMPT_ID_SCREENSHOT_BODY_REPAIR,
    PROMPT_ID_SCREENSHOT_SUMMARY_REPAIR,
    PROMPT_ID_SCREENSHOT_TITLE_REPAIR,
    PROMPT_ID_SUPPLEMENT_PROPOSAL,
    PromptTemplateLoader,
)


def test_prompt_template_loader_loads_and_renders_qa_prompt() -> None:
    loader = PromptTemplateLoader()

    bundle = loader.load_bundle(PROMPT_ID_QA_ANSWER)
    system_message, user_message = bundle.render_messages(
        variables={
            "query": "What does positional encoding do?",
            "context_text": "[C1] path=Knowledge/NLP/Week5\nPositional encoding adds order.",
        }
    )

    assert bundle.prompt_id == PROMPT_ID_QA_ANSWER
    assert bundle.version == "qa_answer_v3"
    assert "Answer only from the provided context." in system_message
    assert "INSUFFICIENT_INFO" in system_message
    assert "What does positional encoding do?" in user_message
    assert "Knowledge/NLP/Week5" in user_message


def test_prompt_template_loader_loads_and_renders_supplement_prompt() -> None:
    loader = PromptTemplateLoader()

    bundle = loader.load_bundle(PROMPT_ID_SUPPLEMENT_PROPOSAL)
    system_message, user_message = bundle.render_messages(
        variables={
            "source_type": "chat_text",
            "source_display_name": "chat-2026-06-17",
            "source_language": "English",
            "selected_target_path": "Knowledge/NLP/Week5/AI Supplement Zone",
            "source_text": "Source notes about residual connections and layer normalization.",
        }
    )

    assert bundle.prompt_id == PROMPT_ID_SUPPLEMENT_PROPOSAL
    assert bundle.version == "supplement_proposal_v7"
    assert bundle.path.name == "supplement_proposal_v7.md"
    assert "Return strict JSON only" in system_message
    assert "generated fields listed below" in system_message
    assert "source`, `target_path`" in system_message
    assert "source_type=chat_text" in user_message
    assert "chat-2026-06-17" in user_message
    assert "Knowledge/NLP/Week5/AI Supplement Zone" in user_message
    assert "SOURCE_LANGUAGE=English" in user_message


def test_prompt_template_loader_loads_title_repair_prompt() -> None:
    loader = PromptTemplateLoader()

    bundle = loader.load_bundle(PROMPT_ID_SCREENSHOT_TITLE_REPAIR)
    system_message, user_message = bundle.render_messages(
        variables={
            "source_language": "Traditional Chinese (繁體中文)",
            "failed_title": "[BEGIN UNTRUSTED FAILED_TITLE]\nRedis\n[END UNTRUSTED FAILED_TITLE]",
            "source_supported_title_anchors": "[BEGIN UNTRUSTED SOURCE_SUPPORTED_TITLE_ANCHORS]\n- MySQL\n- EXPLAIN\n[END UNTRUSTED SOURCE_SUPPORTED_TITLE_ANCHORS]",
            "source_text": "[BEGIN UNTRUSTED SOURCE_TEXT]\nMySQL EXPLAIN\n[END UNTRUSTED SOURCE_TEXT]",
        }
    )

    assert bundle.prompt_id == PROMPT_ID_SCREENSHOT_TITLE_REPAIR
    assert bundle.version == "screenshot_title_repair_v3"
    assert bundle.path.name == "screenshot_title_repair_v3.md"
    assert "exactly one field" in system_message
    assert "MySQL EXPLAIN" in user_message


def test_prompt_template_loader_loads_summary_repair_prompt() -> None:
    loader = PromptTemplateLoader()

    bundle = loader.load_bundle(PROMPT_ID_SCREENSHOT_SUMMARY_REPAIR)
    system_message, user_message = bundle.render_messages(
        variables={
            "source_language": "Traditional Chinese (繁體中文)",
            "failed_summary": "[BEGIN UNTRUSTED FAILED_SUMMARY]\n資料庫查詢\n[END UNTRUSTED FAILED_SUMMARY]",
            "source_text": "[BEGIN UNTRUSTED SOURCE_TEXT]\nMySQL EXPLAIN\n[END UNTRUSTED SOURCE_TEXT]",
        }
    )

    assert bundle.prompt_id == PROMPT_ID_SCREENSHOT_SUMMARY_REPAIR
    assert bundle.version == "screenshot_summary_repair_v2"
    assert "only the summary" in system_message
    assert "MySQL EXPLAIN" in user_message


def test_prompt_template_loader_loads_body_repair_prompt() -> None:
    loader = PromptTemplateLoader()

    bundle = loader.load_bundle(PROMPT_ID_SCREENSHOT_BODY_REPAIR)
    system_message, user_message = bundle.render_messages(
        variables={
            "source_language": "Traditional Chinese (繁體中文)",
            "failed_body": "[BEGIN UNTRUSTED FAILED_BODY]\n{}\n[END UNTRUSTED FAILED_BODY]",
            "source_text": "[BEGIN UNTRUSTED SOURCE_TEXT]\nMySQL EXPLAIN\n[END UNTRUSTED SOURCE_TEXT]",
        }
    )

    assert bundle.prompt_id == PROMPT_ID_SCREENSHOT_BODY_REPAIR
    assert bundle.version == "screenshot_body_repair_v2"
    assert "major key concept" in system_message
    assert "MySQL EXPLAIN" in user_message
