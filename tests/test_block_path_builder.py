from src.rag import BlockPathNode, build_block_paths


def test_build_block_paths_handles_mixed_page_toggle_heading_and_child_page() -> None:
    blocks = [
        BlockPathNode(
            block_id="blk-heading",
            block_type="heading_2",
            content_text="Attention",
            children=[
                BlockPathNode(
                    block_id="blk-toggle",
                    block_type="toggle",
                    content_text="Scaled dot-product",
                    children=[
                        BlockPathNode(
                            block_id="blk-detail",
                            block_type="paragraph",
                            content_text="QK^T / sqrt(dk)",
                        )
                    ],
                ),
                BlockPathNode(
                    block_id="blk-child-page",
                    block_type="child_page",
                    content_text="Transformer Notes",
                    children=[
                        BlockPathNode(
                            block_id="blk-child-heading",
                            block_type="heading_3",
                            content_text="Encoder",
                        )
                    ],
                ),
            ],
        )
    ]

    snapshots = build_block_paths(page_path="Knowledge/NLP/Week5", blocks=blocks)

    heading = snapshots[0]
    assert heading.block_path == "Knowledge/NLP/Week5/Attention"

    toggle = heading.children[0]
    assert toggle.block_path == "Knowledge/NLP/Week5/Attention/Scaled dot-product"

    detail = toggle.children[0]
    assert (
        detail.block_path
        == "Knowledge/NLP/Week5/Attention/Scaled dot-product/QK^T / sqrt(dk)"
    )

    child_page = heading.children[1]
    assert child_page.block_path == "Knowledge/NLP/Week5/Attention/Transformer Notes"

    child_heading = child_page.children[0]
    assert (
        child_heading.block_path
        == "Knowledge/NLP/Week5/Attention/Transformer Notes/Encoder"
    )


def test_build_block_paths_inherits_parent_path_when_content_is_empty() -> None:
    blocks = [
        BlockPathNode(
            block_id="blk-heading",
            block_type="heading_2",
            content_text="Section A",
            children=[
                BlockPathNode(
                    block_id="blk-empty",
                    block_type="paragraph",
                    content_text="   ",
                )
            ],
        )
    ]

    snapshots = build_block_paths(page_path="Knowledge/NLP/Week5", blocks=blocks)

    heading = snapshots[0]
    empty_child = heading.children[0]
    assert heading.block_path == "Knowledge/NLP/Week5/Section A"
    assert empty_child.block_path == "Knowledge/NLP/Week5/Section A"
