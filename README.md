# autocoin

빗썸 연동 자동매매 서버 (Python).

## 개발 환경

- **direnv**: `~/.local/bin`이 PATH에 있어야 합니다. 셸 설정에 `eval "$(direnv hook bash)"` (또는 zsh/fish용 훅)을 추가하세요.
- **가상환경**: 저장소 루트의 `.venv` (direnv가 자동으로 `PATH`에 넣음).
- **pre-commit**: `pre-commit install` 완료됨. 커밋 시 훅 실행.

프로젝트 디렉터리로 들어가면 direnv가 `.envrc`를 로드합니다. 최초 한 번 `direnv allow`가 필요할 수 있습니다.

## 가상환경 재생성 (참고)

시스템에 `python3-venv` 패키지가 없을 때는 [uv](https://github.com/astral-sh/uv)로 `.venv`를 만들 수 있습니다: `uv venv .venv --python 3.12`
