import pathlib

from cascade_cms.operation_logger import OperationLogger
from cascade_cms.redaction import mask_token

TOKEN = "supersecrettoken-abcd"


def test_mask_token_keeps_last_four():
    assert mask_token(TOKEN) == "****abcd"


def test_mask_token_short_tokens_fully_masked():
    assert mask_token("abcd") == "****"
    assert mask_token("ab") == "**"
    assert mask_token("") == ""


def test_network_headers_mask_authorization(tmp_path):
    logger = OperationLogger(server="test", debug_config={"log_dir": str(tmp_path)})
    logger._config["show_network_headers"] = True
    logger.log_network_headers(
        {"authorization": f"Bearer {TOKEN}", "Content-Type": "json"}, {}
    )
    handler = logger._file_logger.handlers[0]
    handler.flush()
    text = pathlib.Path(handler.baseFilename).read_text()
    assert TOKEN not in text
    assert "Bearer ****abcd" in text
    assert "Content-Type: json" in text
