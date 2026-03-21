"""테스트 패키지.

추가 예정(여기서 한 곳에만 두고 점차 옮기면 됨)
----------------------------------------
- ``conftest.py``
    - 공통 fixture: DB 세션, ``TestClient``, 빗썸 클라이언트 mock 등
    - ``pytest_plugins`` 로 플러그인 분리 시 이 모듈에서 로드
- 디렉터리
    - ``tests/unit/`` — DB·HTTP 없는 순수 로직
    - ``tests/integration/`` — API·DB 통합 (현재 루트 테스트와 동일 역할)
- 마커
    - ``pytest.ini`` / ``pyproject.toml`` 에 ``markers = slow: ...`` 등 정의 후 선택 실행
- pre-commit
    - ``.pre-commit-config.yaml`` 하단 주석에 ``pytest`` 로컬 훅 예시
    - DB는 CI에서만 두고 켜는 편이 안전
"""
