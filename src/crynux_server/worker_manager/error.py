import re


class TaskCancelled(Exception):
    pass


class TaskError(Exception):
    error_type = "TaskError"

    def __init__(self, msg: str):
        self.msg = msg

    def __str__(self) -> str:
        return f"{self.error_type}, error msg:\n{self.msg}\n"


class TaskInvalid(TaskError):
    error_type = "TaskInvalid"


class TaskExecutionError(TaskError):
    error_type = "TaskExecutionError"


class TaskDownloadError(TaskError):
    error_type = "TaskDownloadError"


def is_task_invalid(stdout: str) -> bool:
    pattern = re.compile(r"Task args invalid")
    return pattern.search(stdout) is not None


def is_model_not_downloaded(stdout: str) -> bool:
    return "Task model not downloaded" in stdout
