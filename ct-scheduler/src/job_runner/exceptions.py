"""잡 실행 중 발생하는 예외 (워커에서 Failed·재시도 분기에 사용)."""


class JobExecutionFailed(Exception):
    """일시적 처리 실패. `retry_cnt` 증가 후 `Failed`(재시도) 또는 `retry_max` 도달 시 `Error`."""
