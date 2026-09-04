import pytest

from src.rag.chunker import ChunkerBlock, ChunkerPage, chunk_notion_page


def test_chunk_notion_page_respects_page_toggle_heading_child_page_boundaries() -> None:
    page = ChunkerPage(
        notion_page_id="page-nlp-week5",
        title="NLP Week 5",
        notion_path="Knowledge/NLP/Week5",
        blocks=[
            ChunkerBlock(
                notion_block_id="blk-intro",
                block_type="paragraph",
                content_text="Week overview",
                block_path="Knowledge/NLP/Week5",
            ),
            ChunkerBlock(
                notion_block_id="blk-heading",
                block_type="heading_2",
                content_text="Attention",
                block_path="Knowledge/NLP/Week5/Attention",
                children=[
                    ChunkerBlock(
                        notion_block_id="blk-attn-detail",
                        block_type="paragraph",
                        content_text="Scaled dot-product attention",
                        block_path="Knowledge/NLP/Week5/Attention/Scaled dot-product attention",
                    ),
                    ChunkerBlock(
                        notion_block_id="blk-toggle",
                        block_type="toggle",
                        content_text="Variants",
                        block_path="Knowledge/NLP/Week5/Attention/Variants",
                        children=[
                            ChunkerBlock(
                                notion_block_id="blk-child-page",
                                block_type="child_page",
                                content_text="Transformer Notes",
                                block_path="Knowledge/NLP/Week5/Attention/Variants/Transformer Notes",
                                children=[
                                    ChunkerBlock(
                                        notion_block_id="blk-child-leaf",
                                        block_type="paragraph",
                                        content_text="Encoder stack",
                                        block_path="Knowledge/NLP/Week5/Attention/Variants/Transformer Notes/Encoder stack",
                                    )
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )

    chunks = chunk_notion_page(page)

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3]
    assert [chunk.notion_path for chunk in chunks] == [
        "Knowledge/NLP/Week5",
        "Knowledge/NLP/Week5/Attention",
        "Knowledge/NLP/Week5/Attention/Variants",
        "Knowledge/NLP/Week5/Attention/Variants/Transformer Notes",
    ]
    assert chunks[0].chunk_text == "Week overview"
    assert chunks[1].chunk_text == "Attention\nScaled dot-product attention"
    assert chunks[2].chunk_text == "Variants"
    assert chunks[3].chunk_text == "Transformer Notes\nEncoder stack"
    assert chunks[1].citation_meta["notion_page_id"] == "page-nlp-week5"
    assert chunks[1].citation_meta["notion_page_path"] == "Knowledge/NLP/Week5"
    assert chunks[1].citation_meta["notion_block_ids"] == [
        "blk-heading",
        "blk-attn-detail",
    ]


def test_chunk_notion_page_splits_long_chunk_by_max_chunk_chars() -> None:
    page = ChunkerPage(
        notion_page_id="page-1",
        title="T",
        notion_path="Knowledge/Test",
        blocks=[
            ChunkerBlock(
                notion_block_id="blk-1",
                block_type="paragraph",
                content_text="alpha beta gamma",
                block_path="Knowledge/Test",
            ),
            ChunkerBlock(
                notion_block_id="blk-2",
                block_type="paragraph",
                content_text="delta epsilon zeta",
                block_path="Knowledge/Test",
            ),
        ],
    )

    chunks = chunk_notion_page(page, max_chunk_chars=20)

    assert len(chunks) == 2
    assert chunks[0].chunk_text == "alpha beta gamma"
    assert chunks[1].chunk_text == "delta epsilon zeta"
    assert chunks[0].notion_path == "Knowledge/Test"
    assert chunks[1].notion_path == "Knowledge/Test"


def test_chunk_notion_page_rejects_non_positive_chunk_size() -> None:
    page = ChunkerPage(
        notion_page_id="page-1",
        title="T",
        notion_path="Knowledge/Test",
        blocks=[],
    )

    with pytest.raises(ValueError):
        chunk_notion_page(page, max_chunk_chars=0)
