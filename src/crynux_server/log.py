import logging
import os
from logging.handlers import RotatingFileHandler


def init(
    log_dir: str,
    log_level: str,
    max_bytes: int,
    backup_count: int,
    log_filename: str = "crynux-server.log",
    root: bool = False,
):
    stream_handler = logging.StreamHandler()

    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, log_filename)
    file_handler = RotatingFileHandler(
        log_file,
        encoding="utf-8",
        delay=True,
        maxBytes=max_bytes,
        backupCount=backup_count,
    )

    dt_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(
        "[{asctime}] [{levelname:<8}] {name}: {message}", dt_fmt, style="{"
    )

    stream_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    logger_names = []
    if root:
        logger_names.append(None)
    else:
        logger_names.append("crynux_server")

    for name in logger_names:
        logger = logging.getLogger(name)
        logger.addHandler(stream_handler)
        logger.addHandler(file_handler)
        logger.setLevel(log_level)
