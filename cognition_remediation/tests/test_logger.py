import json
import logging
import os
import pytest


@pytest.mark.unit
def test_file_handler_writes_json(tmp_path):
    log_file = str(tmp_path / "test.log")

    # Import after tmp_path is ready so logger isn't cached with wrong path
    from app.shared.logger import get_logger

    # Use a unique name to avoid handler-cache collision with other tests
    logger = get_logger("test_file_handler_unique", log_file=log_file)
    logger.info("test_event", extra={"key": "value"})

    assert os.path.exists(log_file)
    with open(log_file) as f:
        line = f.readline()
    record = json.loads(line)
    assert record["message"] == "test_event"
    assert record["key"] == "value"


@pytest.mark.unit
def test_no_file_handler_when_log_file_omitted():
    from app.shared.logger import get_logger

    logger = get_logger("test_no_file_handler_unique")
    file_handlers = [h for h in logger.handlers if hasattr(h, "baseFilename")]
    assert file_handlers == []
